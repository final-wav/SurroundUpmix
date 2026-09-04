"""End-to-end upmix from a folder of stems to a surround file."""
import os
import numpy as np

from . import presets as _presets
from .balance import auto_balance, build_lfe, normalize
from .detect import classify_vocal_width
from .dsp import rms, highpass, db_to_lin, decorrelate as dsp_decorrelate
from .io import Stereo, load, load_stems, write_surround
from .layouts import LAYOUTS, has_heights
from .routing import spatialize


def _log(verbose, msg):
    if verbose:
        print(msg)


# HF air restore is ALWAYS applied - it's part of the algorithm, not an option.
# Neural separation loses the top octave, so the master's highs are reinjected
# to keep the brilliance. These are fixed, not user knobs.
_AIR_CROSS = 9000.0       # Hz crossover
_AIR_GAIN = 0.0           # dB on the restored air
_AIR_HEIGHTS_DB = -12.0   # a quiet touch overhead


def _load_original(path, sr, n):
    """Load the original master as (n, 2) float32 at `sr`, padded/truncated."""
    try:
        st = load(path)
    except Exception:
        return None
    data = st.data
    if st.sr != sr:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(sr, st.sr)
        data = resample_poly(data, sr // g, st.sr // g, axis=0).astype(np.float32)
    if len(data) < n:
        data = np.concatenate([data, np.zeros((n - len(data), 2), np.float32)])
    return data[:n]


def upmix_folder(stems_folder, fmt="5.1", preset="immersive", out_dir=None,
                 track_label=None, rear_gain=0.0, rear_below_front=None,
                 vocal_mode="auto", backing_gain="auto", backing_below_lead=8.0,
                 lfe_cross=None, norm_level=-1.0, force_wav=False, place=None,
                 overrides=None, split_vocals="off", split_python=None,
                 adm=False, adm_bits=24, adm_order="playback", adm_objects=False,
                 adm_all_objects=False,
                 original=None, decorrelate=True, vocal_roles="auto", recover_detail=True,
                 recover_gain=0.0, binaural_amount=0.0, verbose=True):
    """Upmix a Demucs stems folder. Returns the output path.

    adm=True writes a Dolby-Atmos ADM BWF master (a 7.1.2 bed, 48 kHz) that
    opens directly in the Dolby Atmos Renderer - no channel mapping. It forces
    the 7.1.2 layout and resamples to 48 kHz if needed (Atmos requirement).
    adm_all_objects=True writes a Studio One & Dolby Atmos Renderer compatible
    30-channel All-Objects Master (10 silent 7.1.2 bed carrier channels + 14 speaker
    anchors + 6 dynamic moving 3D objects).
    """
    if adm_all_objects or fmt == "all_objects":
        adm = True
        adm_all_objects = True
    if adm:
        fmt = "7.1.2"   # the Atmos bed is 7.1.2

    stems, sr = load_stems(stems_folder)
    from .overrides import normalize as _norm_ov, zones as _ov_zones
    ov = _norm_ov(overrides)
    place = {**(place or {}), **_ov_zones(ov)}   # overrides' zones win over --place
    # lead/backing split for the stems path (allinone splits on disk already, so
    # a 'backing' stem is present there and this is a no-op)
    if split_vocals != "off":
        from .split import maybe_split_vocals
        stems = maybe_split_vocals(stems, sr, split_vocals, split_python,
                                   lambda m: _log(verbose, m))
    p = _presets.get(preset)
    if rear_below_front is not None:
        p["rear_below_front"] = rear_below_front
    if lfe_cross is not None:
        p["lfe_cross"] = lfe_cross
    p["decorr"] = decorrelate
    p["recover_gain"] = recover_gain
    p["recover_cross"] = _AIR_CROSS       # residual fills BELOW the air crossover
    # detail recovery: use the separation residual if present (and enabled),
    # otherwise ignore it (the HF-air restore below still runs)
    if "residual" in stems and not recover_detail:
        stems.pop("residual", None)

    _log(verbose, "SurroundUpmix v2  |  %s  |  preset: %s%s" % (
        fmt, preset, "" if decorrelate else "  (rear decorrelation OFF)"))
    _log(verbose, "  stems: %s  (%d Hz)" % (", ".join(stems), sr))

    # lead/backing role check: the karaoke split only *labels* lead vs backing;
    # verify from the signal so a stylistically wet lead (Tame-Impala wash) that
    # the model mislabelled as "backing" isn't wrapped behind the listener.
    if "backing" in stems and vocal_roles != "keep":
        from .voice import assess_vocal_roles
        full = stems.get("vocals_full", stems.get("vocals"))
        a = assess_vocal_roles(stems["vocals"], stems["backing"], full, sr,
                               mode=vocal_roles)
        if a["action"] == "swap":
            stems["vocals"], stems["backing"] = stems["backing"], stems["vocals"]
            _log(verbose, "  vocal roles: SWAPPED - %s" % a["reason"])
        elif a["action"] == "nosplit":
            stems["vocals"] = stems.get("vocals_full", stems["vocals"])
            stems.pop("backing", None)
            stems.pop("vocals_full", None)
            _log(verbose, "  vocal roles: no split - %s" % a["reason"])
        else:
            _log(verbose, "  vocal roles: kept (%s)" % a["reason"])

    # vocal width: keep a doubled vocal forward (no spread -> no comb filter)
    vocal_class = "reverb"
    if "vocals" in stems:
        if vocal_mode == "auto":
            vocal_class = classify_vocal_width(stems["vocals"])
            _log(verbose, "  vocal width: %s%s" % (
                vocal_class, "  -> kept forward" if vocal_class == "double" else ""))
        elif vocal_mode == "forward":
            vocal_class = "double"
        else:
            vocal_class = "reverb"

    # adaptive backing level: sit a set amount under the lead, per song
    backing_gain_db = -6.0
    if "backing" in stems:
        if backing_gain != "auto":
            backing_gain_db = float(backing_gain)
        else:
            lr = rms(stems["vocals"].data) if "vocals" in stems else 0.0
            br = rms(stems["backing"].data)
            if lr > 0 and br > 0:
                g = 20 * np.log10(lr) - 20 * np.log10(br) - backing_below_lead
                backing_gain_db = float(max(-12.0, min(6.0, g)))
                _log(verbose, "  adaptive backing level: %.1f dB" % backing_gain_db)

    # binaural depth steer: on genuinely binaural material (real interaural time
    # difference) lean the diffuse field front/back per the detected cue. The
    # effect is confidence * amount and hard-bounded, so a normal (pan-pot) song
    # scores ~0 confidence and is untouched no matter where the slider sits.
    p["binaural_back_db"] = 0.0
    p["binaural_side_db"] = 0.0
    if binaural_amount > 0:
        from .binaural import analyze
        bsig = None
        if original is not None:
            try:
                bsig = load(original)
            except Exception:
                bsig = None
        if bsig is None:
            acc = None
            for k, stv in stems.items():
                if k in ("backing", "vocals_full", "residual"):
                    continue
                d = stv.data
                acc = d.copy() if acc is None else (
                    acc[:min(len(acc), len(d))] + d[:min(len(acc), len(d))])
            if acc is not None:
                bsig = Stereo(acc, sr)
        if bsig is not None:
            ba = analyze(bsig)
            steer = ba["confidence"] * float(binaural_amount)
            back = float(max(-5.0, min(5.0, steer * ba["rear_bias"] * 5.0)))
            p["binaural_back_db"] = back
            p["binaural_side_db"] = -0.5 * back
            _log(verbose, "  binaural: confidence %.2f, ITD %.2f ms, rear_bias %+.2f"
                 " -> back %+.1f dB (amount %.0f%%)"
                 % (ba["confidence"], ba["itd_ms"], ba["rear_bias"], back,
                    binaural_amount * 100))

    # spatialise
    chans = spatialize(stems, fmt, p, sr, vocal_class=vocal_class,
                       backing_gain_db=backing_gain_db, place=place, overrides=ov,
                       discrete_backing=(adm and adm_objects and "backing" in stems))

    # LFE
    lfe = build_lfe(stems, p["lfe_cross"], sr, overrides=ov)
    chans.data["LFE"][:len(lfe)] += lfe[:chans.n]

    # HF air restore: neural separation loses the top octave and the stems no
    # longer sum to the master, so brilliance (~9-21 kHz) goes missing. Reinject
    # the ORIGINAL master's highs: below the crossover keep the spatialised stem
    # signal, above it use the master's own top end (little localisation is lost
    # up there). Added to the FORCED layer so the auto-balance leaves it alone.
    # HF-air restore reinjects the master's front highs above the crossover;
    # the detail residual (if any) filled the band BELOW it, so the two combine
    # into a full-band front reconstruction without doubling the top end.
    if "residual" in stems:
        _log(verbose, "  detail recovery: residual reinjected (< %d Hz)" % int(_AIR_CROSS))
    if original is not None:
        orig = _load_original(original, sr, chans.n)
        if orig is not None:
            ag = db_to_lin(_AIR_GAIN)
            oL, oR = orig[:, 0], orig[:, 1]
            mid = 0.5 * (oL + oR)          # centred content (the lead vocal lives here)
            sd = 0.5 * (oL - oR)           # stereo-only content
            # L/C/R matrix so the CENTRE (muffled vocal) gets its air back too:
            # FC <- master mid highs, FL/FR <- master side highs. In each front
            # channel, swap the attenuated stem highs for the master's own.
            aFC = highpass(mid, _AIR_CROSS, sr) * ag
            aSD = highpass(sd, _AIR_CROSS, sr) * ag
            chans.add("FC", aFC - highpass(chans.total("FC"), _AIR_CROSS, sr),
                      0.0, forced=True)
            chans.add("FL", aSD - highpass(chans.total("FL"), _AIR_CROSS, sr),
                      0.0, forced=True)
            chans.add("FR", -aSD - highpass(chans.total("FR"), _AIR_CROSS, sr),
                      0.0, forced=True)
            # a touch of that air overhead (heights want air, not just treble)
            if has_heights(fmt):
                chans.add("TFL", highpass(oL, _AIR_CROSS, sr) * ag,
                          _AIR_HEIGHTS_DB, forced=True)
                chans.add("TFR", highpass(oR, _AIR_CROSS, sr) * ag,
                          _AIR_HEIGHTS_DB, forced=True)
            _log(verbose, "  HF air restored (L/C/R) from original above %d Hz"
                 % int(_AIR_CROSS))
        else:
            _log(verbose, "  (air restore: could not read original - skipped)")

    # front/rear auto-balance
    info = auto_balance(chans, p["rear_below_front"], rear_gain)
    if info.get("applied"):
        _log(verbose, "  balance: front %.1f / rear %.1f dB -> trim rears %.1f dB"
             % (info["front_db"], info["rear_db"], info["trim"]))

    normalize(chans, norm_level)

    # write
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(stems_folder)),
                                      "Output")
    os.makedirs(out_dir, exist_ok=True)
    track_label = track_label or os.path.basename(os.path.normpath(stems_folder))
    channels = {c: chans.total(c) for c in LAYOUTS[fmt]}

    if adm and adm_all_objects:
        from .adm import write_adm_bwf, ANCHORS_7_1_6
        from .motion import build_dynamic_blocks

        sr_out = sr
        all_obj_defs = []
        all_obj_signals = []

        # 1. Synthesize the 14 Speaker Anchors (7.1.6 layout)
        fl_sig = chans.total("FL")
        fr_sig = chans.total("FR")
        fc_sig = chans.total("FC")
        lfe_sig = chans.total("LFE")
        sl_sig = chans.total("SL")
        sr_sig = chans.total("SR")
        bl_sig = chans.total("BL")
        br_sig = chans.total("BR")
        tfl_base = chans.total("TFL")
        tfr_base = chans.total("TFR")

        # Front Heights: air and presence
        tfl_sig = 0.85 * tfl_base + 0.15 * highpass(fl_sig, 3000.0, sr)
        tfr_sig = 0.85 * tfr_base + 0.15 * highpass(fr_sig, 3000.0, sr)
        # Middle Heights (Top Middle)
        tml_sig = tfl_base.copy()
        tmr_sig = tfr_base.copy()
        # Rear Heights: decorrelated diffuse ceiling canopy + rear wash
        tfl_decorr = dsp_decorrelate(tfl_base, sr)
        tfr_decorr = dsp_decorrelate(tfr_base, sr)
        trl_sig = 0.70 * tfl_decorr + 0.30 * bl_sig
        trr_sig = 0.70 * tfr_decorr + 0.30 * br_sig

        anchor_map = {
            "FL": fl_sig, "FR": fr_sig, "FC": fc_sig, "LFE": lfe_sig,
            "SL": sl_sig, "SR": sr_sig, "BL": bl_sig, "BR": br_sig,
            "TFL": tfl_sig, "TFR": tfr_sig, "TML": tml_sig, "TMR": tmr_sig,
            "TRL": trl_sig, "TRR": trr_sig,
        }

        for ch_key, disp_name, ax, ay, az in ANCHORS_7_1_6:
            sig = anchor_map.get(ch_key, np.zeros(chans.n, dtype=np.float32))
            all_obj_defs.append({
                "name": disp_name,
                "x": ax, "y": ay, "z": az,
            })
            all_obj_signals.append(sig)

        # 2. Dynamic 3D Moving Objects (Channels 15-20)
        n_samples = chans.n

        # Object 15 & 16: Backing Left & Right (Continuous 3D Motion + 360° Orbit)
        bg_sig_l = np.zeros(n_samples, dtype=np.float32)
        bg_sig_r = np.zeros(n_samples, dtype=np.float32)
        if "backing" in stems:
            bg_data = stems["backing"].data
            ov_bg = ov.get("backing", {})
            bg_mul = db_to_lin(backing_gain_db + ov_bg.get("level", 0.0))
            bg_sig_l = bg_data[:, 0] * bg_mul
            bg_sig_r = bg_data[:, 1] * bg_mul
            b_bg_l = build_dynamic_blocks(bg_data, sr, base_x=-0.85, base_y=-0.50,
                                          base_z=0.35, pan_range=0.45, orbit=True)
            b_bg_r = build_dynamic_blocks(bg_data, sr, base_x=0.85, base_y=-0.50,
                                          base_z=0.35, pan_range=0.45, orbit=True)
        else:
            b_bg_l = [(0.0, n_samples / float(sr), -0.85, -0.50, 0.35)]
            b_bg_r = [(0.0, n_samples / float(sr), 0.85, -0.50, 0.35)]

        all_obj_defs.append({"name": "Backing Left", "blocks": b_bg_l})
        all_obj_signals.append(bg_sig_l)
        all_obj_defs.append({"name": "Backing Right", "blocks": b_bg_r})
        all_obj_signals.append(bg_sig_r)

        # Object 17: Lead Vocal (Intimate Whisper Proximity + Pitch Elevation)
        voc_sig = np.zeros(n_samples, dtype=np.float32)
        if "vocals" in stems:
            v_data = stems["vocals"].data
            voc_sig = 0.5 * (v_data[:, 0] + v_data[:, 1])
            b_voc = build_dynamic_blocks(v_data, sr, base_x=0.0, base_y=0.70,
                                         base_z=0.10, pan_range=0.35,
                                         intimacy_proximity=True, pitch_elevation=True)
        else:
            b_voc = [(0.0, n_samples / float(sr), 0.0, 0.70, 0.10)]
        all_obj_defs.append({"name": "Lead Vocal", "blocks": b_voc})
        all_obj_signals.append(voc_sig)

        # Object 18: Guitar / Solo (Dynamic 3D Tracking & Solo Swirl)
        gtr_stem = stems.get("guitar", stems.get("other"))
        gtr_sig = np.zeros(n_samples, dtype=np.float32)
        if gtr_stem is not None:
            g_data = gtr_stem.data
            gtr_sig = g_data[:, 0] * 0.75
            b_gtr = build_dynamic_blocks(g_data, sr, base_x=-0.60, base_y=0.40,
                                         base_z=0.25, pan_range=0.50, orbit=True)
        else:
            b_gtr = [(0.0, n_samples / float(sr), -0.60, 0.40, 0.25)]
        all_obj_defs.append({"name": "Guitar / Solo", "blocks": b_gtr})
        all_obj_signals.append(gtr_sig)

        # Object 19: Piano / Synth / Texture (Stereo Width & Shimmering Height)
        pno_stem = stems.get("piano", stems.get("other"))
        pno_sig = np.zeros(n_samples, dtype=np.float32)
        if pno_stem is not None:
            p_data = pno_stem.data
            pno_sig = (p_data[:, 1] if p_data.shape[1] > 1 else p_data[:, 0]) * 0.75
            b_pno = build_dynamic_blocks(p_data, sr, base_x=0.60, base_y=0.40,
                                         base_z=0.25, pan_range=0.50, pitch_elevation=True)
        else:
            b_pno = [(0.0, n_samples / float(sr), 0.60, 0.40, 0.25)]
        all_obj_defs.append({"name": "Piano / Synth", "blocks": b_pno})
        all_obj_signals.append(pno_sig)

        # Object 20: Ear Candy / FX / Delay (Spatial Riser & 3D Delay Swirl)
        fx_sig = np.zeros(n_samples, dtype=np.float32)
        fx_src = stems.get("residual", stems.get("other", stems.get("vocals")))
        if fx_src is not None:
            f_data = fx_src.data
            if f_data.ndim > 1 and f_data.shape[1] > 1:
                fx_sig = 0.5 * (f_data[:, 0] - f_data[:, 1]) * 0.70
            else:
                fx_sig = f_data.ravel() * 0.70
            b_fx = build_dynamic_blocks(f_data, sr, base_x=0.0, base_y=-0.60,
                                        base_z=0.60, pan_range=0.80, orbit=True,
                                        pitch_elevation=True)
        else:
            b_fx = [(0.0, n_samples / float(sr), 0.0, -0.60, 0.60)]
        all_obj_defs.append({"name": "Ear Candy / FX", "blocks": b_fx})
        all_obj_signals.append(fx_sig)

        if sr != 48000:
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(48000, sr)
            up, down = 48000 // g, sr // g
            all_obj_signals = [resample_poly(s, up, down).astype(np.float32)
                               for s in all_obj_signals]
            _log(verbose, "  resampled %d -> 48000 Hz (Atmos)" % sr)
            sr_out = 48000

        from .adm import BED_RENDERER
        base = os.path.join(out_dir, "%s [%s %s]" % (track_label, "Atmos All-Objects", preset))
        # 10 Bed channels (silent carrier in BED_RENDERER order) + 20 Audio Objects
        # for 100% Studio One & Dolby Atmos Renderer compatibility (total 30 channels).
        out = write_adm_bwf(base, channels={}, sr=sr_out, objects=all_obj_defs,
                            object_signals=all_obj_signals, bits=adm_bits,
                            program_name=track_label, bed=BED_RENDERER, all_objects=False)
        _log(verbose, "  wrote %s (ADM BWF All-Objects, 10-ch 7.1.2 silent bed carrier + 14 anchors + %d dynamic objects = %d ch, %d-bit)"
             % (out, len(all_obj_defs) - 14, 10 + len(all_obj_defs), adm_bits))
        return out

    if adm:
        from .adm import write_adm_bwf, BED, BED_RENDERER
        renderer = (adm_order == "renderer")
        bed = BED_RENDERER if renderer else BED
        sr_out = sr

        objects = []
        object_signals = []
        if adm_objects:
            from .motion import build_dynamic_blocks
            n_samples = chans.n

            # 1 & 2: Backing Left & Right
            if "backing" in stems:
                bg_data = stems["backing"].data
                ov_bg = ov.get("backing", {})
                bg_mul = db_to_lin(backing_gain_db + ov_bg.get("level", 0.0))
                b_left = build_dynamic_blocks(bg_data, sr, base_x=-0.85, base_y=-0.50,
                                              base_z=0.35, pan_range=0.45, orbit=True)
                objects.append({"name": "Backing Left", "blocks": b_left})
                object_signals.append(bg_data[:, 0] * bg_mul)

                b_right = build_dynamic_blocks(bg_data, sr, base_x=0.85, base_y=-0.50,
                                               base_z=0.35, pan_range=0.45, orbit=True)
                objects.append({"name": "Backing Right", "blocks": b_right})
                object_signals.append(bg_data[:, 1] * bg_mul)

            # 3: Lead Vocal (Intimate Whisper Proximity + Pitch Elevation)
            if "vocals" in stems:
                v_data = stems["vocals"].data
                voc_sig = 0.5 * (v_data[:, 0] + v_data[:, 1])
                b_voc = build_dynamic_blocks(v_data, sr, base_x=0.0, base_y=0.70,
                                             base_z=0.10, pan_range=0.35,
                                             intimacy_proximity=True, pitch_elevation=True)
                objects.append({"name": "Lead Vocal", "blocks": b_voc})
                object_signals.append(voc_sig)

            # 4: Guitar / Solo
            gtr_stem = stems.get("guitar", stems.get("other"))
            if gtr_stem is not None:
                g_data = gtr_stem.data
                b_gtr = build_dynamic_blocks(g_data, sr, base_x=-0.60, base_y=0.40,
                                             base_z=0.25, pan_range=0.50, orbit=True)
                objects.append({"name": "Guitar / Solo", "blocks": b_gtr})
                object_signals.append(g_data[:, 0] * 0.75)

            # 5: Piano / Synth / Texture
            pno_stem = stems.get("piano", stems.get("other"))
            if pno_stem is not None:
                p_data = pno_stem.data
                p_sig = (p_data[:, 1] if p_data.shape[1] > 1 else p_data[:, 0]) * 0.75
                b_pno = build_dynamic_blocks(p_data, sr, base_x=0.60, base_y=0.40,
                                             base_z=0.25, pan_range=0.50, pitch_elevation=True)
                objects.append({"name": "Piano / Synth", "blocks": b_pno})
                object_signals.append(p_sig)

            # 6: Ear Candy / FX / Delay
            fx_src = stems.get("residual", stems.get("other", stems.get("vocals")))
            if fx_src is not None:
                f_data = fx_src.data
                if f_data.ndim > 1 and f_data.shape[1] > 1:
                    fx_sig = 0.5 * (f_data[:, 0] - f_data[:, 1]) * 0.70
                else:
                    fx_sig = f_data.ravel() * 0.70
                b_fx = build_dynamic_blocks(f_data, sr, base_x=0.0, base_y=-0.60,
                                            base_z=0.60, pan_range=0.80, orbit=True,
                                            pitch_elevation=True)
                objects.append({"name": "Ear Candy / FX", "blocks": b_fx})
                object_signals.append(fx_sig)

            _log(verbose, "  adm objects: active (%d 3D objects with dynamic automation)"
                 % len(objects))

        if sr != 48000:                       # Atmos requires 48 kHz
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(48000, sr)
            up, down = 48000 // g, sr // g
            channels = {k: resample_poly(v, up, down).astype(np.float32)
                        for k, v in channels.items()}
            object_signals = [resample_poly(s, up, down).astype(np.float32)
                              for s in object_signals]
            _log(verbose, "  resampled %d -> 48000 Hz (Atmos)" % sr)
            sr_out = 48000
        tag = "ADM Renderer" if renderer else "Atmos"
        base = os.path.join(out_dir, "%s [%s %s]" % (track_label, tag, preset))
        out = write_adm_bwf(base, channels, sr_out, objects=objects,
                            object_signals=object_signals,
                            bits=adm_bits, program_name=track_label, bed=bed)
        _log(verbose, "  wrote %s (ADM BWF, 7.1.2 bed%s, %s order, %d-bit)"
             % (out, (" + %d objects" % len(objects)) if objects else "",
                "Renderer" if renderer else "playback", adm_bits))
        return out

    base = os.path.join(out_dir, "%s [%s %s]" % (track_label, fmt, preset))
    out = write_surround(base, channels, fmt, sr, bits=24, force_wav=force_wav,
                         original=original)
    _log(verbose, "  wrote %s (%d ch)" % (out, len(LAYOUTS[fmt])))
    return out
