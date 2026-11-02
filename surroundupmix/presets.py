"""Presets for the v2 (direct/ambient) engine.

Every knob is a dB gain unless noted. The presets are anchored on 'immersive'
- the character the project was hand-tuned to by ear - and the rest of the
family is derived by moving how much of the *ambient* (decorrelated) component
wraps to the sides / backs / heights, and how strongly the lead vocal is
anchored to the centre.

Knobs:
  vocal_center : 0..1  fraction of the lead vocal's DIRECT part folded to FC
  vocal_front  : dB    gain on the lead vocal's direct front image
  amb_side     : dB    texture ambient -> side speakers
  amb_back     : dB    texture ambient -> back speakers
  amb_height   : dB    texture ambient -> height speakers
  drum_side    : dB    drum ambient (cymbal/room) -> sides only
  voc_side     : dB    lead-vocal ambient (its reverb) -> sides
  height_hp    : Hz    high-pass on the height feed (air, not body)
  lateral_arc  : 0..1  how far a panned direct source extends toward its side
  rear_below_front : dB  auto-balance target (rear field this far under front)
  lfe_cross    : Hz    LFE low-pass crossover
"""

PRESETS = {
    # vocal-forward, tasteful wrap - dialogue/lead sits firmly up front
    "focus": dict(
        vocal_center=0.65, vocal_front=0.0,
        amb_side=-8, amb_back=-14, amb_height=-14,
        drum_side=-12, voc_side=-14,
        height_hp=3500, lateral_arc=0.15,
        rear_below_front=24, lfe_cross=115,
    ),
    # balanced all-rounder - THE reference character (Standard DSP reference standard)
    "immersive": dict(
        vocal_center=0.50, vocal_front=0.0,
        amb_side=-6, amb_back=-12, amb_height=-12,
        drum_side=-9, voc_side=-12,
        height_hp=3000, lateral_arc=0.25,
        rear_below_front=22, lfe_cross=120,
    ),
    # roomy / live - a touch more height and back, slightly looser front
    "concert": dict(
        vocal_center=0.45, vocal_front=0.0,
        amb_side=-4, amb_back=-10, amb_height=-10,
        drum_side=-7, voc_side=-10,
        height_hp=2800, lateral_arc=0.30,
        rear_below_front=20, lfe_cross=120,
    ),
    # maximum wrap - deep immersion
    "envelop": dict(
        vocal_center=0.40, vocal_front=0.0,
        amb_side=-3, amb_back=-8, amb_height=-8,
        drum_side=-6, voc_side=-8,
        height_hp=2500, lateral_arc=0.35,
        rear_below_front=18, lfe_cross=125,
    ),
}

DEFAULT_PRESET = "immersive"


def get(name):
    key = (name or DEFAULT_PRESET).lower()
    if key not in PRESETS:
        raise ValueError("unknown preset %r (have: %s)"
                         % (name, ", ".join(PRESETS)))
    return dict(PRESETS[key])
