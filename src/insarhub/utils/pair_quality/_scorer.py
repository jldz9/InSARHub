# -*- coding: utf-8 -*-
"""
Per-pair quality scorer.

Combines temporal baseline, perpendicular baseline, snow conditions,
season crossing, and vegetation density into a single score in [0, 1]:

    1.0 – likely high-coherence interferogram
    0.0 – likely low-coherence / noisy interferogram

Weights (applied as penalties internally, then inverted)
---------------------------------------------------------
  dt_score       0.30  (temporal decorrelation)
  bperp_score    0.15  (geometric decorrelation)
  snow_score     0.25  (snow + freeze-thaw)
  season_score   0.15  (cross-season misfit)
  veg_score      0.15  (vegetation × temporal interaction)
  annual_bonus  -0.30  (same-day-of-year repeat, reduces penalty)
"""

from __future__ import annotations

from datetime import datetime

from insarhub.utils.defaults import SEASON_NH as _SEASON_NH, SEASON_ADJACENT as _ADJACENT


def _season(month: int, lat: float) -> str:
    """Return season name adjusted for hemisphere."""
    # Flip seasons for southern hemisphere
    if lat < 0:
        month = ((month - 1 + 6) % 12) + 1
    return _SEASON_NH[month]


def season_penalty(date1: str, date2: str, lat: float = 45.0) -> float:
    """Return 0–1 season-crossing penalty.

    0.0 – same season
    0.35 – adjacent seasons
    0.70 – opposite seasons (no winter)
    0.95 – one date in core winter, other not  (high snow-cover risk)
    """
    m1 = int(date1[5:7])
    m2 = int(date2[5:7])
    s1 = _season(m1, lat)
    s2 = _season(m2, lat)
    if s1 == s2:
        return 0.0
    pair = frozenset({s1, s2})
    if pair in _ADJACENT:
        return 0.35
    if "winter" in pair:
        return 0.95
    return 0.70


# ── Annual-repeat bonus ───────────────────────────────────────────────────────

def annual_repeat_bonus(date1: str, date2: str,
                        tol_days: int = 20) -> float:
    """Return 0.30 if the pair is an approximate N-year repeat, else 0."""
    d1 = datetime.fromisoformat(date1)
    d2 = datetime.fromisoformat(date2)
    dt_days = abs((d2 - d1).days)
    for n in range(1, 5):
        if abs(dt_days - n * 365) <= tol_days:
            return 0.30
    return 0.0


# ── Snow score ────────────────────────────────────────────────────────────────

def snow_score(snow1: dict, snow2: dict) -> float:
    """Return 0–1 quality penalty from snow conditions on both dates."""

    def _single(s: dict) -> float:
        depth    = s.get("snow_depth") or 0.0
        snowfall = s.get("snowfall")   or 0.0
        tmax     = s.get("temp_max")
        if depth > 20:
            # Wet deep snow (tmax > 0) is worse than cold dry snow
            return 1.0 if (tmax is not None and tmax > 0) else 0.85
        if depth > 5:
            return 0.60
        if snowfall > 1.0:
            return 0.30
        return 0.0

    base = max(_single(snow1), _single(snow2))

    # Freeze-thaw crossing: one date frozen (tmax < 0), other thawed
    tmax1 = snow1.get("temp_max")
    tmax2 = snow2.get("temp_max")
    if tmax1 is not None and tmax2 is not None:
        frozen1 = tmax1 < 0
        frozen2 = tmax2 < 0
        if frozen1 != frozen2:
            base = min(1.0, base + 0.35)

    return base


# ── Main scorer ───────────────────────────────────────────────────────────────

def score_pair(
    date1:      str,
    date2:      str,
    bperp_diff: float,
    snow1:      dict,
    snow2:      dict,
    ndvi1:      float,
    ndvi2:      float,
    lat:        float = 45.0,
    dt_saturate:    float = 180.0,
    bperp_saturate: float = 300.0,
) -> tuple[float, dict]:
    """Compute quality score and factor breakdown for a single pair.

    Returns
    -------
    score : float in [0, 1], where 1.0 = high coherence, 0.0 = low coherence
    factors : dict with individual component scores
    """
    d1 = datetime.fromisoformat(date1)
    d2 = datetime.fromisoformat(date2)
    dt_days = abs((d2 - d1).days)

    dt_s     = min(dt_days / dt_saturate, 1.0)
    bperp_s  = min(abs(bperp_diff) / bperp_saturate, 1.0)
    snow_s   = snow_score(snow1, snow2)
    season_s = season_penalty(date1, date2, lat)
    veg_s    = max(ndvi1, ndvi2) * dt_s          # high veg × long dt = bad
    bonus    = annual_repeat_bonus(date1, date2)

    raw = (
        0.30 * dt_s
        + 0.15 * bperp_s
        + 0.25 * snow_s
        + 0.15 * season_s
        + 0.15 * veg_s
        - bonus
    )
    score = max(0.0, min(1.0, 1.0 - raw))

    factors = {
        "dt_days":    dt_days,
        "bperp_diff": round(abs(bperp_diff), 1),
        "dt":         round(dt_s, 3),
        "bperp":      round(bperp_s, 3),
        "snow":       round(snow_s, 3),
        "season":     round(season_s, 3),
        "veg":        round(veg_s, 3),
        "annual_bonus": round(bonus, 3),
        "score":      round(score, 3),
    }
    return score, factors
