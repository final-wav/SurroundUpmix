"""Lead / backing role check for the karaoke split.

The Roformer karaoke model just *labels* one output "Vocals" (lead) and the
other "Instrumental" (backing) - it never proves the labelled lead is really
the main vocal. On stylistically wet/filtered productions (a Tame-Impala-style
wash) the model often pushes the real, reverberant lead into the "backing"
bucket and promotes a thin dry element to "lead". Trusting the labels then puts
the actual singer *behind* the listener.

This module decides from the SIGNAL, not the label, which of the two split
outputs is the primary vocal, and returns one of:

  * keep    - the model's split is fine, use it as-is
  * swap    - the model inverted them; the "backing" is the real lead
  * nosplit - neither part is a clean dry lead (the whole vocal is a wide/wet
              wash); don't split at all, keep the full vocal up front

Two cheap, explainable measures (no ML):
  energy share - the primary vocal carries most of the vocal energy over a song
  coverage     - the primary is active in most vocal-present frames (verses AND
                 choruses); backing is intermittent (choruses/harmonies only)
plus the broadband inter-channel coherence of the FULL vocal as a "how wide/wet
is this production" cue (≈1 dry & centred, ≈0 a wide stereo wash).
"""
import numpy as np

from .decompose import broadband_coherence
from .dsp import mono


def _activity(x, sr, frame_ms=50, floor_db=-45.0):
    """Boolean per-frame activity mask (frame RMS above a floor under the
    track's own peak)."""
    hop = max(1, int(sr * frame_ms / 1000.0))
    nf = len(x) // hop
    if nf < 1:
        return np.zeros(0, dtype=bool)
    fr = np.asarray(x[:nf * hop], dtype=np.float64).reshape(nf, hop)
    e = np.sqrt(np.mean(fr * fr, axis=1) + 1e-12)
    thr = (e.max() + 1e-12) * (10.0 ** (floor_db / 20.0))
    return e > thr


def assess_vocal_roles(lead, backing, full, sr, mode="auto"):
    """Return a dict: {action: keep|swap|nosplit, reason, <metrics>}.
    `lead`, `backing`, `full` are Stereo signals. `mode` forces the outcome
    for 'keep'/'swap'; 'auto' (default) decides from the signal."""
    Lm, Bm, Fm = mono(lead), mono(backing), mono(full)
    eL = float(np.mean(Lm * Lm))
    eB = float(np.mean(Bm * Bm))
    share_lead = eL / (eL + eB + 1e-12)

    aL, aB, aF = (_activity(Lm, sr), _activity(Bm, sr), _activity(Fm, sr))
    m = min(len(aL), len(aB), len(aF))
    if m > 0:
        aL, aB, aF = aL[:m], aB[:m], aF[:m]
        vp = float(aF.sum()) + 1e-9              # vocal-present frames
        cov_lead = float((aL & aF).sum()) / vp
        cov_back = float((aB & aF).sum()) / vp
    else:
        cov_lead = cov_back = 0.0
    coh_full = broadband_coherence(full)

    metrics = dict(share_lead=share_lead, cov_lead=cov_lead,
                   cov_back=cov_back, coh_full=coh_full)

    if mode == "keep":
        return dict(action="keep", reason="forced keep", **metrics)
    if mode == "swap":
        return dict(action="swap", reason="forced swap", **metrics)

    # primary-ness score: mostly energy, partly temporal coverage
    score_lead = 0.6 * share_lead + 0.4 * cov_lead
    score_back = 0.6 * (1.0 - share_lead) + 0.4 * cov_back

    # A) inversion - the "backing" is clearly the primary vocal
    if score_back > score_lead + 0.12 and cov_back > cov_lead:
        return dict(action="swap",
                    reason="backing carries the primary vocal (energy+coverage)",
                    **metrics)
    # B) wide/wet wash - no clean dry lead to separate from a sparse backing
    if cov_lead < 0.45 and share_lead < 0.62 and coh_full < 0.35:
        return dict(action="nosplit",
                    reason="vocal is a wide/wet wash - no clean lead/backing split",
                    **metrics)
    return dict(action="keep", reason="model split kept", **metrics)
