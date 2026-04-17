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

def _norm_precip_3day(v: float) -> float:
    """Linear normalisation for 3-day accumulated precipitation.

    Saturates at 30 mm (returns 1.0).

    Reference points:
       5 mm  →  0.17   (drizzle, minor soil moisture change)
      10 mm  →  0.33   (moderate shower, noticeable on bare soil)
      20 mm  →  0.67   (significant, soil approaching saturation)
      30 mm  →  1.00   (full decorrelation over bare soil / agriculture)
    """
    return min(float(v) / 30.0, 1.0)

_NORMALISE: dict[str, Any] = {
    "precip_3day_d1": _norm_precip_3day,
    "precip_3day_d2": _norm_precip_3day,
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


# ── Coherence-based scoring (recommended when S1 global dataset is available) ──

def _lc_features_available(fv: dict) -> bool:
    """Return True if enough NDVI/landcover features exist for a meaningful lc_score."""
    return any(
        fv.get(k) is not None
        for k in ("lc_dominant_class", "lc_forest_fraction", "ndvi_max", "ndvi_d1")
    )


def coherence_score(fv: dict) -> tuple[float, dict]:
    """Quality score with three-tier fallback chain.

    Tier 1 — AWS S3 coherence (``coherence_source == "s3"``)
        Coherence itself becomes a penalty component (weight 0.50).  Reference
        point γ = 0.50 (urban/bare-soil 12-day average in the Kellndorfer global
        dataset).  Pairs at or above 0.50 incur no coherence penalty.  Environmental penalties (snow 0.25, precip 0.10+0.10,
        freeze-thaw 0.05) are added on top.  Score is returned as an integer
        in [0, 100] to distinguish it from coherence values (0–1).

    Tier 2 — NDVI / land-cover scoring (``coherence_source == "failed"``)
        S3 was unreachable.  Delegates to lc_score() which uses WorldCover
        land-cover fractions, NDVI transitions, and weather features.

    Tier 3 — Climatology safeline
        Both S3 and NDVI/landcover data are unavailable.  Uses the hardcoded
        latitude-band coherence table (``coherence_climatology`` in fv) as the
        base signal, then applies the same environmental penalties as Tier 1.

    Returns
    -------
    score   : int in [0, 100]  (100 = excellent, 0 = unusable)
    factors : dict with coherence breakdown + penalty contributions
    """
    coh    = fv.get("coherence_expected")
    source = fv.get("coherence_source", "s3")

    # ── Tier 2: S3 failed → NDVI/landcover ───────────────────────────────
    if coh is None or source == "failed":
        if _lc_features_available(fv):
            logger.debug("S1 S3 unavailable — scoring via NDVI/landcover (lc_score)")
            return lc_score(fv)
        # ── Tier 3: NDVI/landcover also unavailable → climatology ─────────
        coh = fv.get("coherence_climatology")
        if coh is None:
            logger.debug("All coherence sources unavailable — flat score fallback")
            return score(fv)
        logger.debug("NDVI/landcover unavailable — scoring via climatology safeline")

    base    = float(coh)
    rho_inf = float(fv.get("coherence_rho_inf") or 0.0)

    # ── Raw feature values ────────────────────────────────────────────────────
    snow_frac_d1   = fv.get("snow_cover_frac_d1") or 0.0
    snow_frac_d2   = fv.get("snow_cover_frac_d2") or 0.0
    delta_snow_raw = fv.get("delta_snow_cover")    or 0.0
    temp_d1        = fv.get("temp_max_d1")
    temp_d2        = fv.get("temp_max_d2")

    # ── Hard kill: wet snow → score = 0 ──────────────────────────────────────
    # C-band penetration depth collapses to 5–10 cm at ≥1% liquid water
    # content (Strozzi et al. 1999).  Dry snow (temp < 0) penetrates ~20 m
    # and does NOT kill coherence — handled by the soft snow penalty below.
    wet_snow = (
        (temp_d1 is not None and temp_d1 > 0 and snow_frac_d1 > 0.30) or
        (temp_d2 is not None and temp_d2 > 0 and snow_frac_d2 > 0.30)
    )
    hard_kill = "wet_snow" if wet_snow else None

    if hard_kill:
        coh_abs      = 0.0
        final        = 0
        coh_penalty  = snow_penalty = pr_penalty_d1 = pr_penalty_d2 = ft_penalty = 0.0
    else:
        # ── Coherence penalty (weight 0.40) ───────────────────────────────────
        # Reference point: γ = 0.50 — pairs at or above this incur no coherence
        # penalty (urban/bare soil 12-day pairs typically land here).  Below 0.50
        # the penalty rises linearly to 1.0 at γ = 0 (water / tropical forest).
        _COH_REF = 0.50
        coh_penalty = max(0.0, (_COH_REF - base) / _COH_REF)   # 0 at γ≥0.50, 1.0 at γ=0

        # ── Snow penalty (weight 0.25) ────────────────────────────────────────
        # Use the worst single snow metric to avoid double-counting dates.
        snow_metric  = max(snow_frac_d1, snow_frac_d2, delta_snow_raw)
        snow_penalty = min(snow_metric, 1.0)

        # ── Precipitation penalty (weight 0.80 per date) ─────────────────────
        # Linear, saturates at 30 mm over 3 days.
        pr_penalty_d1 = _resolve(fv, "precip_3day_d1")
        pr_penalty_d2 = _resolve(fv, "precip_3day_d2")

        # ── Freeze-thaw penalty (weight 0.05) ────────────────────────────────
        ft        = float(fv.get("freeze_thaw") or 0.0)
        ft_penalty = ft * 0.05

        # ── Weighted sum → 0-100 score ────────────────────────────────────────
        total_penalty = (
            0.40 * coh_penalty   +
            0.25 * snow_penalty  +
            0.75 * pr_penalty_d1 +
            0.75 * pr_penalty_d2 +
            0.05 * ft_penalty
        )
        final = max(0, min(100, round((1.0 - total_penalty) * 100)))

        # Absolute coherence from S1 decay model, floored at PS fraction.
        # Kept for MintPy minimum-coherence threshold comparison.
        coh_abs = round(max(rho_inf, base), 4)

    factors: dict = {
        # Coherence source
        "coherence_source":      source,
        "coherence_expected":    base,
        "coherence_climatology": fv.get("coherence_climatology"),
        "coherence_same_season": fv.get("coherence_same_season"),
        "coherence_season_d1":   fv.get("coherence_season_d1"),
        "coherence_season_d2":   fv.get("coherence_season_d2"),
        "coherence_segments":    fv.get("coherence_segments"),
        # Weighted penalty breakdown (each = weight × normalised feature)
        "penalties": {
            "coherence":   round(0.40 * coh_penalty,   4),
            "snow":        round(0.25 * snow_penalty,   4),
            "precip_d1":   round(0.75 * pr_penalty_d1, 4),
            "precip_d2":   round(0.75 * pr_penalty_d2, 4),
            "freeze_thaw": round(0.05 * ft_penalty,    4),
        },
        # Raw sensor values for display
        "snow_cover_d1":  round(snow_frac_d1,   4),
        "snow_cover_d2":  round(snow_frac_d2,   4),
        "delta_snow":     round(delta_snow_raw, 4),
        "temp_max_d1":    fv.get("temp_max_d1"),
        "temp_max_d2":    fv.get("temp_max_d2"),
        "precip_3day_d1": fv.get("precip_3day_d1"),
        "precip_3day_d2": fv.get("precip_3day_d2"),
        # Geometry
        "dt_days":    fv.get("dt_days", 0),
        "bperp_diff": fv.get("bperp_diff", 0.0),
        # Meta
        "hard_kill":      hard_kill,
        "snow_source":    fv.get("snow_source", "none"),
        "rho_inf":        round(rho_inf, 4),
        "coherence_abs":  round(coh_abs,  4),
        "score":          final,   # 0–100 integer
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
