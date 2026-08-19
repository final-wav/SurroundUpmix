# SurroundUpmix v2

Turn a stereo song into a genuine **5.1 / 7.1 / 7.1.2** surround mix — locally, cross-platform, with free tools.

v2 is a from-scratch rewrite in **Python**. Instead of spreading the stereo image wider (which smears every instrument into every speaker), or deciding placement purely by which *stem* a sound falls into, it splits **every stem into its direct and its ambient part** and places each where it physically belongs: the dry, localised source stays anchored at the front; only the decorrelated room/reverb wraps around you.

> This is an independent re-implementation of the "SurroundUpmix" concept. The v1 PowerShell engine (SoX + CenterCutCL) lives in [`legacy/`](legacy/); v2 supersedes it.

---

## The core idea: direct vs. ambient, not front vs. back-by-category

A real Atmos album "fits perfectly" because the engineer places the **dry** source and then adds room/reverb **designed for the speaker layout**. A stereo master has all of that already baked into two channels — you can't un-bake the artist's intent, but you *can* recover the one thing that matters most: **what is a localised source and what is diffuse space.**

For every stem the engine computes the short-time **inter-channel coherence** and splits it:

```
gamma(f,t) = |<L·conj(R)>| / sqrt(<|L|²>·<|R|²>)     (0 = decorrelated, 1 = coherent)

direct  = X · gamma            → front image, keeps its L↔R position
ambient = X · sqrt(1 - gamma²) → the surround field (sides / backs / heights)
```

`gamma² + (1-gamma²) = 1`, so the split conserves energy per time-frequency tile. A dry, panned or centred source (γ→1) is **all direct**; independent stereo width / reverb (γ→0) is **all ambient**. This is exactly the decision a mixing engineer makes in reverse — made measurable, and applied per song.

**What it can and can't do** (honestly): stereo carries only *one* real spatial axis — left↔right — plus an implicit depth hint (reverb). So the engine **reconstructs real left→right position and movement** and puts the song's own space around you. It does **not** invent front↔back trajectories or "circle" a static source — that information simply isn't in two channels, and faking it sounds like a gimmick.

---

## Signal flow

```
stereo song
   │  Demucs (AI stem separation, GPU)
   ▼
bass · drums · vocals · other  (+ guitar/piano with a 6-stem model)
   │  Roformer karaoke split (optional): vocals → lead + backing
   ▼
per stem:  direct/ambient decomposition
   ▼
FL FR FC  LFE  BL BR  SL SR  (TFL TFR)
```

Per-stem modulation (the few things that really *are* category-specific):

- **Bass** → front + LFE only (never wraps).
- **Drums** → front body + a gentle same-side wash of the decorrelated cymbals/room.
- **Vocals (lead)** → direct anchored to the centre/front; a short-delay **doubled** vocal is detected and kept fully forward (spreading it would comb-filter).
- **Other / guitar / piano** → direct up front, **ambient** wraps sides + backs + heights.
- **Backing vocals** (from the karaoke split) → their own object behind you, with the full clean vocal laid underneath as a quiet **bed** to mask split artifacts.
- **Heights (7.1.2)** are fed from the **decorrelated ambient** specifically (air, not just treble) — what real height channels want.
- **LFE** is derived from the real low end of **bass + kick**, not a low-pass of the whole mix.
- **Rear/front auto-balance** trims the whole rear field to a fixed amount under the front, per song, so every track sits consistently.

5.1 and 7.1 are written as 24-bit **FLAC**; 7.1.2 (10 ch) as a 24-bit **WAV** with a correct `WAVEFORMATEXTENSIBLE` channel mask so players route the ten channels right.

---

## Install

```bash
pip install -r requirements.txt          # numpy, scipy, soundfile — the engine
```

For the full chain (`allinone.py`):

```bash
pip install -U demucs                     # AI stem separation
# NVIDIA GPU: pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install audio-separator onnxruntime   # optional: lead/backing karaoke split
pip install tkinterdnd2                    # optional: drag & drop onto the GUI
```

No SoX, no CenterCutCL, no platform lock-in — it runs anywhere Python does.

---

## Usage

**GUI (dark mode, batch queue)** — double-click `SurroundUpmix-GUI.bat` on Windows, or run `python gui.py` anywhere. **Drag songs or whole folders onto the window** (or use *Add files / Add folder*) — each drop is auto-classified into a queue: a folder of Demucs stems becomes one job, any other folder is scanned for audio and each track becomes a job, and files become jobs. Set format/preset once (applied to every job), hit **Start queue**, and the jobs render one after another with a live per-row status and log. Tkinter ships with Python; OS drag & drop additionally needs `pip install tkinterdnd2` (without it, the Add buttons still fill the queue). Every option has a **hover tooltip**, and a **live description under the options** explains the selected preset — the help is built into the window.

**Full chain — song → surround:**
```bash
python allinone.py "song.flac" --format 7.1.2 --preset immersive --device cuda
```

**Just upmix an existing Demucs stems folder:**
```bash
python upmix.py "stems/song" --format 5.1 --preset focus
```

Run either CLI with `-h` for every option.

---

## Presets

Anchored on **`immersive`** — the balanced character the project was hand-tuned to by ear — with the family derived by how much of the *ambient* component wraps and how firmly the lead vocal is centred:

| Preset | Character |
|--------|-----------|
| `focus` | vocal-forward, tasteful wrap |
| `immersive` | balanced all-rounder *(default)* |
| `concert` | roomy / live |
| `envelop` | maximum wrap |

All are phase-coherent: no L−R inversion, no decorrelation delays — only the genuinely decorrelated ambient wraps, so the pristine front image is never cancelled.

Key options (both CLIs): `--rear-gain` (taste offset on the rear field), `--rear-below-front` (override the balance target), `--vocal-mode auto|spread|forward`, `--backing-gain auto|<dB>`, `--lfe-cross <Hz>`, `--wav` (force WAV even for ≤8 ch).

---

## Placement — auto, and by hand

**Model:** `--model htdemucs_6s` (or the GUI *Demucs model* box) separates **guitar** and **piano** as their own stems, so they can be placed individually. It is slower, has no `_ft` refinement, and the `piano` stem is the weakest link, so **`htdemucs_ft` (4-stem) stays the default.**

**Auto-place (per song, no labels):** Demucs never tells you a sound is a "trumpet" — that's baked into `other` — and "should be behind me" is artistic intent, not something in the signal. So the engine doesn't guess instruments; it reacts to *measurable* per-stem behaviour. For each texture stem it measures its **diffuseness** (how decorrelated it is) and its **pan**, and adapts how far its ambient wraps: a dry, centred guitar/piano is held forward automatically, while a reverberant or clearly panned part wraps further to the sides/back — song by song. Tunable per preset (`ap_k`, `ap_d0`, `ap_kp`, `ap_p0`); set `auto_place=False` to fall back to fixed preset dB.

**Manual overrides (taste decisions a metric can't make):** force any stem into a zone — `--place-guitar rear`, `--place-piano side`, `--place-other front`, etc. (`auto | front | side | rear`, default `auto`). In the GUI: the **Placement per stem** row. `auto` uses the auto-place layer; the others put that whole stem (its direct source *and* its ambient) into the chosen zone.

---

## Layout

```
surroundupmix/          # the engine (a normal Python package)
  layouts.py            # speaker layouts + WAVE channel masks
  io.py                 # load stems, write FLAC / WAV-extensible
  decompose.py          # direct/ambient split (the core), pan, coherence
  detect.py             # doubled-vocal classifier (GCC-PHAT)
  dsp.py                # filters, gain, mono
  routing.py            # stem → channels (direct front, ambient wrap)
  balance.py            # LFE, front/rear auto-balance, normalise
  presets.py            # the presets
  inputs.py             # expand dropped paths → song/stems jobs (GUI queue)
  engine.py             # end-to-end upmix_folder()
upmix.py                # CLI: stems folder → surround
allinone.py             # CLI: song → Demucs → (split) → surround
gui.py                  # dark-mode Tkinter GUI (drives both CLIs)
SurroundUpmix-GUI.bat   # double-click launcher for the GUI (Windows)
split_vocals.py         # lead/backing Roformer karaoke split (used by allinone)
tests/test_engine.py    # synthetic-signal tests (physics, no audio needed)
legacy/                 # the v1 PowerShell engine (SoX + CenterCutCL)
```

Run the tests with `python tests/test_engine.py` (or `pytest`). They prove the physics — coherent → front, decorrelated → rear, bass stays front, the auto-balance hits its target, channel counts and the WAV mask are correct — without needing any real audio.

---

## Licence

The scripts in this repo are provided as-is for personal use. The third-party tools (Demucs, audio-separator / Roformer models, and — for `legacy/` — SoX, CenterCutCL, ffmpeg) each carry their own licences; get them from their own sources.
