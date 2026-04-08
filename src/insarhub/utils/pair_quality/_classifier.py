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
    """Saturate at 80 mm 7-day rolling precipitation.

    50 mm/7 days is common in humid climates and doesn't necessarily
    degrade coherence.  80 mm represents genuinely wet conditions
    that significantly raise soil moisture / dielectric constant.
    """
    return min(v / 80.0, 1.0)

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
        Uses the measured seasonal coherence as the base signal.  Applies
        date-specific environmental penalties (snow, precip, bperp) on top.

    Tier 2 — NDVI / land-cover scoring (``coherence_source == "failed"``)
        S3 was unreachable.  Delegates to lc_score() which uses WorldCover
        land-cover fractions, NDVI transitions, and weather features.

    Tier 3 — Climatology safeline
        Both S3 and NDVI/landcover data are unavailable.  Uses the hardcoded
        latitude-band coherence table (``coherence_climatology`` in fv) as the
        base signal, then applies the same environmental penalties as Tier 1.

    Returns
    -------
    score   : float in [0, 1]
    factors : dict with coherence breakdown + environmental contributions
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

    base = float(coh)

    # Environmental penalties (all in [0, 1] after normalisation)
    snow_d1    = _resolve(fv, "snow_cover_frac_d1")
    snow_d2    = _resolve(fv, "snow_cover_frac_d2")
    delta_snow = _resolve(fv, "delta_snow_cover")
    ft         = _resolve(fv, "freeze_thaw")
    pr_d1      = _resolve(fv, "precip_7day_d1")   # already normalised via _NORMALISE
    pr_d2      = _resolve(fv, "precip_7day_d2")
    bperp      = _resolve(fv, "bperp_normalized")

    penalty = (
        snow_d1    * 0.08 +   # reduced: S3 COH maps already encode seasonal snow baseline;
        snow_d2    * 0.08 +   # soft penalty covers day-specific deviation above that average
        delta_snow * 0.08 +   # increased: surface change from snowfall is a stronger signal
        ft         * 0.06 +   # freeze-thaw without snow (soil moisture, frost heave)
        pr_d1      * 0.04 +   # 7-day cumulative soil moisture (saturates at 80 mm)
        pr_d2      * 0.04 +
        bperp      * 0.06     # geometric: dead zone <150 m, saturates at 800 m
    )

    # Hard kills
    lc_water   = fv.get("lc_water_fraction") or 0.0
    precip_max = max(fv.get("precip_d1") or 0.0, fv.get("precip_d2") or 0.0)

    snow_frac_d1  = fv.get("snow_cover_frac_d1") or 0.0
    snow_frac_d2  = fv.get("snow_cover_frac_d2") or 0.0
    snow_frac_max = max(snow_frac_d1, snow_frac_d2)
    delta_snow    = fv.get("delta_snow_cover") or 0.0
    temp_d1       = fv.get("temp_max_d1")
    temp_d2       = fv.get("temp_max_d2")

    # Wet snow: temp > 0°C on acquisition day AND significant snow cover.
    # C-band penetration depth drops to ~5–10 cm at ≥1% volumetric liquid
    # water content — essentially opaque.  Dry snow (temp < 0) penetrates
    # ~20 m and does NOT kill coherence regardless of depth.
    wet_snow = (
        (temp_d1 is not None and temp_d1 > 0 and snow_frac_d1 > 0.30) or
        (temp_d2 is not None and temp_d2 > 0 and snow_frac_d2 > 0.30)
    )

    # Fresh snowfall event: sudden large change in snow cover fraction between
    # the two acquisitions.  ~11 cm accumulation causes complete C-band
    # decorrelation via surface elevation change (Guneriussen et al. 2001).
    # delta > 0.5 means ≥50% of the AOI changed from snow-free to snow-covered.
    fresh_snowfall = delta_snow > 0.50

    if lc_water > 0.50:
        final = 0.0
        hard_kill = "water_dominant"
    elif precip_max > 30.0:
        final = 0.0
        hard_kill = "heavy_rain"
    elif wet_snow:
        final = 0.0
        hard_kill = "wet_snow"
    elif fresh_snowfall:
        final = 0.0
        hard_kill = "fresh_snowfall"
    elif snow_frac_max > 0.90:
        # Near-total dry snow cover: catches extreme events (summer blizzard,
        # high-altitude snowpack) not captured by the S3 seasonal COH maps.
        final = 0.0
        hard_kill = "heavy_snow_cover"
    else:
        final = round(max(0.0, min(1.0, base - penalty)), 4)
        hard_kill = None

    factors: dict = {
        # Coherence source
        "coherence_source":      source,
        "coherence_expected":    coh,
        "coherence_by_class":    fv.get("coherence_by_class", {}),
        "coherence_climatology": fv.get("coherence_climatology"),
        "coherence_same_season": fv.get("coherence_same_season"),
        "coherence_season_d1":   fv.get("coherence_season_d1"),
        "coherence_season_d2":   fv.get("coherence_season_d2"),
        "coherence_segments":    fv.get("coherence_segments"),
        # Environmental penalty breakdown
        "snow_d1":       round(snow_d1, 4),
        "snow_d2":       round(snow_d2, 4),
        "temp_max_d1":   fv.get("temp_max_d1"),
        "temp_max_d2":   fv.get("temp_max_d2"),
        "freeze_thaw":   int(ft),
        "precip_7day_d1": fv.get("precip_7day_d1"),
        "precip_7day_d2": fv.get("precip_7day_d2"),
        "bperp_normalized": round(bperp, 4),
        # Geometry
        "dt_days":   fv.get("dt_days", 0),
        "bperp_diff": fv.get("bperp_diff", 0.0),
        # Per-class land cover fractions (AOI-level, same for all pairs)
        "lc_class_fractions": {
            "stable":     round((fv.get("lc_urban_fraction") or 0.0) + (fv.get("lc_bare_fraction") or 0.0), 3),
            "vegetation": round((fv.get("lc_shrub_fraction") or 0.0) + (fv.get("lc_grass_fraction") or 0.0) + (fv.get("lc_crop_fraction") or 0.0), 3),
            "forest":     round(fv.get("lc_forest_fraction") or 0.0, 3),
        },
        # Meta
        "hard_kill":   hard_kill,
        "snow_source": fv.get("snow_source", "none"),
        "score":       final,
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
