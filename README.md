# SurroundUpmix

Local, stems-based spatial audio upmixer for **5.1**, **7.1**, **7.1.2**, and **Dolby Atmos ADM BWF** masters. Runs locally on Windows, macOS, and Linux using open-source audio toolchains.

---

## Overview

Traditional upmixers widen a stereo track by applying phase tricks or generic delays, which smears instruments indiscriminately across all speakers.

**SurroundUpmix** takes a stems-first approach:
1. It separates the input track into discrete stems (vocals, drums, bass, other, and optionally guitar/piano) using neural source separation (Demucs / Roformer).
2. For each stem, it splits the signal into **direct (localised)** and **ambient (diffuse)** components using short-time inter-channel coherence analysis.
3. Dry, localised elements remain firmly anchored in the front stage, while diffuse reflections, room reverb, and spatial textures envelop the listener across side, rear, and height channels.
4. For immersive production, it exports broadcast-ready **Dolby Atmos ADM BWF** (RF64/BW64) masters with ITU-R BS.2076 metadata, complete with fixed speaker anchors and dynamic 3D audio object automation.

---

## How It Works

### 1. Energy-Conserving Coherence Decomposition

A stereo master contains both direct instrumentation and diffuse acoustic space mixed into two channels. For each stem, the engine computes the short-time complex inter-channel coherence:

$$\gamma(f, t) = \frac{|\langle L \cdot R^* \rangle|}{\sqrt{\langle |L|^2 \rangle \cdot \langle |R|^2 \rangle}} \quad (0 \le \gamma \le 1)$$

The signal is decomposed into direct and ambient components:

$$\text{Direct} = X \cdot \gamma$$
$$\text{Ambient} = X \cdot \sqrt{1 - \gamma^2}$$

Because $\gamma^2 + (1 - \gamma^2) = 1$, total energy is strictly conserved in every time-frequency bin. Dry, centred, or cleanly panned sources ($\gamma \to 1$) are directed to the front stage, preserving their original stereo placement. Diffuse reverb and ambient spread ($\gamma \to 0$) wrap smoothly around the listener.

### 2. Stem-Specific Acoustic Routing

Routing rules are grounded in room acoustics rather than artificial panning:

- **Bass**: Routed exclusively to the front and LFE. Low frequencies are non-directional; routing bass into surround channels causes room muddiness and phase cancellation.
- **Drums**: Kick and snare remain anchored at the front. Decorrelated drum room ambience, overhead spill, and cymbals gently wrap to the surround channels.
- **Lead Vocal**: Anchored to the centre/front. Short-delay doubled vocals (hard-panned double takes) are detected via GCC-PHAT and kept forward to prevent comb-filtering.
- **Backing Vocals**: When isolated via the karaoke split, backing harmonies wrap into the rear and side channels full-range with dedicated front anchors to ensure natural vocal balance.
- **Instruments / Textures**: Dry guitars, keyboards, and synths remain in the front stage, while their natural room reflections and stereo tails wrap to the sides and rear according to measured diffuseness.
- **LFE Channel**: Synthesized from the sub-bass of bass and kick drums, band-limited via crossover (high-passed at 20 Hz), and calibrated with the standard ITU-R BR.1384 reproduction offset.
- **Heights (7.1.2 / 7.1.6)**: Fed by high-passed, decorrelated ambient reflections to create an open overhead canopy rather than a muffled clone of the mix.

---

## Dolby Atmos & ADM BWF Masters

SurroundUpmix authors compliant **Dolby Atmos ADM BWF** (RF64 / BW64) masters containing ITU-R BS.2076 `axml` and `chna` metadata, ready for direct import into the Dolby Atmos Renderer, Studio One, Pro Tools, Logic Pro, and DaVinci Resolve.

### Export Modes

1. **Standard Playback Bed (`--adm`)**:
   - 7.1.2 Bed in standard WAVE playback order (`L R C LFE Lrs Rrs Lss Rss Ltm Rtm`).
   - Includes a Dolby Audio Metadata (`dbmd`) chunk.
   - Ideal for direct playback on hardware AVRs and consumer players.

2. **Renderer Bed (`--adm --adm-order renderer`)**:
   - 7.1.2 Bed in canonical Dolby Atmos Renderer order (`L R C LFE Lss Rss Lrs Rrs Ltm Rtm`).
   - Intended for DAW imports to ensure immediate, correct routing between side and rear surrounds.

3. **Discrete 3D Audio Objects (`--adm --adm-objects`)**:
   - Isolates dynamic stems (lead vocal, backing vocals, guitar, synth, sound effects) and exports them as discrete 3D Audio Objects with Cartesian coordinates instead of folding them into the bed.

4. **All-Objects 7.1.6 Master (`--all-objects`)**:
   - Modern 30-channel master architecture:
     - **Channels 1–10**: 7.1.2 Bed Carrier with silent PCM (`0.0`). Provides standard DAW compatibility on ADM import, preventing *"File does not contain beds"* errors.
     - **Channels 11–24**: 14 Fixed Speaker Anchors configured in a 7.1.6 room layout:
       - Front Left, Center, Front Right: $(\pm 1.0, 1.0, 0.0)$, $(0.0, 1.0, 0.0)$
       - LFE / Subwoofer: $(0.0, 0.8, 0.0)$
       - Side Surrounds L/R: $(\pm 1.0, 0.0, 0.0)$
       - Rear Surrounds L/R: $(\pm 1.0, -1.0, 0.0)$
       - Top Front L/R: $(\pm 1.0, 1.0, 1.0)$
       - Top Middle L/R: $(\pm 1.0, 0.0, 1.0)$
       - Top Rear L/R: $(\pm 1.0, -1.0, 1.0)$
     - **Channels 25–30**: 6 Dynamic Moving 3D Audio Objects with automated real-time spatial trajectories:
       - **Lead Vocal**: Intimate whisper proximity tracking and pitch-to-elevation dynamics.
       - **Backing Left & Right**: Continuous 360° orbit around the listener.
       - **Guitar / Solo**: Short-time energy pan tracking across the stereo plane.
       - **Piano / Synth**: Dynamic width expansion with shimmering ceiling elevation.
       - **Ear Candy / FX**: Spatial risers and 3D delay swirls.

---

## Presets

| Preset | Spatial Character |
| :--- | :--- |
| `focus` | Vocal-forward staging with subtle ambient surround wrap. |
| `immersive` | **Default**. Balanced acoustic staging; natural room envelopment. |
| `concert` | Expansive soundstage with pronounced side, rear, and height reflection. |
| `envelop` | Maximum surround wrapping for ambient, electronic, and cinematic material. |

All presets maintain strict phase coherence: no artificial out-of-phase inversions ($L - R$) and no destructive delays on front-stage material.

---

## Advanced Signal Processing

- **Detail Recovery**: Neural separation can cause subtle broadband loss (15–30 dB down). The engine computes the residual signal ($\text{master} - \sum \text{stems}$) and re-injects high-fidelity micro-detail into the mix. A trust verification check automatically disables injection on lossy or misaligned sources.
- **HF Air Restoration**: Restores high-frequency air above ~9 kHz from the original master into the front soundstage, eliminating the dullness associated with neural stem recombination.
- **Rear Field Decorrelation**: Applies flat-magnitude all-pass decorrelation filters to rear and height channels, preventing acoustic collapse between side and rear speakers without comb-filtering.
- **Vocal Role Classifier**: Analyzes signal energy distribution and duty cycles to verify lead vs. backing vocal roles, automatically resolving model classification errors.
- **Binaural Cue Detection**: Detects interaural time differences (ITD) in dummy-head recordings or binaural masters and maps them to front/rear depth positioning (`--binaural`).
- **Auto-Balance**: Evaluates integrated loudness across front and rear fields, applying gentle trim adjustments to achieve consistent target balance across diverse tracks.

---

## Installation

### Requirements
- Python 3.9+
- Recommended: CUDA-compatible NVIDIA GPU for accelerated stem separation

```bash
# Clone the repository
git clone https://github.com/final-wav/SurroundUpmix.git
cd SurroundUpmix

# Install core dependencies
pip install -r requirements.txt

# Install Demucs for stem separation
pip install -U demucs

# Optional: Karaoke vocal separation & drag-and-drop UI support
pip install audio-separator onnxruntime tkinterdnd2
```

> **CUDA Acceleration**: For PyTorch with NVIDIA GPU acceleration, install the appropriate wheel from [pytorch.org](https://pytorch.org/):
> ```bash
> pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
> ```

---

## Usage

### Graphical Interface (GUI)
Launch the graphical interface:
```bash
python gui.py
# On Windows: double-click SurroundUpmix-GUI.bat
```
- **Drag & Drop**: Drop audio files or folders directly into the batch queue.
- **Format Selection**: Choose between 5.1, 7.1, 7.1.2, Dolby Atmos (Playback), ADM BWF (Renderer), or 30-channel All-Objects.
- **Queue Manager**: Reorder, pause, or remove jobs with live per-track logging.

### Command Line (CLI)

#### End-to-End Processing (`allinone.py`)
Processes an audio file from separation through final surround export:
```bash
# 7.1.2 Immersive WAV
python allinone.py "song.flac" --format 7.1.2 --preset immersive --device cuda

# 30-channel Dolby Atmos All-Objects Master
python allinone.py "song.flac" --all-objects --adm-objects --device cuda
```

#### Stems-Only Processing (`upmix.py`)
Upmixes an existing directory of Demucs stems:
```bash
# 5.1 Surround FLAC
python upmix.py "stems/song/" --format 5.1 --preset focus

# Dolby Atmos ADM BWF for DAW Import
python upmix.py "stems/song/" --adm --adm-order renderer --adm-objects
```

---

## Codebase Architecture

```
SurroundUpmix/
├── surroundupmix/          # Core processing package
│   ├── layouts.py          # Speaker channel configurations & WAVE masks
│   ├── io.py               # Multichannel FLAC / RF64 / BW64 file I/O
│   ├── adm.py              # ITU-R BS.2076 ADM BWF & Dolby metadata generation
│   ├── motion.py           # Short-time pan tracking, 360° orbit & 3D trajectories
│   ├── decompose.py        # Coherence-based direct/ambient decomposition
│   ├── detect.py           # Doubled-vocal detection (GCC-PHAT)
│   ├── voice.py            # Lead / backing vocal role verification
│   ├── recover.py          # Stem separation residual detail recovery
│   ├── binaural.py         # ITD / HRTF spatial cue detection
│   ├── dsp.py              # Filters, all-pass decorrelators, loudness utilities
│   ├── routing.py          # Channel matrices and stem allocation
│   ├── balance.py          # Front/rear balance & true-peak normalisation
│   ├── presets.py          # Spatial presets configuration
│   └── engine.py           # Pipeline orchestration (upmix_folder)
├── allinone.py             # CLI: Song -> Demucs -> Split -> Surround
├── upmix.py                # CLI: Stems -> Surround
├── split_vocals.py         # Lead / backing karaoke separator
├── demo_generator.py       # Standalone spatial Atmos demo master generator
├── gui.py                  # Tkinter multi-threaded batch GUI
└── tests/
    └── test_engine.py      # Synthetic audio physics test suite
```

---

## Verification & Tests

The test suite validates physical properties, channel masks, and metadata without requiring external audio assets:
```bash
python tests/test_engine.py
```

Tests verify:
- Energy conservation across direct and ambient splits.
- RF64/BW64 container integrity and ITU-R BS.2076 `axml`/`chna` chunk structure.
- Front/rear auto-balance calibration.
- Decorrelation phase linearity and correlation reduction.
- GCC-PHAT doubled vocal detection and vocal role guards.
- Dynamic 3D motion tracking and coordinate boundaries.

---

## License

Personal and production use, provided as-is. Third-party components (Demucs, Roformer models, Cavern metadata structures) are governed by their respective licenses.
