"""Per-instrument overrides: one normalized structure the GUI and the CLI share.

The GUI writes these as a small JSON file and passes it with --overrides; the CLI
can take the same file. Each stem's entry sits on TOP of the preset (the preset
is the template, these are the fine-tuning). All fields are optional.

Schema per stem (bass/drums/vocals/other/guitar/piano/backing):
    zone    "auto"|"front"|"side"|"rear"   placement (auto = the engine decides)
    level   dB                              gain trim on the whole stem
    mute    bool                            drop the stem entirely
    spread  "auto"|0..100                   how far its ambient wraps (phase 2)
    center  0..100                          vocals: how much folds to FC (phase 2)
    lfe     0..100                          bass/drums: how much feeds the LFE (phase 2)

Unknown stems and out-of-range values are dropped, so a hand-written or stale
file can never crash a render - it just contributes nothing.
"""
import json

STEMS = ("bass", "drums", "vocals", "other", "guitar", "piano", "backing")
ZONES = ("auto", "front", "side", "rear")


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def normalize(data):
    """Return {stem: {field: value}} with only valid, non-default fields kept."""
    out = {}
    for stem, ov in (data or {}).items():
        if stem not in STEMS or not isinstance(ov, dict):
            continue
        d = {}
        z = str(ov.get("zone", "auto")).lower()
        if z in ZONES and z != "auto":
            d["zone"] = z
        lv = _clamp(ov.get("level", 0.0), -24.0, 24.0, 0.0)
        if lv:
            d["level"] = lv
        if bool(ov.get("mute", False)):
            d["mute"] = True
        if "spread" in ov and str(ov["spread"]).lower() != "auto":
            d["spread"] = _clamp(ov["spread"], 0.0, 100.0, None)
        if "center" in ov:
            d["center"] = _clamp(ov["center"], 0.0, 100.0, None)
        if "lfe" in ov:
            d["lfe"] = _clamp(ov["lfe"], 0.0, 100.0, None)
        d = {k: v for k, v in d.items() if v is not None}
        if d:
            out[stem] = d
    return out


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return normalize(json.load(f))


def zones(overrides):
    """The placement map {stem: zone} implied by the overrides (for --place)."""
    return {s: ov["zone"] for s, ov in overrides.items() if "zone" in ov}
