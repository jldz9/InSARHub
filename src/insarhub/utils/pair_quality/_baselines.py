# -*- coding: utf-8 -*-
"""
Baseline feature extractor.

Derives pair-level geometry features from scene names and the pre-saved
baselines_*.json files.  No network calls — all data is already on disk.

Features returned
-----------------
  dt_days          : int   — absolute temporal baseline in days
  bperp_diff       : float — absolute perpendicular baseline difference in m
  dt_normalized    : float — dt_days / 180, clamped to [0, 1]
  bperp_normalized : float — bperp_diff / 300, clamped to [0, 1]
  is_annual_repeat : int   — 1 if pair is an N-year repeat (±20 days), else 0
"""

from __future__ import annotations

from datetime import datetime


_DT_SATURATE    = 180.0   # days at which dt penalty maxes out
_BPERP_SATURATE = 300.0   # metres at which bperp penalty maxes out
_ANNUAL_TOL     = 20      # day tolerance for annual-repeat detection


def _scene_date(name: str) -> datetime | None:
    """Parse acquisition date from a Sentinel-1 scene name."""
    raw = name[17:25] if len(name) > 25 else ""
    if len(raw) == 8:
        try:
            return datetime(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            pass
    return None


def _is_annual_repeat(dt_days: int, tol: int = _ANNUAL_TOL) -> bool:
    for n in range(1, 6):
        if abs(dt_days - n * 365) <= tol:
            return True
    return False


def extract(
    ref: str,
    sec: str,
    bperp_ref: float,
    bperp_sec: float,
) -> dict:
    """Return baseline feature dict for a single (ref, sec) pair.

    Parameters
    ----------
    ref, sec      : Sentinel-1 scene names
    bperp_ref     : perpendicular baseline of ref scene (m)
    bperp_sec     : perpendicular baseline of sec scene (m)
    """
    d1 = _scene_date(ref)
    d2 = _scene_date(sec)

    if d1 and d2:
        dt_days = abs((d2 - d1).days)
    else:
        dt_days = 0

    bperp_diff = abs(bperp_ref - bperp_sec)

    return {
        "dt_days":          dt_days,
        "bperp_diff":       round(bperp_diff, 1),
        "dt_normalized":    round(min(dt_days / _DT_SATURATE, 1.0), 4),
        "bperp_normalized": round(min(bperp_diff / _BPERP_SATURATE, 1.0), 4),
        "is_annual_repeat": int(_is_annual_repeat(dt_days)),
    }
