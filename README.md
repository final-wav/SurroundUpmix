# SurroundUpmix

Upmix a stereo track to 5.1, 7.1, 7.1.2, or a Dolby Atmos ADM master. Runs
locally, on Windows/macOS/Linux, with free tools.

Most upmixers widen the stereo image, which smears every instrument into every
speaker. This one separates the track into stems first, then splits each stem
into its direct and its ambient part. The dry, localised sound stays at the
front. Only the diffuse room and reverb wrap around you.

## How it works

A stereo master already has the mix's space baked into two channels. You can't
recover where the artist "meant" each sound to sit, but you can recover the one
thing that decides placement: what is a localised source and what is diffuse
space.

For each stem the engine measures the short-time inter-channel coherence and
splits the signal by it:

```
gamma(f,t) = |<L·conj(R)>| / sqrt(<|L|²>·<|R|²>)     (0 = decorrelated, 1 = coherent)
direct  = X · gamma
ambient = X · sqrt(1 - gamma²)
```

Because gamma² + (1 - gamma²) = 1, the split conserves energy in every
time-frequency tile. A dry, panned, or centred source (gamma near 1) is all
direct. Stereo width and reverb (gamma near 0) is all ambient. Direct goes to
the front and keeps its left/right position; ambient wraps the sides, backs,
and heights.

What it will not do: stereo carries only one real spatial axis, left/right, plus
a depth hint from reverb. The engine reconstructs left/right position and puts
the track's own space around you. It does not invent front/back motion or spin a
static source around the room, because that information isn't in two channels and
faking it sounds like a trick. (One gated exception, off by default: see Binaural
depth.)

## Signal flow

```
stereo track
   | Demucs (stem separation, GPU)
   v
bass  drums  vocals  other   (+ guitar / piano with the 6-stem model)
   | Roformer karaoke split (optional): vocals -> lead + backing
   v
per stem: direct / ambient split
   v
FL FR FC  LFE  BL BR  SL SR  (TFL TFR)
```

The split is the same for every stem. A few per-stem rules handle the things
that are physical, not stylistic:

- **Bass**: front and LFE only, never wraps. Low frequencies aren't localisable,
  and bass in the surrounds just muddies the room.
- **Drums**: kick and snare stay a front image. Only the decorrelated part
  (cymbals, room, overheads) spills a little to the same-side surround.
- **Lead vocal**: anchored to the centre/front. A short-delay doubled vocal (two
  hard-panned takes) is detected with GCC-PHAT and kept fully forward, since
  spreading it would comb-filter and hollow the voice out.
- **Other / guitar / piano**: the dry part stays front, the ambient wraps. How
  far it wraps is decided per song from the stem's measured diffuseness and pan,
  not from an instrument label. A dry guitar stays forward; a reverberant or
  hard-panned one wraps to the sides and back.
- **Backing vocals** (when the karaoke split ran): their own signal, so they wrap
  the rears full-range with no phase risk. A quiet front anchor stops word
  transitions from jumping, and the full vocal sits underneath as a low bed to
  cover split artifacts.
- **Heights (7.1.2)**: fed from the decorrelated ambient of the texture stems,
  high-passed. Height channels want air, not a low-passed copy of the mix.
- **LFE**: built from the low end of bass + kick, band-limited at the crossover,
  not a low-pass of the whole mix.
- **Rear/front balance**: the finished rear field is measured against the front
  and trimmed to a set amount below it, per song, so a busy track and a dry one
  land at the same front-to-back balance.

Output files: 5.1 and 7.1 are 24-bit FLAC. 7.1.2 (10 channels) is a 24-bit WAV
with a correct WAVEFORMATEXTENSIBLE channel mask, because FLAC has no standard
10-channel mask.

## Install

```
pip install -r requirements.txt        # numpy, scipy, soundfile
```

For the full song-to-surround chain:

```
pip install -U demucs                                 # stem separation
# NVIDIA GPU: pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install audio-separator onnxruntime               # optional: lead/backing split
pip install tkinterdnd2                                # optional: drag and drop in the GUI
```

No SoX, no CenterCutCL, no platform lock-in. It runs anywhere Python does.

## Usage

GUI: run `python gui.py`, or double-click `SurroundUpmix-GUI.bat` on Windows.
Drag tracks or folders onto the window, or use Add files / Add folder. Each drop
is sorted into a queue: a folder of Demucs stems becomes one job, any other
folder is scanned for audio, single files become jobs. Pick the output and preset
once, press Start, and the jobs render one after another with a live per-row
status and log. Every control has a tooltip.

Song to surround:

```
python allinone.py "song.flac" --format 7.1.2 --preset immersive --device cuda
```

Existing Demucs stems to surround:

```
python upmix.py "stems/song" --format 5.1 --preset focus
```

Both take `-h` for the full option list.

## Presets

Built around `immersive`, the default, tuned by ear. The others move how much of
the ambient wraps and how firmly the lead vocal is centred.

| Preset | Character |
|--------|-----------|
| `focus` | vocal forward, light wrap |
| `immersive` | balanced (default) |
| `concert` | roomier: more sides, backs, height |
| `envelop` | maximum wrap |

All are phase-coherent: no L−R inversion and no decorrelation delays on the main
wrap, so the front image is never cancelled.

Common options (both CLIs): `--rear-gain`, `--rear-below-front`,
`--vocal-mode auto|spread|forward`, `--backing-gain auto|<dB>`, `--lfe-cross <Hz>`,
`--wav` (force WAV even for 8 channels or fewer).

## Detail recovery

Demucs doesn't reconstruct the mix perfectly. The stems sum to a few dB short of
the master, and what's missing is a quiet broadband layer: air, transients,
breaths, the detail that lives 15 to 30 dB down. The overall tone still looks
right, but that quiet detail is the first thing to go.

Since the full chain has the master, the missing layer is recoverable exactly:
`residual = master - sum of stems`. The engine reinjects it, placed by the same
rule as everything else. The coherent part rebuilds the front, the diffuse part
wraps. A trust check refuses the residual when the stems don't line up with the
master (a lossy or misaligned source), so it only fires on lossless masters and
never injects garbage.

On by default in `allinone.py`; `--recover-detail off` to compare. In the GUI:
Detail recovery. Below the ~9 kHz crossover the residual fills the front; above
it the HF air restore takes over, so the two don't overlap.

## HF air restore

Neural separation loses the top octave, so a split-and-recombine can sound dull.
When the master is available the engine reinjects its highs above ~9 kHz into the
front, where little localisation is lost. This is automatic whenever the original
is present: `allinone.py` always has it, and for `upmix.py` you pass
`--original <master>`.

## Rear decorrelation

On 7.1 and 7.1.2 the backs would otherwise get the same ambient signal as the
sides, which pulls the rear field onto a point between the speakers instead of
around you. A flat-magnitude all-pass gives the backs and heights a decorrelated
copy: same tone, no comb filter, no audible echo, but the field opens up. On by
default; `--decorrelate off` to compare. In the GUI: Rear decorrelation.

## Vocal roles

The karaoke split only labels one output "lead" and the other "backing". On
heavily processed vocals it sometimes gets them backwards and sends the real lead
behind you. The engine checks from the signal, using each part's energy and how
continuously it is active, and swaps them if the model was wrong, or skips the
split entirely if the whole vocal is one wide wash. `--vocal-roles auto|keep|swap`
(default auto). In the GUI: Vocal roles.

## Binaural depth (off by default)

For recordings that carry a real front/back cue: dummy-head binaural, or a track
made with an HRTF / binaural panner. A normal stereo mix pans by level alone, with
no inter-channel delay. Binaural material has a sub-millisecond interaural time
difference, and the detector looks for that and only that (diffuse width and
reverb are excluded, so a reverberant pop mix doesn't count).

The slider leans the diffuse field front/back by the detected amount. The effect
is always multiplied by a measured confidence and hard-bounded, so a normal track
scores about zero and is left untouched no matter where the slider sits.
`--binaural 0-100`. In the GUI: the Binaural depth slider.

## Placement

Model: `--model htdemucs_6s` separates guitar and piano as their own stems, so
they can be placed individually. It is slower, has no `_ft` refinement, and the
piano stem is the weakest, so `htdemucs_ft` (4 stems) is the default. Plain
`htdemucs` is also available.

Auto-place: the engine doesn't guess instrument names. For each texture stem it
measures diffuseness and pan and adapts how far the ambient wraps, per song.
Tunable per preset (`ap_k`, `ap_d0`, `ap_kp`, `ap_p0`), or set `auto_place=False`
for fixed dB.

Manual: force a stem into a zone with `--place-guitar rear`, `--place-piano side`,
`--place-other front`, and so on (`auto|front|side|rear`, default auto). A forced
placement is exempt from the rear/front balance, so it stays at the level you
asked for. In the GUI: the Placement per stem row.

## Dolby Atmos export (ADM BWF)

Writes a bed-based 7.1.2 ADM BWF master (RF64/BW64) instead of a plain FLAC/WAV:
all audio in the ten bed channels, with ITU-R BS.2076 `axml` + `chna` metadata and
a valid Dolby `dbmd` chunk, resampled to 48 kHz. The `dbmd` generator is ported
from Cavern (VoidXH, open source) and checked byte-for-byte against a real Atmos
master, so the file stands on its own with no extra tool needed to author it.

There are two bed orders, because playback and the renderer disagree about the
channel order:

- **Dolby Atmos**: bed in `L R C LFE  Lrs Rrs  Lss Rss  Ltm Rtm` (rears before
  sides), the WAVE convention consumer players and speaker rigs route by. Use this
  for playback. `--adm`.
- **ADM BWF**: bed in the Dolby Atmos Renderer's own order,
  `L R C LFE  Lss Rss  Lrs Rrs  Ltm Rtm` (sides before rears). Use this when
  importing into the Renderer in Studio One so the sides and rears map correctly.
  `--adm --adm-order renderer`.

Both are in the GUI's Output list. `--adm` forces the 7.1.2 layout.

Playback note: consumer players (VLC, MusicBee) play the raw PCM and route the
ear-level 7.1 correctly but not the two height channels, which is a limit of raw
multichannel playback and true of Studio One's own exports too. The heights become
real Atmos height once the master goes through a Dolby chain (Studio One → Dolby
Atmos → E-AC-3/JOC encode). That JOC encode is a proprietary Dolby step, not
something a free tool produces. For height that plays anywhere, use plain 7.1.2
FLAC/WAV.

## Layout

```
surroundupmix/          the engine (a normal Python package)
  layouts.py            speaker layouts + WAVE channel masks
  io.py                 load stems, write FLAC / WAV-extensible
  adm.py                write Dolby Atmos ADM BWF (both bed orders)
  decompose.py          direct/ambient split (the core), pan, coherence
  detect.py             doubled-vocal classifier (GCC-PHAT)
  voice.py              lead/backing role check for the karaoke split
  recover.py            separation-residual detail recovery
  binaural.py           binaural cue detection + front/back depth steer
  dsp.py                filters, gain, mono, decorrelation
  routing.py            stem -> channels (direct front, ambient wrap)
  balance.py            LFE, front/rear auto-balance, normalise
  presets.py            the presets
  inputs.py             expand dropped paths into song/stems jobs (GUI queue)
  engine.py             end-to-end upmix_folder()
upmix.py                CLI: stems folder -> surround
allinone.py             CLI: song -> Demucs -> (split) -> surround
gui.py                  dark Tkinter GUI (drives both CLIs)
SurroundUpmix-GUI.bat   double-click launcher for the GUI (Windows)
split_vocals.py         lead/backing Roformer karaoke split (used by allinone)
tests/test_engine.py    synthetic-signal tests (physics, no audio needed)
```

## Tests

```
python tests/test_engine.py      # or: pytest
```

Synthetic signals prove the behaviour without any real audio: coherent content
goes front and decorrelated content goes rear, bass stays front, the balance hits
its target, rear decorrelation drops the side/back correlation, the vocal-role
guard swaps an inverted split, detail recovery reconstructs the residual and
refuses an untrustworthy one, the binaural detector flags an ITD signal and
ignores a pan-pot one, and the channel counts and WAV mask are correct.

## Licence

Personal use, as-is. The third-party tools (Demucs, the audio-separator / Roformer
models, and the Cavern-derived `dbmd` generator) carry their own licences; get them
from their own sources.
