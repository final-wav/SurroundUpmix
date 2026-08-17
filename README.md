# SurroundUpmix

Turn a stereo song into a genuine **5.1 / 7.1 / 7.1.2** surround mix — locally, on Windows, with free tools.

Instead of just spreading the stereo image wider (which smears every instrument into every speaker), this pipeline **separates the song into its parts first** and then places each one where it belongs: lead vocal locked to the front/centre, bass to the front + LFE, drums across the front and sides, instruments and their ambience around you, and — with the karaoke split — **backing vocals as their own object behind you**.

It is phase-coherent (no comb-filter mush), it detects short-delay-doubled vocals and keeps them forward, and it auto-balances the whole rear field per song so every track sits consistently.

> This is an independent re-implementation of the "SurroundUpmix" concept. The original forum script was not available, so the engine here was written from scratch and then extended.

---

## What it does

```
stereo song
   │  Demucs (AI stem separation, GPU)
   ▼
bass · drums · vocals · other
   │  Roformer karaoke split (optional)
   ▼
vocals → lead + backing
   │  SoX + CenterCutCL routing
   ▼
FL FR FC LFE  BL BR  SL SR  (TFL TFR)
```

- **Lead vocal** → centre / front phantom, anchored (never smeared).
- **Backing vocals** → placed behind you (real source separation = phase-safe), with a full-vocal "bed" underneath to mask separation artifacts.
- **Bass** → front + LFE only (LFE is derived from the real low end of the full mix).
- **Drums** → front body + a gentle cymbal wash to the sides.
- **Other / guitar / piano** → the texture that wraps the sides / backs / height.
- **Rear/front auto-balance** keeps the surround field a fixed amount under the front on *every* song.

FLAC caps at 8 channels, so **5.1 and 7.1 are written as FLAC, 7.1.2 as WAV**.

---

## Files

| File | What it is |
|------|------------|
| `SurroundUpmix.ps1` | the upmix **engine** — takes a folder of stems, writes the surround file |
| `SurroundUpmix-AllInOne.ps1` | the **full chain** — song file → Demucs → (split) → upmix |
| `SurroundUpmix-GUI.ps1` | dark **WinForms GUI** exposing every option |
| `SurroundUpmix.bat` | double-click launcher for the GUI |
| `RunBatch.ps1` | process **every song in a folder** (resumable) |
| `detect_vocal.py` | detects a short-delay-doubled vocal (so it isn't smeared) |
| `split_vocals.py` | lead/backing split via a Mel-Band Roformer karaoke model |

---

## Requirements (all free)

The scripts look for their tools in a `bin\` folder next to them. `bin\` is **not** in this repo (binaries + licences + size) — download them once:

1. **SoX** 14.4.2 — <https://sourceforge.net/projects/sox/files/sox/14.4.2/> — unzip the *whole* folder to `bin\SoX\` (it needs its DLLs, not just `sox.exe`).
2. **CenterCutCL** — from moitah.net (use the [Wayback Machine](https://web.archive.org/web/*/http://www.moitah.net/download/latest/Center_Cut_GUI.zip) if the site is down) — put `CenterCutCL.exe` in `bin\`.
3. **ffmpeg** *(optional, only for `-LoudnessMatch`)* — <https://www.gyan.dev/ffmpeg/builds/> — `bin\ffmpeg\ffmpeg.exe`.
4. **Demucs** (AI separation): `py -3.10 -m pip install -U demucs`. For NVIDIA GPU first install CUDA PyTorch:
   `py -3.10 -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121`
5. **Lead/backing splitter** *(optional)* — an isolated venv with `audio-separator`:
   ```
   py -3.10 -m venv bin\splitter_venv
   bin\splitter_venv\Scripts\python -m pip install audio-separator onnxruntime torch torchaudio --index-url https://download.pytorch.org/whl/cu121
   ```
   The Roformer karaoke model (~900 MB) downloads on first use into `bin\splitter_models\`.

Folder layout:
```
SurroundUpmix\
├─ SurroundUpmix.ps1 / -AllInOne.ps1 / -GUI.ps1 / RunBatch.ps1
├─ detect_vocal.py / split_vocals.py / SurroundUpmix.bat
└─ bin\
   ├─ CenterCutCL.exe
   ├─ SoX\  (whole folder)
   ├─ ffmpeg\ffmpeg.exe          (optional)
   ├─ splitter_venv\             (optional)
   └─ splitter_models\           (optional, auto-filled)
```

---

## Usage

**GUI** — double-click `SurroundUpmix.bat`, pick a song, choose the options, **Start**.

**Full chain (CLI):**
```powershell
.\SurroundUpmix-AllInOne.ps1 "D:\Music\song.flac" -OutputFormat 7.1.2 -Preset Immersive -Device cuda -SplitVocals on
```

**A whole folder:**
```powershell
.\RunBatch.ps1 "D:\Music\album" -OutputFormat 7.1.2 -Preset Immersive
```

**Just upmix existing Demucs stems:**
```powershell
.\SurroundUpmix.ps1 "D:\stems\song" -OutputFormat 5.1 -Preset Focus
```

---

## Presets

`None · Music · Movie · Anime · PLIIx` (classic) and the enveloping family:

- **Focus** – vocal-forward, tasteful wrap
- **Immersive** – balanced all-rounder *(default)*
- **Envelop** – maximum wrap
- **Concert** – roomy / live
- **WideStage** – wide front + sides

All enveloping presets are phase-coherent: only the direct same-side **high band** wraps (no decorrelation delays, no L−R inversion, no echo), so the full-range front image is never cancelled.

## Key options

| Option | Meaning |
|--------|---------|
| `-BackingMode` | `blend` (backing + full-vocal bed, default) · `choir` (backing + reverb glue) · `halo` (reverb only) · `rear` (dry) |
| `-BackingGain` | `auto` = balance backing vs the lead per song, or a fixed dB |
| `-RearBelowFront` | keep the whole rear field this many dB under the front (default 16) |
| `-RearGain` | your taste offset on top of the balance |
| `-VocalMode` | `auto` detects a doubled vocal and keeps it forward; `forward` / `spread` to force |
| `-SplitVocals` | `on` / `off` the lead/backing karaoke split |

---

## How the clever bits work

- **Vocal doubling detection** (`detect_vocal.py`) — a short-delay double comb-filters into a "many voices" mess if you spread it. GCC-PHAT + the side-signal short/long-lag energy ratio tells a structured double from diffuse reverb; a doubled vocal stays fully forward.
- **Lead/backing split** — Demucs can't split a vocal; a Mel-Band Roformer *karaoke* model can. The lead becomes the vocal (front), the backing becomes its own object.
- **Backing artifacts** — separation is never perfect, so a hard front/back split "jumps" at word boundaries. `blend` lays the full clean vocal underneath as a bed to fill holes and mask transitions.
- **Rear/front auto-balance** — every song carries a different amount of rear material, so the engine measures front vs rear energy and trims the rears to a fixed target — consistent from track to track.

---

## Licence

The scripts in this repo are provided as-is for personal use. The third-party tools (SoX, CenterCutCL, ffmpeg, Demucs, audio-separator / Roformer models) each carry their own licences — get them from their own sources.
