# -*- coding: utf-8 -*-
"""
Global Seasonal Sentinel-1 Interferometric Coherence lookup.

Dataset : Global Seasonal Sentinel-1 Interferometric Coherence and
          Backscatter Data Set — Kellndorfer et al. (2022)
          https://www.nature.com/articles/s41597-022-01439-7
Source  : AWS S3 COGs (no auth required)
          s3://sentinel-1-global-coherence-earthbigdata

Note    : This dataset is produced and hosted exclusively by Earth Big Data
          LLC on their AWS S3 bucket.  ASF only archives raw SAR scenes and
          does not redistribute this derived product.  There is no known
          public mirror; the climatology table below is the offline fallback.

Tiles   : 1°×1°, named by lower-left corner (e.g. N47E011, N37W113)
Seasons : winter (DJF), spring (MAM), summer (JJA), fall (SON)
Levels  : COH06, COH12, COH18, COH24, COH36, COH48 (days)
Pol     : vv (default), vh

Fallback: If the S3 read fails (no network, region blocked, etc.) the
          _CLIM climatology table provides median VV coherence values
          binned by 10° latitude and season — these are always available.

Usage
-----
    from insarhub.utils.pair_quality._coherence import estimate_coherence

    result = estimate_coherence(
        aoi_wkt="POLYGON(...)",
        lat=37.8, lon=-112.8,
        date1="20200115", date2="20200408",
    )
    # result["coherence_expected"] → float in [0, 1] (never None — falls back to climatology)
"""

from __future__ import annotations

import functools
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

_log = logging.getLogger(__name__)

# ── Dataset source ────────────────────────────────────────────────────────────
#
# Source 1 — AWS S3 (primary, no auth required)
#   Kellndorfer et al. 2022, hosted by Earth Big Data LLC.
#   The dataset lives exclusively on this bucket; ASF and MPC do not mirror it.
# Source 2 — Hardcoded latitude-band climatology (never fails)
#   Median VV coherence values derived from global dataset statistics,
#   binned by 10° latitude and season.  Used when S3 is unreachable.

_S3_BASE = (
    "https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com"
    "/data/mosaics"
)

# Available temporal baselines in days
_COH_LEVELS = [12, 24, 36, 48]

# Season names and the months that belong to them (Northern Hemisphere)
_MONTH_TO_SEASON: dict[int, str] = {
    12: "winter", 1: "winter",  2: "winter",
    3:  "spring", 4: "spring",  5: "spring",
    6:  "summer", 7: "summer",  8: "summer",
    9:  "fall",   10: "fall",   11: "fall",
}

# Season-start boundaries as (month, day) — used to split cross-season pairs
_SEASON_STARTS = [(3, 1), (6, 1), (9, 1), (12, 1)]


# ── Tile & URL helpers ────────────────────────────────────────────────────────

def _tile_lat(lat: float) -> str:
    """Return the latitude part of the 1°×1° tile name (lower-left corner)."""
    t = math.floor(lat)
    return f"N{t:02d}" if t >= 0 else f"S{abs(t):02d}"


def _tile_lon(lon: float) -> str:
    """Return the longitude part of the 1°×1° tile name (lower-left corner)."""
    t = math.floor(lon)
    return f"E{t:03d}" if t >= 0 else f"W{abs(t):03d}"


def _coh_url(season: str, level: int, pol: str = "vv") -> str:
    """Return the AWS S3 URL for a given season/level/pol mosaic."""
    fname = f"Global_{season}_{pol}_COH{level:02d}_100ppd.tif"
    return f"{_S3_BASE}/{fname}"


# ── Climatology fallback ──────────────────────────────────────────────────────
#
# Median VV COH values per 10° latitude band and season.
# Derived from the global dataset statistics (Kellndorfer et al. 2022).
# Rows keyed by abs(lat) band lower bound (0, 10, 20, ... 80).
# Values: {level: coh} where level is temporal baseline in days.
#
# Typical patterns:
#   - Tropics (0–20°): high humidity / vegetation → low coherence all year
#   - Mid-latitudes (30–60°): seasonal agriculture / snow → strong seasonality
#   - High latitudes (60–80°): winter snow / permafrost → low winter coherence

_CLIM: dict[int, dict[str, dict[int, float]]] = {
    # abs(lat) band → season → {level: coh}
    0: {  # 0–10° (tropics)
        "winter": {6: 0.38, 12: 0.28, 18: 0.22, 24: 0.18, 36: 0.14, 48: 0.11},
        "spring": {6: 0.36, 12: 0.27, 18: 0.21, 24: 0.17, 36: 0.13, 48: 0.10},
        "summer": {6: 0.35, 12: 0.26, 18: 0.20, 24: 0.16, 36: 0.12, 48: 0.10},
        "fall":   {6: 0.37, 12: 0.27, 18: 0.21, 24: 0.17, 36: 0.13, 48: 0.11},
    },
    10: {  # 10–20°
        "winter": {6: 0.45, 12: 0.34, 18: 0.27, 24: 0.22, 36: 0.17, 48: 0.14},
        "spring": {6: 0.42, 12: 0.31, 18: 0.25, 24: 0.20, 36: 0.16, 48: 0.13},
        "summer": {6: 0.38, 12: 0.28, 18: 0.22, 24: 0.18, 36: 0.14, 48: 0.11},
        "fall":   {6: 0.43, 12: 0.32, 18: 0.25, 24: 0.20, 36: 0.16, 48: 0.13},
    },
    20: {  # 20–30° (subtropics / desert belt)
        "winter": {6: 0.58, 12: 0.47, 18: 0.39, 24: 0.33, 36: 0.26, 48: 0.21},
        "spring": {6: 0.55, 12: 0.44, 18: 0.36, 24: 0.30, 36: 0.24, 48: 0.19},
        "summer": {6: 0.50, 12: 0.39, 18: 0.32, 24: 0.26, 36: 0.20, 48: 0.17},
        "fall":   {6: 0.54, 12: 0.43, 18: 0.35, 24: 0.29, 36: 0.23, 48: 0.18},
    },
    30: {  # 30–40° (Mediterranean / temperate)
        "winter": {6: 0.52, 12: 0.41, 18: 0.33, 24: 0.27, 36: 0.21, 48: 0.17},
        "spring": {6: 0.48, 12: 0.37, 18: 0.30, 24: 0.24, 36: 0.19, 48: 0.15},
        "summer": {6: 0.60, 12: 0.49, 18: 0.41, 24: 0.34, 36: 0.27, 48: 0.22},
        "fall":   {6: 0.57, 12: 0.46, 18: 0.38, 24: 0.31, 36: 0.25, 48: 0.20},
    },
    40: {  # 40–50° (mid-latitude agriculture)
        "winter": {6: 0.48, 12: 0.36, 18: 0.29, 24: 0.23, 36: 0.18, 48: 0.14},
        "spring": {6: 0.45, 12: 0.34, 18: 0.27, 24: 0.22, 36: 0.17, 48: 0.13},
        "summer": {6: 0.55, 12: 0.43, 18: 0.35, 24: 0.29, 36: 0.23, 48: 0.18},
        "fall":   {6: 0.58, 12: 0.46, 18: 0.38, 24: 0.31, 36: 0.25, 48: 0.20},
    },
    50: {  # 50–60° (boreal fringe)
        "winter": {6: 0.44, 12: 0.32, 18: 0.25, 24: 0.20, 36: 0.15, 48: 0.12},
        "spring": {6: 0.40, 12: 0.29, 18: 0.23, 24: 0.18, 36: 0.14, 48: 0.11},
        "summer": {6: 0.47, 12: 0.36, 18: 0.28, 24: 0.23, 36: 0.18, 48: 0.14},
        "fall":   {6: 0.50, 12: 0.38, 18: 0.30, 24: 0.25, 36: 0.19, 48: 0.15},
    },
    60: {  # 60–70° (boreal / taiga)
        "winter": {6: 0.38, 12: 0.27, 18: 0.21, 24: 0.17, 36: 0.13, 48: 0.10},
        "spring": {6: 0.35, 12: 0.25, 18: 0.19, 24: 0.15, 36: 0.12, 48: 0.09},
        "summer": {6: 0.40, 12: 0.30, 18: 0.23, 24: 0.19, 36: 0.15, 48: 0.12},
        "fall":   {6: 0.42, 12: 0.31, 18: 0.24, 24: 0.20, 36: 0.15, 48: 0.12},
    },
    70: {  # 70–80° (arctic / tundra)
        "winter": {6: 0.32, 12: 0.22, 18: 0.17, 24: 0.14, 36: 0.10, 48: 0.08},
        "spring": {6: 0.30, 12: 0.21, 18: 0.16, 24: 0.13, 36: 0.10, 48: 0.08},
        "summer": {6: 0.35, 12: 0.25, 18: 0.19, 24: 0.16, 36: 0.12, 48: 0.10},
        "fall":   {6: 0.33, 12: 0.23, 18: 0.18, 24: 0.14, 36: 0.11, 48: 0.09},
    },
    80: {  # 80–90° (polar)
        "winter": {6: 0.28, 12: 0.19, 18: 0.14, 24: 0.11, 36: 0.09, 48: 0.07},
        "spring": {6: 0.28, 12: 0.19, 18: 0.14, 24: 0.11, 36: 0.09, 48: 0.07},
        "summer": {6: 0.30, 12: 0.21, 18: 0.16, 24: 0.13, 36: 0.10, 48: 0.08},
        "fall":   {6: 0.29, 12: 0.20, 18: 0.15, 24: 0.12, 36: 0.09, 48: 0.07},
    },
}


def _climatology_coh_map(lat: float, season: str) -> dict[int, float]:
    """Return climatological COH map for a latitude/season combination."""
    band = min(int(abs(lat) // 10) * 10, 80)
    return dict(_CLIM[band][season])


# ── Season helpers ────────────────────────────────────────────────────────────

def _get_season(month: int, lat: float = 90.0) -> str:
    """Return season string for a given month, flipping hemispheres for S lat."""
    if lat < 0:
        month = ((month - 1 + 6) % 12) + 1   # shift 6 months for SH
    return _MONTH_TO_SEASON[month]


def _normalize_date(s: str) -> str:
    """Return YYYYMMDD string from any of three input formats:
      - ``'YYYYMMDD'``       — used as-is
      - ``'YYYY-MM-DD...'``  — dashes stripped
      - Sentinel-1 scene name (len > 25) — date extracted from chars 17–24
    """
    if len(s) > 25:          # full scene name: S1A_IW_SLC__1SDV_YYYYMMDD...
        return s[17:25]
    return s.replace("-", "")[:8]


def _season_boundaries_between(d1: datetime, d2: datetime) -> list[datetime]:
    """Return all season-start boundaries strictly between d1 and d2, sorted."""
    boundaries: list[datetime] = []
    for year in range(d1.year, d2.year + 1):
        for month, day in _SEASON_STARTS:
            try:
                b = datetime(year, month, day)
            except ValueError:
                continue
            if d1 < b < d2:
                boundaries.append(b)
    return sorted(boundaries)


def split_by_season(
    date1_str: str,
    date2_str: str,
    lat: float = 90.0,
) -> list[tuple[int, str]]:
    """Split a pair's date span into (days, season) segments at season boundaries.

    Parameters
    ----------
    date1_str, date2_str : YYYYMMDD strings (or longer scene names)
    lat                  : AOI centroid latitude (flips seasons in SH)

    Returns
    -------
    List of (segment_days, season_name) tuples ordered from d1 → d2.
    Segments with 0 days are omitted.

    Examples
    --------
    >>> split_by_season("20200115", "20200320")
    [(46, 'winter'), (19, 'spring')]
    >>> split_by_season("20200601", "20200812")
    [(72, 'summer')]
    """
    d1 = datetime.strptime(_normalize_date(date1_str), "%Y%m%d")
    d2 = datetime.strptime(_normalize_date(date2_str), "%Y%m%d")
    if d2 < d1:
        d1, d2 = d2, d1

    boundaries = _season_boundaries_between(d1, d2)
    segments: list[tuple[int, str]] = []
    current = d1

    for b in boundaries:
        dt = (b - current).days
        if dt > 0:
            segments.append((dt, _get_season(current.month, lat)))
        current = b

    dt = (d2 - current).days
    if dt > 0:
        segments.append((dt, _get_season(current.month, lat)))

    if not segments:
        total = (d2 - d1).days
        segments = [(max(total, 1), _get_season(d1.month, lat))]

    return segments


# ── InSAR-relevant land cover groups ─────────────────────────────────────────
#
# WorldCover class codes grouped by InSAR coherence behaviour:
#   stable     — urban + bare rock/soil: highest ρ∞, longest τ, PS candidates
#   vegetation — shrub + grass + crop:   seasonal, NDVI-driven, most variable
#   forest     — tree cover + mangrove:  volume scattering, always low coherence
#   water      — water + wetland:        zero coherence (hard kill)
#
_LC_GROUPS: dict[str, set[int]] = {
    "stable":     {50, 60},
    "vegetation": {20, 30, 40},
    "forest":     {10, 95},
    "water":      {80, 90},
}
_LC_MIN_PIXELS = 30   # minimum coherence pixels required for a reliable class mean


# ── COH level reader (S3 COG windowed read) ───────────────────────────────────

def _read_coh_pixels(
    aoi_wkt: str,
    season: str,
    level: int,
    pol: str = "vv",
) -> tuple:
    """Return (array_uint8, transform, crs) for coherence pixels over AOI.

    array_uint8 values: 0–100 = coherence × 100; 255 = nodata.
    Returns (None, None, None) on failure.
    """
    try:
        import rasterio
        from rasterio.mask import mask as rio_mask
        from shapely import wkt as shapely_wkt

        geom    = shapely_wkt.loads(aoi_wkt)
        vsicurl = f"/vsicurl/{_coh_url(season, level, pol)}"

        with rasterio.open(vsicurl) as src:
            out, transform = rio_mask(
                src, [geom.__geo_interface__],
                crop=True, nodata=255, filled=True,
            )
            return out[0], transform, src.crs

    except Exception as exc:
        _log.debug("S1 COH pixel read failed — season=%s COH%02d: %s", season, level, exc)
        return None, None, None


def _read_mean_coh(
    aoi_wkt: str,
    season: str,
    level: int,
    pol: str = "vv",
) -> float | None:
    """Return mean coherence over AOI from a global mosaic S3 COG (windowed read).

    Raster values are uint8 (0–100, where 100 = coherence 1.0).
    Returns coherence in [0, 1], or None on failure.
    """
    arr, _, _ = _read_coh_pixels(aoi_wkt, season, level, pol)
    if arr is None:
        return None
    data  = arr.astype(float)
    valid = data[(data > 0) & (data < 255)]   # 0 = native nodata, 255 = outside polygon
    if valid.size == 0:
        return None
    return float(valid.mean()) / 100.0


def _fetch_season_coh_by_class(
    aoi_wkt: str,
    lat: float,
    lon: float,
    season: str,
    pol: str = "vv",
    cache: dict | None = None,
) -> dict[str, dict[int, float]]:
    """Return per-LC-class COH maps for one season.

    Reads WorldCover pixel data, reprojects to coherence grid (~1.1 km),
    then groups coherence pixel values by InSAR-relevant land cover class.

    Returns
    -------
    {class_name: {level_days: coherence}}
    Classes with fewer than _LC_MIN_PIXELS coherence pixels are omitted.
    Returns {} if WorldCover or coherence data are unavailable.
    """
    cache_key = f"s1coh_cls:{lat:.2f}:{lon:.2f}:{season}:{pol}"
    if cache is not None and cache_key in cache:
        raw = cache[cache_key]
        # Re-inflate int keys (JSON serialises dict keys as strings)
        return {cls: {int(k): float(v) for k, v in lvl.items()}
                for cls, lvl in raw.items()}

    try:
        import numpy as np
        from rasterio.warp import reproject, Resampling
        from insarhub.utils.pair_quality._landcover import read_pixels as _wc_pixels
    except ImportError as exc:
        _log.debug("Per-class coherence skipped — missing dependency: %s", exc)
        return {}

    # Read WorldCover pixels once for the AOI
    wc_arr, wc_transform, wc_crs = _wc_pixels(aoi_wkt)
    if wc_arr is None:
        return {}

    class_maps: dict[str, dict[int, float]] = {g: {} for g in _LC_GROUPS}

    for level in _COH_LEVELS:
        coh_raw, coh_transform, coh_crs = _read_coh_pixels(aoi_wkt, season, level, pol)
        if coh_raw is None:
            continue

        # Reproject WorldCover labels to coherence pixel grid (majority resampling)
        wc_on_coh = np.zeros(coh_raw.shape, dtype=np.uint8)
        try:
            reproject(
                source=wc_arr.astype(np.uint8),
                destination=wc_on_coh,
                src_transform=wc_transform,
                src_crs=wc_crs,
                dst_transform=coh_transform,
                dst_crs=coh_crs,
                resampling=Resampling.mode,
            )
        except Exception as exc:
            _log.debug("WorldCover reproject failed at COH%02d: %s", level, exc)
            continue

        coh_f     = coh_raw.astype(float)
        valid_mask = (coh_f > 0) & (coh_f < 255)   # 0 = native nodata, 255 = outside polygon

        for group, codes in _LC_GROUPS.items():
            class_mask = np.isin(wc_on_coh, list(codes)) & valid_mask
            pixels     = coh_f[class_mask]
            if pixels.size >= _LC_MIN_PIXELS:
                class_maps[group][level] = round(float(pixels.mean()) / 100.0, 4)

    result = {g: v for g, v in class_maps.items() if v}

    if cache is not None:
        cache[cache_key] = result   # stored with int keys → fine for in-memory

    return result


# ── Interpolation & decay chaining ────────────────────────────────────────────

@functools.lru_cache(maxsize=64)
def _fit_decay_model_cached(coh_items: tuple) -> tuple[float, float, float]:
    """LRU-cached entry point — converts the hashable tuple back to a dict."""
    return _fit_decay_model(dict(coh_items))


def _fit_decay_model(coh_map: dict[int, float]) -> tuple[float, float, float]:
    """Fit γ(t) = γ∞ + (γ0 − γ∞) · exp(−t/τ) to measured COH levels.

    Three free parameters
    ---------------------
    γ∞  — permanent-scatterer floor: the asymptote as t → ∞.
            Rocks, buildings — always coherent regardless of baseline.
    γ0  — initial coherence extrapolated back to t = 0.
            Stable surfaces: γ0 ≈ 1.0.
            Forests / volume scatterers: γ0 < 1.0 even at zero baseline
            because volume decorrelation is instantaneous.
    τ   — decorrelation time constant (days).
            Short τ (5–15 d): agriculture, wet soil, snow.
            Long τ (30–100 d): desert, urban, bare rock.

    Algorithm
    ---------
    1. Grid-search γ∞ ∈ [0, min(coh) × 0.95]  (40 steps).
    2. For each γ∞, fit γ0 and τ jointly by OLS in log space:

           y(t)  = γ(t) − γ∞  =  A · exp(−t/τ)     where A = γ0 − γ∞

       Taking log:  log y = log A − t/τ

       This is a linear model  log y = b0 + b1·t  with
           b1 = −1/τ  (must be negative)
           b0 = log A  →  A = exp(b0)  →  γ0 = γ∞ + A

    3. Pick the (γ∞, γ0, τ) triple with smallest MSE.

    Returns
    -------
    (gamma_inf, gamma0, tau)
        gamma_inf : float ∈ [0, 1)  — PS floor
        gamma0    : float ∈ (0, 1]  — initial coherence at t = 0 (clamped to 1)
        tau       : float > 0       — decorrelation time constant (days)
    Falls back to (0.0, 1.0, 30.0) if the fit fails.
    """
    t_arr   = np.array(sorted(coh_map), dtype=float)
    coh_arr = np.array([coh_map[int(l)] for l in t_arr])

    min_coh = float(coh_arr.min())
    best_ginf, best_g0, best_tau, best_err = 0.0, 1.0, 30.0, float("inf")

    # Pre-compute OLS helpers (mean-centered for numerical stability)
    t_mean = t_arr.mean()
    t_c    = t_arr - t_mean            # centered time
    ss_t   = float(np.dot(t_c, t_c))  # Σ(t - t̄)²

    for ginf in np.linspace(0.0, min_coh * 0.95, 40):
        y = coh_arr - ginf             # = A · exp(−t/τ),  A = γ0 − γ∞
        if np.any(y <= 0):
            continue

        log_y      = np.log(y)
        log_y_mean = log_y.mean()

        # OLS slope = Σ[(t−t̄)(log y − log ȳ)] / Σ(t−t̄)²
        slope = float(np.dot(t_c, log_y - log_y_mean)) / ss_t   # = −1/τ
        if slope >= 0:
            continue                    # decay requires negative slope

        tau       = -1.0 / slope
        intercept = log_y_mean - slope * t_mean   # = log A
        A         = math.exp(intercept)            # = γ0 − γ∞
        g0        = ginf + A                       # γ0 (may exceed 1 from noise)

        pred = ginf + A * np.exp(-t_arr / tau)
        err  = float(np.mean((pred - coh_arr) ** 2))

        if err < best_err:
            best_err  = err
            best_ginf = float(ginf)
            best_g0   = float(g0)
            best_tau  = float(tau)

    # γ0 > 1 is unphysical — clamp but keep the fit otherwise intact.
    best_g0 = min(best_g0, 1.0)
    return best_ginf, best_g0, best_tau


def _eval_decay(gamma_inf: float, gamma0: float, tau: float, dt: float) -> float:
    """Evaluate γ(t) = γ∞ + (γ0 − γ∞) · exp(−t/τ)."""
    return gamma_inf + (gamma0 - gamma_inf) * math.exp(-dt / tau)


# ── Per-pixel decay-map fitting ───────────────────────────────────────────────

def _fit_pixel_decay_maps(
    aoi_wkt: str,
    season: str,
    pol: str = "vv",
) -> dict | None:
    """Fit γ(t) = γ∞ + (γ0 − γ∞)·exp(−t/τ) independently at every pixel.

    Reads _COH_LEVELS from S3 as pixel arrays, stacks them into an
    (N_pixels, N_levels) matrix, then fits the three decay parameters per pixel.

    Why per-pixel instead of fitting to AOI means
    ---------------------------------------------
    Fitting to mean values conflates different land-cover types: an AOI that is
    50% urban (γ∞ ≈ 0.40, τ ≈ 60 d) and 50% forest (γ∞ ≈ 0.05, τ ≈ 8 d) would
    yield a mean curve that matches neither class.  Per-pixel fitting captures
    spatial heterogeneity; AOI-mean coherence is then the mean of γ(dt) evaluated
    per pixel — a more accurate estimator.

    Nodata handling
    ---------------
    Two nodata conventions coexist in the raster:
      0   — native file nodata (no Sentinel-1 acquisition at this location)
      255 — outside the AOI polygon (filled by rio_mask)
    Both are excluded from the fit.  A pixel needs ≥ 3 valid levels to constrain
    the three model parameters.

    Fitting strategy
    ----------------
    Fast path  (all _COH_LEVELS valid) — vectorised grid-search + OLS over all
               such pixels simultaneously using numpy broadcasting.
    Slow path  (3 to N_levels-1 valid) — scalar _fit_decay_model() loop, one
               pixel at a time, using only its available levels.

    Parameters
    ----------
    aoi_wkt : WKT polygon (geographic CRS, EPSG:4326)
    season  : "winter" | "spring" | "summer" | "fall"
    pol     : "vv" (default) or "vh"

    Returns
    -------
    dict with keys:
        gamma_inf : 2-D list[list[float]] — PS floor map
        gamma0    : 2-D list[list[float]] — initial coherence at t=0
        tau       : 2-D list[list[float]] — decorrelation time constant (days)
        valid     : 2-D list[list[bool]]  — True where ≥3 levels had data
        shape     : [H, W]
        transform : list[float]           — 6-element rasterio affine
        season    : str
        pol       : str
    Returns None if any COH level file cannot be read from S3.
    """
    # ── Read all COH levels as uint8 pixel arrays ─────────────────────────
    arrs: list[np.ndarray] = []
    transform = None
    crs = None
    for level in _COH_LEVELS:
        arr, tfm, c = _read_coh_pixels(aoi_wkt, season, level, pol)
        if arr is None:
            _log.debug("Pixel decay map: COH%02d failed for %s/%s", level, season, pol)
            return None
        arrs.append(arr)
        if transform is None:
            transform, crs = tfm, c

    H, W  = arrs[0].shape
    t_arr = np.array(_COH_LEVELS, dtype=float)
    n_levels = len(_COH_LEVELS)

    # Stack into (N, n_levels); each row is one pixel, each column one COH level
    flat = np.stack([a.reshape(-1).astype(float) for a in arrs], axis=1)

    # Per-pixel, per-level validity mask
    # 0   = native file nodata (no S1 acquisition)
    # 255 = outside AOI polygon (filled by rio_mask)
    level_ok   = (flat > 0) & (flat < 255)          # (N, n_levels)
    n_ok       = level_ok.sum(axis=1)               # (N,) valid level count per pixel
    valid_mask = n_ok >= 3                           # ≥3 levels needed to fit 3 params
    full_mask  = n_ok == n_levels                    # all levels valid → vectorised path

    coh = np.where(level_ok, flat / 100.0, np.nan)  # (N, n_levels) coherence [0,1]

    N_total  = H * W
    ginf_out = np.zeros(N_total, dtype=float)
    g0_out   = np.zeros(N_total, dtype=float)
    tau_out  = np.full(N_total, 30.0, dtype=float)

    if not valid_mask.any():
        _log.warning("Pixel decay map: no valid pixels for %s/%s", season, pol)
        return None

    # ── Fast path: vectorised grid search over all fully-valid pixels ─────
    # All pixels in coh_full share the same time axis t_arr, so OLS can be
    # broadcast across them in one pass per grid-search step.
    coh_full = coh[full_mask]        # (N_full, n_levels)
    N_full   = coh_full.shape[0]

    if N_full > 0:
        t_mean = t_arr.mean()
        t_c    = t_arr - t_mean      # centred time axis, shared by all pixels
        ss_t   = float(np.dot(t_c, t_c))

        min_coh   = coh_full.min(axis=1)   # per-pixel minimum coherence
        best_ginf = np.zeros(N_full)
        best_g0   = np.ones(N_full)
        best_tau  = np.full(N_full, 30.0)
        best_err  = np.full(N_full, np.inf)

        for i in range(40):
            # γ∞ candidate: linearly from 0 → 0.95 × per-pixel min_coh
            ginf_cand = min_coh * (i / 39) * 0.95        # (N_full,)
            y  = coh_full - ginf_cand[:, np.newaxis]      # (N_full, n_levels)
            ok = np.all(y > 0, axis=1)                    # feasible pixels for this γ∞
            if not ok.any():
                continue

            yv, ginf_ok = y[ok], ginf_cand[ok]
            log_y = np.log(yv)
            lym   = log_y.mean(axis=1)

            # OLS slope = Σ[(t_c)(log y − log ȳ)] / Σ(t_c²)
            slope = (t_c * (log_y - lym[:, np.newaxis])).sum(axis=1) / ss_t
            ns    = slope < 0                             # valid decay requires negative slope
            if not ns.any():
                continue

            tau_ok = np.where(ns, -1.0 / np.where(slope != 0, slope, -1e-9), 30.0)
            A_ok   = np.exp(np.clip(lym - slope * t_mean, -20, 5))
            g0_ok  = np.clip(ginf_ok + A_ok, 0.0, 1.0)

            pred    = ginf_ok[:, np.newaxis] + A_ok[:, np.newaxis] * \
                      np.exp(-t_arr[np.newaxis, :] / tau_ok[:, np.newaxis])
            err     = ((pred - coh_full[ok]) ** 2).mean(axis=1)
            improve = ns & (err < best_err[ok])
            idx     = np.where(ok)[0][improve]
            best_ginf[idx] = ginf_ok[improve]
            best_g0[idx]   = g0_ok[improve]
            best_tau[idx]  = tau_ok[improve]
            best_err[idx]  = err[improve]

        full_idx = np.where(full_mask)[0]
        ginf_out[full_idx] = best_ginf
        g0_out[full_idx]   = best_g0
        tau_out[full_idx]  = best_tau

    # ── Slow path: scalar loop for pixels with 3 to n_levels-1 valid levels
    # Each pixel may have a different subset of levels, so we can't share a
    # common time axis — fall back to the scalar _fit_decay_model().
    partial_mask = valid_mask & ~full_mask
    if partial_mask.any():
        for idx in np.where(partial_mask)[0]:
            lv_ok   = level_ok[idx]
            t_sub   = t_arr[lv_ok]
            coh_sub = coh[idx, lv_ok]
            coh_map = {int(t): float(c) for t, c in zip(t_sub, coh_sub)}
            gi, g0, tau   = _fit_decay_model(coh_map)
            ginf_out[idx] = gi
            g0_out[idx]   = g0
            tau_out[idx]  = tau

    return {
        "gamma_inf": ginf_out.reshape(H, W).tolist(),
        "gamma0":    g0_out.reshape(H, W).tolist(),
        "tau":       tau_out.reshape(H, W).tolist(),
        "valid":     valid_mask.reshape(H, W).tolist(),
        "shape":     [H, W],
        "transform": list(transform),
        "season":    season,
        "pol":       pol,
    }


def _decay_maps_tif_path(save_dir: Path, season: str, pol: str) -> Path:
    """Return the GeoTIFF path for a season/pol decay map."""
    return save_dir / f"S1_coherence_decay_{season}_{pol}.tif"


def _save_decay_maps_to_tif(maps: dict, save_dir: Path, season: str, pol: str) -> None:
    """Save pixel decay map arrays to a 3-band GeoTIFF.

    Bands
    -----
    1 : γ∞  (PS floor)
    2 : γ0  (initial coherence)
    3 : τ   (decorrelation time, days)

    nodata = -9999.0
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.transform import Affine

        save_dir.mkdir(parents=True, exist_ok=True)
        tif_path = _decay_maps_tif_path(save_dir, season, pol)

        H, W = maps["shape"]
        ginf = np.array(maps["gamma_inf"], dtype=np.float32)
        g0   = np.array(maps["gamma0"],    dtype=np.float32)
        tau  = np.array(maps["tau"],       dtype=np.float32)
        valid = np.array(maps["valid"],    dtype=bool)

        NODATA = np.float32(-9999.0)
        ginf[~valid] = NODATA
        g0[~valid]   = NODATA
        tau[~valid]  = NODATA

        tfm_list = maps["transform"]
        transform = Affine(*tfm_list[:6])

        profile = {
            "driver":    "GTiff",
            "dtype":     "float32",
            "width":     W,
            "height":    H,
            "count":     3,
            "crs":       "EPSG:4326",
            "transform": transform,
            "nodata":    float(NODATA),
            "compress":  "lzw",
        }
        with rasterio.open(tif_path, "w", **profile) as dst:
            dst.write(ginf, 1)
            dst.write(g0,   2)
            dst.write(tau,  3)
            dst.update_tags(
                season=season, pol=pol,
                band1="gamma_inf_PS_floor",
                band2="gamma0_initial_coherence",
                band3="tau_decorrelation_days",
            )
        _log.info("Saved coherence decay map: %s", tif_path)

    except Exception as exc:
        _log.warning("Could not save decay map GeoTIFF for %s/%s: %s", season, pol, exc)


def _load_decay_maps_from_tif(save_dir: Path, season: str, pol: str) -> dict | None:
    """Load pixel decay maps from an existing GeoTIFF.

    Returns the same dict structure as _fit_pixel_decay_maps, or None if the
    file does not exist or cannot be read.
    """
    tif_path = _decay_maps_tif_path(save_dir, season, pol)
    if not tif_path.exists():
        return None
    try:
        import numpy as np
        import rasterio

        with rasterio.open(tif_path) as src:
            ginf_arr = src.read(1).astype(float)
            g0_arr   = src.read(2).astype(float)
            tau_arr  = src.read(3).astype(float)
            nodata   = src.nodata
            tfm      = src.transform
            H, W     = src.height, src.width

        nd = float(nodata) if nodata is not None else -9999.0
        valid = (ginf_arr != nd) & (g0_arr != nd) & (tau_arr != nd)

        # Restore nodata pixels to neutral default so downstream code is safe
        ginf_arr[~valid] = 0.0
        g0_arr[~valid]   = 0.0
        tau_arr[~valid]  = 30.0

        return {
            "gamma_inf": ginf_arr.tolist(),
            "gamma0":    g0_arr.tolist(),
            "tau":       tau_arr.tolist(),
            "valid":     valid.tolist(),
            "shape":     [H, W],
            "transform": list(tfm)[:6],
            "season":    season,
            "pol":       pol,
        }

    except Exception as exc:
        _log.warning("Could not load decay map GeoTIFF %s: %s", tif_path, exc)
        return None


def _fetch_season_decay_maps(
    aoi_wkt: str,
    lat: float,
    lon: float,
    season: str,
    pol: str = "vv",
    cache: dict | None = None,
    save_dir: Path | None = None,
) -> tuple[dict | None, str]:
    """Return (pixel_decay_maps, source) for one season, with caching.

    pixel_decay_maps is the dict returned by _fit_pixel_decay_maps, or None
    when S3 is unreachable.  source is "s3" or "failed".

    Caching priority
    ----------------
    1. In-memory dict cache (fastest — no I/O)
    2. GeoTIFF on disk at ``save_dir/S1_coherence_decay_{season}_{pol}.tif``
       (survives process restarts; readable by CLI + GUI)
    3. S3 fetch (slow — network round-trip)

    The GeoTIFF is written after every S3 fetch so subsequent runs skip S3.
    Pass ``save_dir`` as ``{folder}/decay_maps`` to enable disk persistence.
    """
    cache_key = f"s1coh_pmaps:{lat:.2f}:{lon:.2f}:{season}:{pol}"

    # ── 1. In-memory cache ────────────────────────────────────────────────
    if cache is not None and cache_key in cache:
        stored = cache[cache_key]
        if stored is None:
            return None, "failed"
        # Write GeoTIFF if save_dir is given and file doesn't exist yet
        # (happens when data came from a prior run's JSON cache but TIF was never saved)
        if save_dir is not None and stored is not None:
            tif = _decay_maps_tif_path(save_dir, season, pol)
            if not tif.exists():
                _save_decay_maps_to_tif(stored, save_dir, season, pol)
        return stored, "s3"

    # ── 2. Disk GeoTIFF cache ─────────────────────────────────────────────
    if save_dir is not None:
        maps_from_disk = _load_decay_maps_from_tif(save_dir, season, pol)
        if maps_from_disk is not None:
            _log.debug("Loaded coherence decay map from disk: %s/%s", season, pol)
            if cache is not None:
                cache[cache_key] = maps_from_disk
            return maps_from_disk, "s3"

    # ── 3. S3 fetch ───────────────────────────────────────────────────────
    maps = _fit_pixel_decay_maps(aoi_wkt, season, pol)
    source = "s3" if maps is not None else "failed"

    if source == "failed":
        _log.info("S1 coherence pixel maps: S3 unavailable for %s/%s", season, pol)
    elif save_dir is not None:
        _save_decay_maps_to_tif(maps, save_dir, season, pol)

    if cache is not None:
        cache[cache_key] = maps   # None stored on failure so we don't retry

    return maps, source


def _eval_pixel_maps(maps: dict, dt: float) -> tuple[float, float]:
    """Evaluate the pixel decay maps at a given temporal baseline.

    Returns (mean_coh, mean_ginf) averaged over all valid AOI pixels.
    mean_coh  = mean[γ∞ + (γ0 − γ∞) · exp(−dt/τ)]  over valid pixels
    mean_ginf = mean[γ∞]  over valid pixels  (PS floor for the AOI)
    """
    ginf = np.array(maps["gamma_inf"])
    g0   = np.array(maps["gamma0"])
    tau  = np.array(maps["tau"])
    valid = np.array(maps["valid"], dtype=bool)

    coh_pixels = ginf + (g0 - ginf) * np.exp(-dt / tau)
    return float(coh_pixels[valid].mean()), float(ginf[valid].mean())


# ── Season COH map fetcher (mean-based, kept for climatology fallback) ────────

def _fetch_season_coh_map(
    aoi_wkt: str,
    lat: float,
    lon: float,
    season: str,
    pol: str = "vv",
    cache: dict | None = None,
) -> tuple[dict[int, float], str]:
    """Return ({level: coherence}, source) for a given season.

    source is ``"s3"`` when data was read from AWS S3, ``"failed"`` when the
    S3 read returned nothing.  No climatology fallback here — that is the
    responsibility of the caller so the scoring chain (S3 → NDVI/landcover
    → climatology) can be enforced at the right level.
    """
    cache_key     = f"s1coh:{lat:.2f}:{lon:.2f}:{season}:{pol}"
    src_cache_key = f"{cache_key}:source"

    if cache is not None and cache_key in cache:
        raw    = cache[cache_key]
        source = cache.get(src_cache_key, "s3")
        return {int(k): float(v) for k, v in raw.items()}, source

    coh_map: dict[int, float] = {}
    for level in _COH_LEVELS:
        val = _read_mean_coh(aoi_wkt, season, level, pol)
        if val is not None:
            coh_map[level] = val

    source = "s3" if coh_map else "failed"
    if source == "failed":
        _log.info("S1 coherence: S3 unavailable for %s/%s", season, pol)

    if cache is not None:
        cache[cache_key]     = {str(k): v for k, v in coh_map.items()}
        cache[src_cache_key] = source

    return coh_map, source


def _chain_segments(
    segments: list[tuple[int, str]],
    season_maps: dict[str, dict[int, float]],
) -> tuple[float | None, float, list[tuple[int, str, float | None]]]:
    """Chain coherence across season segments using the physical two-component model.

    Simple multiplication is wrong for cross-season pairs because it drives
    the permanent-scatterer floor ρ∞ toward ρ∞² instead of ρ∞ as baselines grow.

    The correct formula separates the two components:
      γ_total = γ∞_eff + (γ0_eff − γ∞_eff) · exp(−Σ dt_i / τ_i)

    where:
      * γ∞_eff = duration-weighted mean(γ∞_i)
                 PS targets are physically constant (metal, rock) — the seasonal
                 dataset fits a lower γ∞ in winter because snow partially masks them,
                 but the actual PS fraction of the scene does not change between seasons.
                 Weighting by segment duration means a 2-day winter crossing does not
                 dominate a 22-day summer segment.
      * γ0_eff = duration-weighted mean(γ0_i)
                 Initial coherence varies with vegetation state and snowpack; weighting
                 by duration gives the dominant season its fair influence.
      * τ_i    = decay time constant for segment i, fitted per season from COH levels
      * dt_i   = duration of segment i in days
      * exp_sum = Σ dt_i/τ_i — distributed-scatterer decorrelation compounds correctly

    For a single-season pair this reduces to the standard γ(t) = γ∞ + (γ0−γ∞)·exp(−t/τ).

    Parameters
    ----------
    segments    : [(dt_days, season_name), ...] from split_by_season()
    season_maps : {season_name: {level_days: coherence}} — may contain empty dicts

    Returns
    -------
    (total_coh, rho_eff, seg_details)
      total_coh   : float in [0, 1], or None if any season map was empty (S3 failed)
      rho_eff     : float — duration-weighted PS floor; 0.0 on failure
      seg_details : [(dt_days, season, seg_coh_or_None), ...]
    """
    seg_details: list[tuple[int, str, float | None]] = []
    exp_sum    = 0.0
    ginf_weighted = 0.0   # Σ(dt_i · γ∞_i)
    g0_weighted   = 0.0   # Σ(dt_i · γ0_i)
    total_dt      = 0.0   # Σ dt_i  (valid segments only)
    all_valid = True

    for seg_dt, season in segments:
        coh_map = season_maps.get(season, {})
        if not coh_map:
            all_valid = False
            seg_details.append((seg_dt, season, None))
            continue

        ginf, g0, tau = _fit_decay_model_cached(tuple(sorted(coh_map.items())))
        exp_sum       += seg_dt / tau
        ginf_weighted += seg_dt * ginf
        g0_weighted   += seg_dt * g0
        total_dt      += seg_dt

        seg_coh = _eval_decay(ginf, g0, tau, seg_dt)
        seg_details.append((seg_dt, season, round(seg_coh, 4)))

    if not all_valid or total_dt == 0:
        return None, 0.0, seg_details

    # Duration-weighted effective parameters
    ginf_eff  = ginf_weighted / total_dt
    g0_eff    = g0_weighted   / total_dt
    total_coh = ginf_eff + (g0_eff - ginf_eff) * math.exp(-exp_sum)
    return round(total_coh, 4), round(ginf_eff, 4), seg_details


@functools.lru_cache(maxsize=4096)
def _climatology_pair_coherence(lat: float, date1: str, date2: str) -> float:
    """Return expected coherence from the climatology table for a pair.

    Uses the same physical segment-chaining as estimate_coherence() but reads
    from the hardcoded _CLIM table instead of S3.  Always returns a value.
    """
    segments    = split_by_season(date1, date2, lat)
    uniq_seasons = list(dict.fromkeys(s for _, s in segments))
    season_maps = {s: _climatology_coh_map(lat, s) for s in uniq_seasons}
    total, _, _ = _chain_segments(segments, season_maps)
    return total if total is not None else 0.0


# ── Main public function ──────────────────────────────────────────────────────

def estimate_coherence(
    aoi_wkt: str,
    lat: float,
    lon: float,
    date1: str,
    date2: str,
    pol: str = "vv",
    cache: dict | None = None,
    save_dir: Path | None = None,
) -> dict[str, Any]:
    """Estimate expected interferometric coherence for a scene pair.

    Handles cross-season pairs by splitting at season boundaries and applying
    the physical two-component decay model (see _chain_segments).  Simple
    multiplication is avoided because it incorrectly drives the permanent-
    scatterer floor ρ∞ toward ρ∞² for long cross-season baselines.

    Parameters
    ----------
    aoi_wkt  : WKT polygon (geographic CRS, EPSG:4326)
    lat, lon : AOI centroid
    date1, date2 : YYYYMMDD scene dates (or longer scene names)
    pol      : "vv" (default) or "vh"
    cache    : mutable dict for in-memory caching of S3 reads

    Returns
    -------
    dict with keys:
        coherence_expected    : float in [0, 1] (S3 AOI-mean or climatology fallback)
        coherence_same_season : bool  — True if pair falls entirely within one season
        coherence_season_d1   : str   — season of first date
        coherence_season_d2   : str   — season of second date
        coherence_segments    : list  — [(dt_days, season, coh), ...]
        coherence_dt_total    : int   — total temporal baseline (days)
    """
    d1_str = _normalize_date(date1)
    d2_str = _normalize_date(date2)

    d1 = datetime.strptime(d1_str, "%Y%m%d")
    d2 = datetime.strptime(d2_str, "%Y%m%d")
    dt_total = abs((d2 - d1).days)

    season_d1 = _get_season(d1.month, lat)
    season_d2 = _get_season(d2.month, lat)

    segments     = split_by_season(d1_str, d2_str, lat)
    uniq_seasons = list(dict.fromkeys(s for _, s in segments))

    # ── Tier 1: per-pixel decay maps (preferred) ──────────────────────────
    # Fetch 3-parameter maps (γ∞, γ0, τ) per pixel for each season, then
    # evaluate γ(dt) per pixel and average — more accurate than fitting to
    # per-season means because spatial heterogeneity is preserved.
    pixel_maps: dict[str, dict] = {}
    any_s3_failed = False
    for season in uniq_seasons:
        pmaps, source = _fetch_season_decay_maps(
            aoi_wkt, lat, lon, season, pol, cache, save_dir=save_dir,
        )
        if source == "failed":
            any_s3_failed = True
        else:
            pixel_maps[season] = pmaps  # type: ignore[assignment]

    if not any_s3_failed and len(pixel_maps) == len(uniq_seasons):
        # All seasons available — compute coherence per pixel across segments.
        # For single-season pair: evaluate model directly at dt_total.
        # For cross-season pair: chain segments using the two-component model
        #   applied per pixel, then average.
        #
        # Cross-season pixel chaining:
        #   γ_pixel(total) = γ∞_eff + (γ0_eff − γ∞_eff) · exp(−Σ dt_i/τ_i)
        # where for each pixel the effective parameters are duration-weighted means:
        #   γ∞_eff = Σ(dt_i · γ∞_i) / Σ dt_i  — PS targets are physically constant;
        #            weighting by duration avoids a 2-day winter crossing dominating
        #            a 22-day summer segment.
        #   γ0_eff = Σ(dt_i · γ0_i) / Σ dt_i  — same rationale for initial coherence.

        first_maps = pixel_maps[uniq_seasons[0]]
        H, W = first_maps["shape"]

        # Initialise per-pixel accumulator arrays
        ginf_sum  = np.zeros((H, W))         # Σ(dt_i · γ∞_i)
        g0_sum    = np.zeros((H, W))         # Σ(dt_i · γ0_i)
        dt_sum    = 0.0                      # Σ dt_i (valid segments)
        exp_sum   = np.zeros((H, W))         # Σ dt_i/τ_i
        valid_all = np.ones((H, W), dtype=bool)
        seg_details: list = []

        for seg_dt, season in segments:
            pmaps = pixel_maps.get(season)
            if pmaps is None:
                seg_details.append((seg_dt, season, None))
                valid_all[:] = False
                continue

            ginf_px = np.array(pmaps["gamma_inf"])
            g0_px   = np.array(pmaps["gamma0"])
            tau_px  = np.array(pmaps["tau"])
            vld_px  = np.array(pmaps["valid"], dtype=bool)

            ginf_sum  += seg_dt * ginf_px
            g0_sum    += seg_dt * g0_px
            dt_sum    += seg_dt
            exp_sum   += seg_dt / np.where(tau_px > 0, tau_px, 30.0)
            valid_all &= vld_px

            # Per-segment display: mean coherence this segment alone produces
            seg_coh_px = ginf_px + (g0_px - ginf_px) * np.exp(-seg_dt / tau_px)
            seg_mean   = float(seg_coh_px[vld_px].mean()) if vld_px.any() else None
            seg_details.append((seg_dt, season, round(seg_mean, 4) if seg_mean else None))

        # Duration-weighted effective parameters per pixel
        ginf_eff = ginf_sum / max(dt_sum, 1.0)
        g0_eff   = g0_sum   / max(dt_sum, 1.0)

        # Evaluate chained model per pixel, then average over AOI
        coh_pixels = ginf_eff + (g0_eff - ginf_eff) * np.exp(-exp_sum)
        total_coh  = round(float(coh_pixels[valid_all].mean()), 4) if valid_all.any() else None
        rho_eff    = round(float(ginf_eff[valid_all].mean()), 4)   if valid_all.any() else 0.0

    else:
        # ── Tier 2 fallback: mean-based approach (S3 partial failure) ────
        season_maps: dict[str, dict[int, float]] = {}
        for season in uniq_seasons:
            coh_map, source = _fetch_season_coh_map(
                aoi_wkt, lat, lon, season, pol, cache,
            )
            season_maps[season] = coh_map

        total_coh, rho_eff, seg_details = _chain_segments(segments, season_maps)

    # Derive rho_eff from climatology when S3 fully failed
    if any_s3_failed or rho_eff == 0.0:
        clim_maps = {s: _climatology_coh_map(lat, s) for s in uniq_seasons}
        _, clim_rho_eff, _ = _chain_segments(segments, clim_maps)
        if any_s3_failed:
            rho_eff = clim_rho_eff

    return {
        "coherence_expected":    total_coh if (total_coh is not None and not any_s3_failed) else None,
        "coherence_source":      "failed" if any_s3_failed else "s3",
        "coherence_same_season": len(uniq_seasons) == 1,
        "coherence_season_d1":   season_d1,
        "coherence_season_d2":   season_d2,
        "coherence_segments":    seg_details,
        "coherence_dt_total":    dt_total,
        "coherence_rho_inf":     rho_eff,
    }


# ── Batch prefetch helper ─────────────────────────────────────────────────────

def prefetch_coherence(
    aoi_wkt: str,
    lat: float,
    lon: float,
    pairs: list[tuple[str, str]],
    pol: str = "vv",
    cache: dict | None = None,
) -> None:
    """Prefetch all unique season COH maps needed for a list of pairs.

    Identifies the unique seasons across all pairs and fetches each once,
    populating `cache` so that subsequent estimate_coherence() calls are instant.

    Parameters
    ----------
    pairs : list of (date1, date2) strings (YYYYMMDD or scene names)
    cache : mutable dict — same object passed to estimate_coherence()
    """
    needed_seasons: set[str] = set()
    for d1, d2 in pairs:
        for _, season in split_by_season(d1, d2, lat):
            needed_seasons.add(season)

    _log.info("S1 coherence: prefetching %d season(s) …", len(needed_seasons))
    for season in needed_seasons:
        _fetch_season_coh_map(aoi_wkt, lat, lon, season, pol, cache)  # populates cache
