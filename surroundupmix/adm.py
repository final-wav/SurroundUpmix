"""Write a Dolby-Atmos-compatible ADM BWF (RF64/BW64) master.

Modelled byte-for-byte on a real Dolby Atmos ADM master (a 7.1.2 bed + N
objects, room-centric Cartesian positions, ITU-R BS.2076 / EBU ebuCore_2016
`axml` + `chna`). Such a file opens directly in the Dolby Atmos Renderer:
- play on a 7.1.2 rig, or
- export a binaural re-render for headphones,
without any manual channel mapping.

The Renderer's 7.1.2 bed order is  L R C LFE  Lss Rss  Lrs Rrs  Lts Rts
(side surrounds *before* rear surrounds). Our engine's WAVE order is
FL FR FC LFE BL BR SL SR TFL TFR, so the bed is remapped here.

This module writes ONLY the open ADM BWF. Encoding that master to E-AC-3 JOC
(Dolby Digital Plus + Atmos) is a proprietary Dolby step (Dolby Atmos
Renderer / Dolby encoder) and is intentionally out of scope.
"""
import struct
import numpy as np

# Bed: (our channel name, speakerLabel, channelFormatName, X, Y, Z)
# Z is None for floor channels (no <position Z> element, matching the reference).
# Standard Dolby 7.1.2 bed order (what Studio One and the Dolby Atmos Renderer
# expect): L R C LFE  Lss Rss (sides)  Lrs Rrs (rears)  Ltm Rtm (top middle).
# Our engine's SL/SR -> side surrounds, BL/BR -> rear surrounds, TFL/TFR -> tops.
BED = [
    ("FL",  "RC_L",   "RoomCentricLeft",             -1.0,  1.0, None),
    ("FR",  "RC_R",   "RoomCentricRight",             1.0,  1.0, None),
    ("FC",  "RC_C",   "RoomCentricCenter",            0.0,  1.0, None),
    ("LFE", "RC_LFE", "RoomCentricLFE",              -1.0,  1.0, -1.0),
    ("SL",  "RC_Lss", "RoomCentricLeftSideSurround", -1.0,  0.0, None),
    ("SR",  "RC_Rss", "RoomCentricRightSideSurround", 1.0,  0.0, None),
    ("BL",  "RC_Lrs", "RoomCentricLeftRearSurround", -1.0, -1.0, None),
    ("BR",  "RC_Rrs", "RoomCentricRightRearSurround", 1.0, -1.0, None),
    ("TFL", "RC_Ltm", "RoomCentricLeftTopMiddle",    -1.0,  0.0,  1.0),
    ("TFR", "RC_Rtm", "RoomCentricRightTopMiddle",    1.0,  0.0,  1.0),
]


def _t(sec):
    """Seconds -> ADM time string HH:MM:SS.sssss."""
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    return "%02d:%02d:%08.5f" % (h, m, s)


def _pcm_bytes(interleaved, bits):
    x = np.clip(interleaved, -1.0, 1.0)
    if bits == 16:
        ints = np.round(x * 32767.0).astype("<i2")
        return ints.reshape(-1).tobytes()
    # 24-bit little-endian
    ints = np.round(x * 8388607.0).astype("<i4").reshape(-1)
    b = ints.view(np.uint8).reshape(-1, 4)[:, :3]
    return b.tobytes()


def _dbmd_checksum(payload):
    return (-(len(payload) + sum(payload))) & 0xFF


def make_dbmd(channel_count, creation0="Created with SurroundUpmix",
              creation1="SurroundUpmix v2"):
    """Build a Dolby audio Metadata (`dbmd`) chunk body for a file with
    `channel_count` PCM tracks. Ported from Cavern's DolbyMetadata (VoidXH,
    open source) and verified byte-exact against a real Dolby Atmos master:
    a version, then Dolby-Digital-Plus, Dolby-Atmos and object-metadata
    segments, each followed by a two's-complement checksum. The object segment
    carries the track count (all tracks are objects at the metadata level)."""
    # Dolby Digital Plus metadata segment (id 7, length 96)
    ddp = bytearray(96)
    ddp[1] = 0x47    # programInfo: acmod + LFE
    ddp[5] = 0x60    # dialnormInfo: protected + original
    ddp[8] = 0x24    # downmix (-3 dB) high byte
    ddp[9] = 0x24    # downmix low byte
    # Dolby Atmos metadata segment (id 9, length 248)
    atmos = bytearray(248)
    c0 = creation0.encode("ascii", "ignore")[:32]
    atmos[0:len(c0)] = c0
    c1 = creation1.encode("ascii", "ignore")[:32]
    atmos[32:32 + len(c1)] = c1
    cwv = [0, 0, 0]
    dot = 0
    for ch in creation1:                       # created-with version from digits
        if ch.isdigit():
            cwv[dot] = cwv[dot] * 10 + int(ch)
        elif cwv[dot] != 0:
            dot += 1
            if dot == 3:
                break
    atmos[96], atmos[97], atmos[98] = cwv
    atmos[103] = 0x03
    atmos[106] = 0x01
    atmos[111] = 0x22    # frame-rate code
    atmos[112] = 0xFF
    # object metadata segment (id 10, length 5 + 262 + count)
    om = bytearray(5 + 262 + channel_count)
    struct.pack_into("<I", om, 0, 0xF8726FBD)  # preamble
    om[4] = channel_count & 0xFF
    for i in range(len(om) - channel_count, len(om)):
        om[i] = 0x84
    out = bytearray(struct.pack("<I", 0x01000006))   # version
    for sid, payload in ((7, bytes(ddp)), (9, bytes(atmos)), (10, bytes(om))):
        out.append(sid)
        out += struct.pack("<H", len(payload))
        out += payload
        out.append(_dbmd_checksum(payload))
    out += b"\x00\x00"                                # terminator
    return bytes(out)


def _axml(n_frames, sr, bits, objects, program_name):
    total = _t(n_frames / float(sr))
    A = []
    A.append('<?xml version="1.0" encoding="utf-8"?>')
    A.append('<ebuCoreMain xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             'xmlns="urn:ebu:metadata-schema:ebuCore_2016" '
             'xsi:schemaLocation="urn:ebu:metadata-schema:ebuCore_2016 ebucore.xsd" '
             'lang="en"><coreMetadata><format><audioFormatExtended>')

    # ---- audioProgramme ----
    A.append('<audioProgramme audioProgrammeID="APR_1001" audioProgrammeName="%s" '
             'start="%s" end="%s">' % (program_name, _t(0), total))
    A.append('<audioContentIDRef>ACO_1001</audioContentIDRef>')
    if objects:
        A.append('<audioContentIDRef>ACO_1002</audioContentIDRef>')
    A.append('</audioProgramme>')

    # ---- audioContent: Bed ----
    A.append('<audioContent audioContentID="ACO_1001" audioContentName="Bed">'
             '<audioObjectIDRef>AO_1001</audioObjectIDRef>'
             '<dialogue mixedContentKind="0">2</dialogue></audioContent>')
    # ---- audioContent: Objects ----
    if objects:
        A.append('<audioContent audioContentID="ACO_1002" audioContentName="Objects">')
        for i in range(len(objects)):
            A.append('<audioObjectIDRef>AO_%04x</audioObjectIDRef>' % (0x100b + i))
        A.append('<dialogue mixedContentKind="0">2</dialogue></audioContent>')

    # ---- audioObject: Bed ----
    A.append('<audioObject audioObjectID="AO_1001" audioObjectName="Bed" '
             'start="%s" duration="%s">' % (_t(0), total))
    A.append('<audioPackFormatIDRef>AP_00011001</audioPackFormatIDRef>')
    for k in range(10):
        A.append('<audioTrackUIDRef>ATU_%08x</audioTrackUIDRef>' % (k + 1))
    A.append('</audioObject>')
    # ---- audioObject: each object ----
    for i, ob in enumerate(objects or []):
        uid = 11 + i
        A.append('<audioObject audioObjectID="AO_%04x" audioObjectName="%s" '
                 'start="%s" duration="%s">'
                 % (0x100b + i, ob.get("name", "Audio Object %d" % (i + 1)),
                    _t(0), total))
        A.append('<audioPackFormatIDRef>AP_0003%04x</audioPackFormatIDRef>' % (0x1001 + i))
        A.append('<audioTrackUIDRef>ATU_%08x</audioTrackUIDRef>' % uid)
        A.append('</audioObject>')

    # ---- bed audioPackFormat ----
    A.append('<audioPackFormat audioPackFormatID="AP_00011001" '
             'audioPackFormatName="AtmosCustomBed" typeDefinition="DirectSpeakers" '
             'typeLabel="0001">')
    for k in range(10):
        A.append('<audioChannelFormatIDRef>AC_0001%04x</audioChannelFormatIDRef>' % (0x1001 + k))
    A.append('</audioPackFormat>')
    # ---- object audioPackFormats ----
    for i, ob in enumerate(objects or []):
        A.append('<audioPackFormat audioPackFormatID="AP_0003%04x" '
                 'audioPackFormatName="Obj_%d" typeDefinition="Objects" typeLabel="0003">'
                 '<audioChannelFormatIDRef>AC_0003%04x</audioChannelFormatIDRef>'
                 '</audioPackFormat>' % (0x1001 + i, i + 1, 0x1001 + i))

    # ---- bed audioChannelFormats ----
    for k, (nm, label, cfname, x, y, z) in enumerate(BED):
        A.append('<audioChannelFormat audioChannelFormatID="AC_0001%04x" '
                 'audioChannelFormatName="%s" typeDefinition="DirectSpeakers" '
                 'typeLabel="0001">' % (0x1001 + k, cfname))
        A.append('<audioBlockFormat audioBlockFormatID="AB_0001%04x_00000001">'
                 % (0x1001 + k))
        A.append('<cartesian>1</cartesian>')
        A.append('<position coordinate="X">%.10f</position>' % x)
        A.append('<position coordinate="Y">%.10f</position>' % y)
        if z is not None:
            A.append('<position coordinate="Z">%.10f</position>' % z)
        A.append('<speakerLabel>%s</speakerLabel>' % label)
        A.append('</audioBlockFormat></audioChannelFormat>')
    # ---- object audioChannelFormats (with position automation) ----
    for i, ob in enumerate(objects or []):
        cid = 0x1001 + i
        A.append('<audioChannelFormat audioChannelFormatID="AC_0003%04x" '
                 'audioChannelFormatName="Obj_%d" typeDefinition="Objects" '
                 'typeLabel="0003">' % (cid, i + 1))
        blocks = ob.get("blocks") or [(0.0, n_frames / float(sr),
                                       ob.get("x", 0.0), ob.get("y", 1.0),
                                       ob.get("z", 0.0))]
        for bi, (rt, dur, x, y, z) in enumerate(blocks):
            A.append('<audioBlockFormat audioBlockFormatID="AB_0003%04x_%08x" '
                     'rtime="%s" duration="%s">' % (cid, bi + 1, _t(rt), _t(dur)))
            A.append('<cartesian>1</cartesian>')
            A.append('<position coordinate="X">%.10f</position>' % x)
            A.append('<position coordinate="Y">%.10f</position>' % y)
            A.append('<position coordinate="Z">%.10f</position>' % z)
            A.append('<jumpPosition interpolationLength="%.5f">1</jumpPosition>'
                     % (0.0 if bi == 0 else dur))
            A.append('</audioBlockFormat>')
        A.append('</audioChannelFormat>')

    # ---- audioStreamFormat + audioTrackFormat (bed + objects) ----
    def stream_track(fmt_hex, name):
        s = ('<audioStreamFormat audioStreamFormatID="AS_%s" '
             'audioStreamFormatName="PCM_%s" formatDefinition="PCM" formatLabel="0001">'
             '<audioChannelFormatIDRef>AC_%s</audioChannelFormatIDRef>'
             '<audioPackFormatIDRef>AP_%s</audioPackFormatIDRef>'
             '<audioTrackFormatIDRef>AT_%s_01</audioTrackFormatIDRef>'
             '</audioStreamFormat>')
        t = ('<audioTrackFormat audioTrackFormatID="AT_%s_01" '
             'audioTrackFormatName="PCM_%s" formatDefinition="PCM" formatLabel="0001">'
             '<audioStreamFormatIDRef>AS_%s</audioStreamFormatIDRef>'
             '</audioTrackFormat>')
        return s, t
    for k in range(10):
        hexid = "0001%04x" % (0x1001 + k)
        pack = "00011001"
        s = ('<audioStreamFormat audioStreamFormatID="AS_%s" audioStreamFormatName="PCM_bed_%d" '
             'formatDefinition="PCM" formatLabel="0001">'
             '<audioChannelFormatIDRef>AC_%s</audioChannelFormatIDRef>'
             '<audioPackFormatIDRef>AP_%s</audioPackFormatIDRef>'
             '<audioTrackFormatIDRef>AT_%s_01</audioTrackFormatIDRef>'
             '</audioStreamFormat>' % (hexid, k + 1, hexid, pack, hexid))
        t = ('<audioTrackFormat audioTrackFormatID="AT_%s_01" audioTrackFormatName="PCM_bed_%d" '
             'formatDefinition="PCM" formatLabel="0001">'
             '<audioStreamFormatIDRef>AS_%s</audioStreamFormatIDRef>'
             '</audioTrackFormat>' % (hexid, k + 1, hexid))
        A.append(s); A.append(t)
    for i in range(len(objects or [])):
        hexid = "0003%04x" % (0x1001 + i)
        s = ('<audioStreamFormat audioStreamFormatID="AS_%s" audioStreamFormatName="PCM_obj_%d" '
             'formatDefinition="PCM" formatLabel="0001">'
             '<audioChannelFormatIDRef>AC_%s</audioChannelFormatIDRef>'
             '<audioPackFormatIDRef>AP_%s</audioPackFormatIDRef>'
             '<audioTrackFormatIDRef>AT_%s_01</audioTrackFormatIDRef>'
             '</audioStreamFormat>' % (hexid, i + 1, hexid, hexid, hexid))
        t = ('<audioTrackFormat audioTrackFormatID="AT_%s_01" audioTrackFormatName="PCM_obj_%d" '
             'formatDefinition="PCM" formatLabel="0001">'
             '<audioStreamFormatIDRef>AS_%s</audioStreamFormatIDRef>'
             '</audioTrackFormat>' % (hexid, i + 1, hexid))
        A.append(s); A.append(t)

    # ---- audioTrackUIDs ----
    for k in range(10):
        A.append('<audioTrackUID UID="ATU_%08x" bitDepth="%d" sampleRate="%d">'
                 '<audioTrackFormatIDRef>AT_0001%04x_01</audioTrackFormatIDRef>'
                 '<audioPackFormatIDRef>AP_00011001</audioPackFormatIDRef>'
                 '</audioTrackUID>' % (k + 1, bits, sr, 0x1001 + k))
    for i in range(len(objects or [])):
        uid = 11 + i
        A.append('<audioTrackUID UID="ATU_%08x" bitDepth="%d" sampleRate="%d">'
                 '<audioTrackFormatIDRef>AT_0003%04x_01</audioTrackFormatIDRef>'
                 '<audioPackFormatIDRef>AP_0003%04x</audioPackFormatIDRef>'
                 '</audioTrackUID>' % (uid, bits, sr, 0x1001 + i, 0x1001 + i))

    A.append('</audioFormatExtended></format></coreMetadata></ebuCoreMain>')
    return "".join(A).encode("utf-8")


def _chna(n_objects):
    n_tracks = 10 + n_objects
    body = struct.pack("<HH", n_tracks, n_tracks)
    def rec(idx, uid, trackfmt, packfmt):
        return (struct.pack("<H", idx)
                + uid.encode().ljust(12, b"\x00")
                + trackfmt.encode().ljust(14, b"\x00")
                + packfmt.encode().ljust(11, b"\x00")
                + b"\x00")
    for k in range(10):
        body += rec(k + 1, "ATU_%08x" % (k + 1),
                    "AT_0001%04x_01" % (0x1001 + k), "AP_00011001")
    for i in range(n_objects):
        body += rec(11 + i, "ATU_%08x" % (11 + i),
                    "AT_0003%04x_01" % (0x1001 + i), "AP_0003%04x" % (0x1001 + i))
    return body


def _chunk(cid, body):
    out = cid + struct.pack("<I", len(body)) + body
    if len(body) % 2:
        out += b"\x00"
    return out


def write_adm_bwf(path, channels, sr, objects=None, bits=24,
                  program_name="SurroundUpmix", object_signals=None, dbmd=None):
    """Write an ADM BWF master.

    channels : {name: mono np.array} for the 10 bed channels (our WAVE names
               FL FR FC LFE BL BR SL SR TFL TFR). Missing -> silence.
    objects  : optional list of dicts describing objects, each:
                 {"name": str, "blocks": [(rtime_s, dur_s, x, y, z), ...]}
               with a matching mono signal in object_signals[i].
    object_signals : list of mono np.arrays, one per object (same order).
    Returns the written path (forces .wav).
    """
    if not path.lower().endswith(".wav"):
        path = path + ".wav"
    objects = objects or []
    object_signals = object_signals or []

    n = 0
    for v in channels.values():
        if v is not None:
            n = max(n, len(v))
    for s in object_signals:
        if s is not None:
            n = max(n, len(s))

    # Interleave: 10 bed channels in DOLBY bed order, then objects.
    cols = []
    for nm, *_ in BED:
        v = channels.get(nm)
        if v is None or len(v) == 0:
            v = np.zeros(n, dtype=np.float32)
        elif len(v) < n:
            v = np.concatenate([v, np.zeros(n - len(v), dtype=np.float32)])
        cols.append(v[:n])
    for s in object_signals:
        if s is None or len(s) == 0:
            s = np.zeros(n, dtype=np.float32)
        elif len(s) < n:
            s = np.concatenate([s, np.zeros(n - len(s), dtype=np.float32)])
        cols.append(s[:n])
    interleaved = np.stack(cols, axis=1).astype(np.float32)

    ch = interleaved.shape[1]
    block_align = ch * bits // 8
    data = _pcm_bytes(interleaved, bits)
    data_size = len(data)

    if dbmd is None:                        # generate a valid Dolby metadata chunk
        dbmd = make_dbmd(ch)

    fmt_body = struct.pack("<HHIIHH", 0x0001, ch, sr, sr * block_align,
                           block_align, bits)
    axml = _axml(n, sr, bits, objects, program_name)
    chna = _chna(len(objects))

    fmt_chunk = _chunk(b"fmt ", fmt_body)
    axml_chunk = _chunk(b"axml", axml)
    chna_chunk = _chunk(b"chna", chna)
    dbmd_chunk = _chunk(b"dbmd", dbmd) if dbmd else b""
    data_pad = b"\x00" if (data_size % 2) else b""

    # ds64: riffSize, dataSize, sampleCount, tableLength=0
    after_ds64 = (len(fmt_chunk) + 8 + data_size + len(data_pad)
                  + len(axml_chunk) + len(chna_chunk) + len(dbmd_chunk))
    ds64_body = struct.pack("<QQQI", 0, data_size, n, 0)   # riffSize patched below
    ds64_chunk = b"ds64" + struct.pack("<I", len(ds64_body)) + ds64_body
    riff_size = 4 + len(ds64_chunk) + after_ds64            # 'WAVE' + chunks
    ds64_body = struct.pack("<QQQI", riff_size, data_size, n, 0)
    ds64_chunk = b"ds64" + struct.pack("<I", len(ds64_body)) + ds64_body

    with open(path, "wb") as f:
        f.write(b"RF64")
        f.write(struct.pack("<I", 0xFFFFFFFF))
        f.write(b"WAVE")
        f.write(ds64_chunk)
        f.write(fmt_chunk)
        f.write(b"data")
        f.write(struct.pack("<I", 0xFFFFFFFF))   # real size in ds64
        f.write(data)
        f.write(data_pad)
        f.write(axml_chunk)
        f.write(chna_chunk)
        if dbmd_chunk:
            f.write(dbmd_chunk)
    return path
