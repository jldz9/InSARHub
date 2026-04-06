# -*- coding: utf-8 -*-
"""
Weighted linear pair quality classifier.

Takes a FeatureVector dict (from _feature_assembler.py) and returns a
quality score in [0, 1] where 1.0 = high coherence, 0.0 = low coherence.

Two scoring modes
-----------------
score(fv)       — flat weighted linear model (original, always available)
lc_score(fv)    — land-cover-aware branching model (recommended)

The lc_score mode routes through _lc_scorer.py which:
  - Selects branch weights based on WorldCover land cover fractions
  - Applies NDVI transition hard kills for vegetation-dominated AOIs
  - Applies hard kills for water, snow/ice, heavy rain, fire

Design (flat mode)
------------------
Each feature is normalised to [0, 1] via a clamp+scale operation, then
multiplied by its weight.  Missing features (None) are substituted with
a neutral value — the value that contributes 0 to the raw score — so
API failures degrade gracefully without biasing the result.

Weights should sum to ≤ 1.0 so the unclamped raw score stays in [0, 1]
for well-conditioned inputs.  The final clip(0, 1) handles edge cases.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Default weights ───────────────────────────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    # Temporal decorrelation
    "dt_normalized":      0.22,

    # Geometric decorrelation
    "bperp_normalized":   0.08,

    # Snow cover (per-date)
    "snow_cover_frac_d1": 0.10,
    "snow_cover_frac_d2": 0.10,

    # Snow state change between dates
    "delta_snow_cover":   0.08,

    # Freeze-thaw crossing
    "freeze_thaw":        0.08,

    # Vegetation × temporal interaction
    "veg_temporal":       0.08,

    # 7-day precipitation (normalised to 50 mm saturation)
    "precip_7day_d1":     0.04,
    "precip_7day_d2":     0.04,

    # Season crossing penalty (already 0–1 from assembler)
    "season_penalty":     0.06,

    # Land cover: forest + water are intrinsically low coherence
    "lc_forest_fraction": 0.03,
    "lc_water_fraction":  0.02,

    # Annual repeat bonus (subtracts from score → improves quality)
    "is_annual_repeat":  -0.18,
}

# ── Neutral values (substituted when a feature is None) ──────────────────────
# Neutral = contributes 0 to the penalty (not necessarily 0 itself).

_NEUTRAL: dict[str, float] = {
    "dt_normalized":      0.0,
    "bperp_normalized":   0.0,
    "snow_cover_frac_d1": 0.0,
    "snow_cover_frac_d2": 0.0,
    "delta_snow_cover":   0.0,
    "freeze_thaw":        0.0,
    "veg_temporal":       0.0,
    "precip_7day_d1":     0.0,
    "precip_7day_d2":     0.0,
    "season_penalty":     0.0,
    "lc_forest_fraction": 0.0,
    "lc_water_fraction":  0.0,
    "is_annual_repeat":   0.0,
}

# ── Per-feature normalisers: (raw_value) → [0, 1] ────────────────────────────

def _norm_precip_7day(v: float) -> float:
    """Saturate at 50 mm 7-day rolling precipitation."""
    return min(v / 50.0, 1.0)

def _norm_slope_p90(v: float) -> float:
    """Saturate at 60° slope."""
    return min(v / 60.0, 1.0)

# Features that need explicit normalisation before weighting.
# Features already in [0, 1] (fractions, normalised baselines, etc.) pass
# through as-is.
_NORMALISE: dict[str, Any] = {
    "precip_7day_d1": _norm_precip_7day,
    "precip_7day_d2": _norm_precip_7day,
}


def _resolve(fv: dict, key: str) -> float:
    """Return the normalised feature value, substituting neutral if None."""
    raw = fv.get(key)
    if raw is None:
        return _NEUTRAL.get(key, 0.0)
    norm_fn = _NORMALISE.get(key)
    val = norm_fn(float(raw)) if norm_fn else float(raw)
    return max(0.0, min(1.0, val))


# ── Main scoring function ─────────────────────────────────────────────────────

def score(
    fv: dict,
    weights: dict[str, float] | None = None,
) -> tuple[float, dict]:
    """Compute quality score and per-feature contribution breakdown.

    Parameters
    ----------
    fv      : FeatureVector dict from FeatureAssembler.assemble()
    weights : optional override for DEFAULT_WEIGHTS

    Returns
    -------
    score   : float in [0, 1] where 1 = good coherence, 0 = bad coherence
    factors : dict mapping feature name → {value, contribution}
              plus meta keys: dt_days, bperp_diff, ndvi_source, snow_source
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS

    raw = 0.0
    contributions: dict[str, float] = {}

    for feat, weight in w.items():
        val = _resolve(fv, feat)
        contribution = weight * val
        raw += contribution
        contributions[feat] = round(contribution, 4)

    # Invert: weights accumulate penalties (higher = worse), flip so 1 = good
    final = round(max(0.0, min(1.0, 1.0 - raw)), 4)

    factors: dict = {
        # Raw score components
        "contributions": contributions,
        # Readable summary fields (kept for backward compat with old factors schema)
        "dt_days":      fv.get("dt_days", 0),
        "bperp_diff":   fv.get("bperp_diff", 0.0),
        "dt":           fv.get("dt_normalized", 0.0),
        "bperp":        fv.get("bperp_normalized", 0.0),
        "snow_cover_d1": fv.get("snow_cover_frac_d1"),
        "snow_cover_d2": fv.get("snow_cover_frac_d2"),
        "delta_snow":   fv.get("delta_snow_cover"),
        "freeze_thaw":  fv.get("freeze_thaw", 0),
        "season":       fv.get("season_penalty", 0.0),
        "veg":          fv.get("veg_temporal"),
        "ndvi_source":  fv.get("ndvi_source", "climatology"),
        "snow_source":  fv.get("snow_source", "none"),
        "slope_p90":    fv.get("slope_p90"),
        "lc_forest":    fv.get("lc_forest_fraction"),
        "lc_water":     fv.get("lc_water_fraction"),
        "score":        final,
    }

    return final, factors


# ── Land-cover-aware scoring (recommended) ────────────────────────────────────

def lc_score(fv: dict) -> tuple[float, dict]:
    """Land-cover-aware quality score using branching weights and hard kills.

    Delegates to _lc_scorer.score().  Falls back to flat score() on any error
    so a missing WorldCover fetch never breaks the pipeline.

    Returns
    -------
    score   : float in [0, 1] — 1.0 = high coherence, 0.0 = low coherence
    factors : dict with branch info, hard_kills, per-branch scores
    """
    try:
        from insarhub.utils.pair_quality._lc_scorer import score as _lc
        return _lc(fv)
    except Exception as exc:
        logger.warning("LC scorer failed (%s), falling back to flat scorer", exc)
        return score(fv)
