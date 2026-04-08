# -*- coding: utf-8 -*-
"""
Land-cover-aware pair quality scorer.

Architecture
------------
1. Determine scoring branch from WorldCover land cover fractions.
2. Apply branch-specific weights (dt saturation, veg penalty scale).
3. Apply hard-kill conditions (water, snow/ice, NDVI transition, fire, heavy rain).
4. Return final score in [0, 1] where 1.0 = high coherence, 0.0 = low coherence.

Branches
--------
  A — Urban / Bare soil       : longest coherence, relaxed dt saturation
  B — Cropland / Grass / Shrub: NDVI-state-dependent, seasonal
  C — Forest                  : structural coherence only, hard cap at 0.25
  D — Mixed                   : weighted blend of A/B/C contributions

Hard kills (override branch score → 0.0)
-----------------------------------------
  Water body dominant (>50 %)
  Snow / Ice dominant (>40 %)
  NDVI state crossing (dormant↔active) for veg-dominated AOI
  Fire event (NASA FIRMS, if MAP_KEY configured)
  Heavy rain (>30 mm/day on either date)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ── NDVI state threshold ──────────────────────────────────────────────────────
_NDVI_THRESH = 0.30   # below = dormant, above = active

# ── Hard-kill thresholds ──────────────────────────────────────────────────────
_WATER_KILL_FRAC  = 0.50   # >50 % water → kill
_SNOW_KILL_FRAC   = 0.40   # >40 % snow/ice → kill
_RAIN_KILL_MM     = 30.0   # mm/day on either date → hard penalty
_VEG_LC_THRESH    = 0.40   # combined veg fraction to trigger NDVI-transition kill

# ── Branch weight sets ────────────────────────────────────────────────────────
# Each set is a penalty weight dict (higher val = more penalty for that feature).
# All positive weights should sum to ≤ 1.0 before the annual_repeat bonus.

_WEIGHTS_A: dict[str, float] = {
    # Urban / Bare — temporal coherence is long, geometric matters more
    "dt_normalized":      0.15,   # relaxed — buildings/rock stay coherent
    "bperp_normalized":   0.12,
    "snow_cover_frac_d1": 0.08,
    "snow_cover_frac_d2": 0.08,
    "delta_snow_cover":   0.06,
    "freeze_thaw":        0.06,
    "precip_7day_d1":     0.04,
    "precip_7day_d2":     0.04,
    "season_penalty":     0.04,
    "veg_temporal":       0.00,   # negligible for urban/bare
    "lc_forest_fraction": 0.02,
    "lc_water_fraction":  0.02,
    "is_annual_repeat":  -0.15,
}

_WEIGHTS_B: dict[str, float] = {
    # Cropland / Grassland / Shrub — vegetation is the dominant driver
    "dt_normalized":      0.18,
    "bperp_normalized":   0.08,
    "snow_cover_frac_d1": 0.08,
    "snow_cover_frac_d2": 0.08,
    "delta_snow_cover":   0.07,
    "freeze_thaw":        0.07,
    "precip_7day_d1":     0.04,
    "precip_7day_d2":     0.04,
    "season_penalty":     0.08,
    "veg_temporal":       0.12,   # strong — veg × dt dominates
    "lc_forest_fraction": 0.02,
    "lc_water_fraction":  0.02,
    "is_annual_repeat":  -0.18,
}

_WEIGHTS_C: dict[str, float] = {
    # Forest — coherence is always low; score is capped downstream
    "dt_normalized":      0.25,
    "bperp_normalized":   0.10,
    "snow_cover_frac_d1": 0.08,
    "snow_cover_frac_d2": 0.08,
    "delta_snow_cover":   0.06,
    "freeze_thaw":        0.05,
    "precip_7day_d1":     0.04,
    "precip_7day_d2":     0.04,
    "season_penalty":     0.08,
    "veg_temporal":       0.15,
    "lc_forest_fraction": 0.05,
    "lc_water_fraction":  0.02,
    "is_annual_repeat":  -0.18,
}

_FOREST_CAP = 0.25   # forest branch score is capped here


# ── Feature resolver (same as _classifier._resolve) ──────────────────────────

def _norm_precip(v: float) -> float:
    return min(v / 80.0, 1.0)

_NORMALISE = {
    "precip_7day_d1": _norm_precip,
    "precip_7day_d2": _norm_precip,
}

def _resolve(fv: dict, key: str) -> float:
    raw = fv.get(key)
    if raw is None:
        return 0.0
    fn = _NORMALISE.get(key)
    val = fn(float(raw)) if fn else float(raw)
    return max(0.0, min(1.0, val))


def _weighted_score(fv: dict, weights: dict[str, float]) -> float:
    """Compute raw penalty sum → invert → clamp to [0, 1]."""
    raw = sum(weights[k] * _resolve(fv, k) for k in weights)
    return max(0.0, min(1.0, 1.0 - raw))


# ── NDVI transition detector ──────────────────────────────────────────────────

def _ndvi_transition_kill(fv: dict, veg_fraction: float) -> bool:
    """Return True if NDVI crosses the dormant/active threshold between dates.

    Only triggered when the AOI has substantial vegetation cover.
    """
    if veg_fraction < _VEG_LC_THRESH:
        return False
    ndvi1 = fv.get("ndvi_d1")
    ndvi2 = fv.get("ndvi_d2")
    if ndvi1 is None or ndvi2 is None:
        return False
    state1 = ndvi1 >= _NDVI_THRESH
    state2 = ndvi2 >= _NDVI_THRESH
    return state1 != state2   # crossing → kill


# ── Fire detection (NASA FIRMS, optional) ─────────────────────────────────────

def _fire_kill(fv: dict) -> bool:
    """Return True if a fire event is recorded for this pair's dates/AOI.

    Requires FIRMS_MAP_KEY environment variable.  Silently skips if absent.
    """
    map_key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not map_key:
        return False

    aoi_wkt  = fv.get("aoi_wkt")
    date1    = fv.get("date1")
    date2    = fv.get("date2")
    if not (aoi_wkt and date1 and date2):
        return False

    try:
        import urllib.request, json, re
        from datetime import datetime, timedelta

        # Parse bbox from WKT
        coords = re.findall(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', aoi_wkt)
        if not coords:
            return False
        lons = [float(c[0]) for c in coords]
        lats = [float(c[1]) for c in coords]
        bbox = f"{min(lons)},{min(lats)},{max(lons)},{max(lats)}"

        d1 = datetime.fromisoformat(date1)
        d2 = datetime.fromisoformat(date2)
        # Check window: 7 days around each acquisition
        for d in (d1, d2):
            start = (d - timedelta(days=3)).strftime("%Y-%m-%d")
            end   = (d + timedelta(days=3)).strftime("%Y-%m-%d")
            url = (
                f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
                f"{map_key}/VIIRS_SNPP_NRT/{bbox}/7/{start}"
            )
            with urllib.request.urlopen(url, timeout=10) as resp:
                text = resp.read().decode()
                lines = [l for l in text.strip().splitlines() if l and not l.startswith("latitude")]
                if lines:
                    logger.info("Fire detected near AOI around %s (%d hotspots)", d.date(), len(lines))
                    return True
    except Exception as exc:
        logger.debug("FIRMS check failed: %s", exc)
    return False


# ── Main scoring function ─────────────────────────────────────────────────────

def score(fv: dict) -> tuple[float, dict]:
    """Land-cover-aware quality score for one interferogram pair.

    Parameters
    ----------
    fv : FeatureVector dict from FeatureAssembler.assemble()
         Must include lc_* fractions, ndvi_d1/d2, weather fields.

    Returns
    -------
    score   : float in [0, 1] — 1.0 = high coherence, 0.0 = low coherence
    factors : dict with branch, hard_kills, component scores
    """
    lc_urban   = fv.get("lc_urban_fraction")   or 0.0
    lc_bare    = fv.get("lc_bare_fraction")    or 0.0
    lc_crop    = fv.get("lc_crop_fraction")    or 0.0
    lc_grass   = fv.get("lc_grass_fraction")   or 0.0
    lc_shrub   = fv.get("lc_shrub_fraction")   or 0.0
    lc_forest  = fv.get("lc_forest_fraction")  or 0.0
    lc_water   = fv.get("lc_water_fraction")   or 0.0
    lc_snow    = fv.get("lc_snow_fraction")    or 0.0

    hard_kills: list[str] = []
    warnings:   list[str] = []

    # ── Hard kills ────────────────────────────────────────────────────────────
    if lc_water > _WATER_KILL_FRAC:
        hard_kills.append("water_dominant")

    if lc_snow > _SNOW_KILL_FRAC:
        hard_kills.append("snow_ice_dominant")

    # Heavy rain on either date
    precip1 = fv.get("precip_d1") or 0.0
    precip2 = fv.get("precip_d2") or 0.0
    if max(precip1, precip2) > _RAIN_KILL_MM:
        hard_kills.append("heavy_rain")

    # Wet snow: temp > 0°C AND significant snow cover on either acquisition day.
    # C-band penetration drops to ~5–10 cm at ≥1% liquid water content.
    snow_frac_d1 = fv.get("snow_cover_frac_d1") or 0.0
    snow_frac_d2 = fv.get("snow_cover_frac_d2") or 0.0
    temp_d1      = fv.get("temp_max_d1")
    temp_d2      = fv.get("temp_max_d2")
    if ((temp_d1 is not None and temp_d1 > 0 and snow_frac_d1 > 0.30) or
            (temp_d2 is not None and temp_d2 > 0 and snow_frac_d2 > 0.30)):
        hard_kills.append("wet_snow")

    # Fresh snowfall: sudden large surface change decorrelates via elevation change.
    # ~11 cm accumulation causes complete C-band decorrelation (Guneriussen 2001).
    delta_snow_cover = fv.get("delta_snow_cover") or 0.0
    if delta_snow_cover > 0.50:
        hard_kills.append("fresh_snowfall")

    # Near-total snow cover on either date (extreme event not in seasonal COH maps)
    if max(snow_frac_d1, snow_frac_d2) > 0.90:
        hard_kills.append("heavy_snow_cover")

    # Fire (optional, requires FIRMS_MAP_KEY)
    if _fire_kill(fv):
        hard_kills.append("fire")

    if hard_kills:
        return 0.0, {
            "branch":       "killed",
            "contributions": {},
            "hard_kills":   hard_kills,
            "warnings":     warnings,
            "score":        0.0,
        }

    # ── Branch selection + blended score ─────────────────────────────────────
    frac_A = lc_urban + lc_bare
    frac_B = lc_crop + lc_grass + lc_shrub
    frac_C = lc_forest
    total  = frac_A + frac_B + frac_C or 1.0   # avoid div-by-zero

    # Normalise fractions
    w_A = frac_A / total
    w_B = frac_B / total
    w_C = frac_C / total

    score_A = _weighted_score(fv, _WEIGHTS_A)
    score_B = _weighted_score(fv, _WEIGHTS_B)
    score_C = min(_weighted_score(fv, _WEIGHTS_C), _FOREST_CAP)

    # Dominant branch label for reporting
    branch_fracs = {"A_urban_bare": w_A, "B_veg": w_B, "C_forest": w_C}
    dominant = max(branch_fracs, key=branch_fracs.get)

    blended = w_A * score_A + w_B * score_B + w_C * score_C

    # Build contributions from the blended (weighted) penalty per feature
    # Use the dominant branch weights scaled by that branch's mix fraction
    _dominant_weights = {"A_urban_bare": _WEIGHTS_A, "B_veg": _WEIGHTS_B, "C_forest": _WEIGHTS_C}[dominant]
    _dominant_frac    = {"A_urban_bare": w_A,        "B_veg": w_B,        "C_forest": w_C}[dominant]
    contributions: dict[str, float] = {
        k: round(_dominant_frac * w * _resolve(fv, k), 4)
        for k, w in _dominant_weights.items()
        if abs(_dominant_frac * w * _resolve(fv, k)) > 0.0001
    }

    # ── NDVI transition: cap score in risky range (0.35–0.50) ────────────────
    veg_fraction = lc_crop + lc_grass + lc_shrub
    if _ndvi_transition_kill(fv, veg_fraction):
        warnings.append("ndvi_transition")
        blended = min(blended, 0.50)   # push into risky, never good

    final   = round(max(0.0, min(1.0, blended)), 4)

    factors: dict = {
        "branch":         dominant,
        "branch_weights": {"A": round(w_A, 3), "B": round(w_B, 3), "C": round(w_C, 3)},
        "score_A":        round(score_A, 4),
        "score_B":        round(score_B, 4),
        "score_C":        round(score_C, 4),
        "contributions":  contributions,
        "hard_kills":     hard_kills,
        "warnings":       warnings,
        "veg_fraction":  round(veg_fraction, 3),
        "ndvi_d1":       fv.get("ndvi_d1"),
        "ndvi_d2":       fv.get("ndvi_d2"),
        "lc_urban":      round(lc_urban, 3),
        "lc_bare":       round(lc_bare, 3),
        "lc_crop":       round(lc_crop, 3),
        "lc_grass":      round(lc_grass, 3),
        "lc_shrub":      round(lc_shrub, 3),
        "lc_forest":     round(lc_forest, 3),
        "lc_water":      round(lc_water, 3),
        "lc_snow":       round(lc_snow, 3),
        "score":         final,
    }

    return final, factors
