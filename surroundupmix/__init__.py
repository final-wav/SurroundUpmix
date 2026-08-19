"""SurroundUpmix v2 - stereo-stem to 5.1 / 7.1 / 7.1.2 surround upmixer.

Direct/ambient decomposition places the coherent (direct) part of every stem
at the front and wraps only the decorrelated (ambient) part around the
listener - reconstructing the song's own space instead of smearing it.
"""
from .engine import upmix_folder
from .presets import PRESETS, DEFAULT_PRESET
from .layouts import LAYOUTS

__all__ = ["upmix_folder", "PRESETS", "DEFAULT_PRESET", "LAYOUTS"]
__version__ = "2.0.0"
