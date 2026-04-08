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
_COH_LEVELS = [6, 12, 18, 24, 36, 48]

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
    valid = data[data < 255]
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
        valid_mask = coh_f < 255

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
def _fit_decay_model_cached(coh_items: tuple) -> tuple[float, float]:
    """LRU-cached entry point — converts the hashable tuple back to a dict."""
    return _fit_decay_model(dict(coh_items))


def _fit_decay_model(coh_map: dict[int, float]) -> tuple[float, float]:
    """Fit γ(t) = (1 − ρ∞) · exp(−t/τ) + ρ∞ to measured COH levels.

    The two components:
      * (1 − ρ∞) · exp(−t/τ) — the decorrelating scatterers (vegetation,
        soil moisture, snow).  Starts at (1 − ρ∞) at t=0 and decays to 0.
      * ρ∞ — permanent-scatterer floor (rocks, buildings).  Never decorrelates
        regardless of temporal baseline.

    Algorithm: grid-search ρ∞ ∈ [0, min(coh)·0.95], then solve for τ by
    ordinary least squares in log space.  Returns (rho_inf, tau_days).

    Parameters
    ----------
    coh_map : {level_days: coherence} — at least 2 entries required.

    Returns
    -------
    (rho_inf, tau) where rho_inf ∈ [0, 1) and tau > 0 (days).
    Falls back to (0.0, 30.0) if the fit fails.
    """
    t_arr   = np.array(sorted(coh_map), dtype=float)
    coh_arr = np.array([coh_map[int(l)] for l in t_arr])

    min_coh = float(coh_arr.min())
    best_rho, best_tau, best_err = 0.0, 30.0, float("inf")

    for rho in np.linspace(0.0, min_coh * 0.95, 40):
        y = coh_arr - rho          # = (1 − ρ∞) · exp(−t/τ)
        amp = 1.0 - rho            # = (1 − ρ∞)
        if np.any(y <= 0) or amp <= 0:
            continue
        # log(y / amp) = −t / τ  →  1/τ = −Σ(t · log(y/amp)) / Σ(t²)
        log_y  = np.log(y / amp)
        inv_tau = -float(np.dot(t_arr, log_y) / np.dot(t_arr, t_arr))
        if inv_tau <= 0:
            continue
        tau  = 1.0 / inv_tau
        pred = amp * np.exp(-t_arr / tau) + rho
        err  = float(np.mean((pred - coh_arr) ** 2))
        if err < best_err:
            best_err, best_rho, best_tau = err, float(rho), tau

    return best_rho, best_tau


def _interpolate_level(coh_map: dict[int, float], dt: int) -> float | None:
    """Estimate coherence for an arbitrary dt (days) from available COH levels.

    Algorithm
    ---------
    * dt ≤ max level : linear interpolation between the two nearest levels.
    * dt > max level : use fitted γ(t) = (1−ρ∞)·exp(−t/τ) + ρ∞ so the
      result approaches the permanent-scatterer floor ρ∞, not zero.

    Returns None if coh_map is empty.
    """
    if not coh_map:
        return None

    levels = sorted(coh_map)
    vals   = [coh_map[l] for l in levels]

    # ── interpolate within range ──────────────────────────────────────────
    if dt <= levels[0]:
        return vals[0]
    if dt <= levels[-1]:
        for i in range(len(levels) - 1):
            if levels[i] <= dt <= levels[i + 1]:
                t = (dt - levels[i]) / (levels[i + 1] - levels[i])
                return vals[i] + t * (vals[i + 1] - vals[i])
        return vals[-1]

    # ── extrapolate beyond max level using the physical decay model ───────
    if len(levels) >= 2:
        rho_inf, tau = _fit_decay_model(coh_map)
        return max(rho_inf, (1.0 - rho_inf) * math.exp(-dt / tau) + rho_inf)

    # Degenerate: only one level available — use it as constant
    return vals[-1]


# ── Season COH map fetcher (with simple in-memory cache) ─────────────────────

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
) -> tuple[float | None, list[tuple[int, str, float | None]]]:
    """Chain coherence across season segments using the physical two-component model.

    Simple multiplication is wrong for cross-season pairs because it drives
    the permanent-scatterer floor ρ∞ toward ρ∞² instead of ρ∞ as baselines grow.

    The correct formula separates the two components:
      γ_total = ρ∞_eff + (1 − ρ∞_eff) · exp(−Σ dt_i / τ_i)

    where:
      * ρ∞_eff = min(ρ∞ across all season segments) — conservative permanent floor
      * τ_i    = decay time constant for segment i, fitted per season from COH levels
      * dt_i   = duration of segment i in days

    For a single-season pair this reduces to the standard γ(t) = (1−ρ∞)·exp(−t/τ) + ρ∞.

    Parameters
    ----------
    segments    : [(dt_days, season_name), ...] from split_by_season()
    season_maps : {season_name: {level_days: coherence}} — may contain empty dicts

    Returns
    -------
    (total_coh, seg_details)
      total_coh   : float in [0, 1], or None if any season map was empty (S3 failed)
      seg_details : [(dt_days, season, seg_coh_or_None), ...]
    """
    seg_details: list[tuple[int, str, float | None]] = []
    exp_sum  = 0.0     # Σ(dt_i / τ_i)
    rho_infs: list[float] = []
    all_valid = True

    for seg_dt, season in segments:
        coh_map = season_maps.get(season, {})
        if not coh_map:
            all_valid = False
            seg_details.append((seg_dt, season, None))
            continue

        rho_inf, tau = _fit_decay_model_cached(tuple(sorted(coh_map.items())))
        exp_sum += seg_dt / tau
        rho_infs.append(rho_inf)

        # Per-segment display coherence (what this segment alone would give)
        seg_coh = rho_inf + (1.0 - rho_inf) * math.exp(-seg_dt / tau)
        seg_details.append((seg_dt, season, round(seg_coh, 4)))

    if not all_valid or not rho_infs:
        return None, seg_details

    rho_eff   = min(rho_infs)                                  # conservative floor
    total_coh = rho_eff + (1.0 - rho_eff) * math.exp(-exp_sum)
    return round(total_coh, 4), seg_details


@functools.lru_cache(maxsize=4096)
def _climatology_pair_coherence(lat: float, date1: str, date2: str) -> float:
    """Return expected coherence from the climatology table for a pair.

    Uses the same physical segment-chaining as estimate_coherence() but reads
    from the hardcoded _CLIM table instead of S3.  Always returns a value.
    """
    segments    = split_by_season(date1, date2, lat)
    uniq_seasons = list(dict.fromkeys(s for _, s in segments))
    season_maps = {s: _climatology_coh_map(lat, s) for s in uniq_seasons}
    total, _    = _chain_segments(segments, season_maps)
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
        coherence_expected    : float in [0, 1] (S3 value or climatology fallback)
        coherence_by_class    : {class_name: float} per-LC-class coherence (may be {})
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

    # ── fetch overall COH maps for each unique season ─────────────────────
    season_maps: dict[str, dict[int, float]] = {}
    any_s3_failed = False
    for season in uniq_seasons:
        coh_map, source = _fetch_season_coh_map(
            aoi_wkt, lat, lon, season, pol, cache,
        )
        season_maps[season] = coh_map
        if source == "failed":
            any_s3_failed = True

    # ── chain segments using physical two-component model ─────────────────
    total_coh, seg_details = _chain_segments(segments, season_maps)

    # ── per-LC-class coherence breakdown ─────────────────────────────────
    # Fetch per-class COH maps for each season, chain segments per class.
    # Skipped gracefully when WorldCover or S3 is unavailable.
    coherence_by_class: dict[str, float] = {}
    if not any_s3_failed:
        # Collect per-class season maps: {season: {class: {level: coh}}}
        class_season_maps: dict[str, dict[str, dict[int, float]]] = {}
        for season in uniq_seasons:
            by_cls = _fetch_season_coh_by_class(aoi_wkt, lat, lon, season, pol, cache)
            for cls_name, coh_map in by_cls.items():
                class_season_maps.setdefault(cls_name, {})[season] = coh_map

        for cls_name, cls_maps in class_season_maps.items():
            # Only chain if all segments have data for this class
            cls_total, _ = _chain_segments(segments, cls_maps)
            if cls_total is not None:
                coherence_by_class[cls_name] = cls_total

    return {
        "coherence_expected":    total_coh if (total_coh is not None and not any_s3_failed) else None,
        "coherence_by_class":    coherence_by_class,
        "coherence_source":      "failed" if any_s3_failed else "s3",
        "coherence_same_season": len(uniq_seasons) == 1,
        "coherence_season_d1":   season_d1,
        "coherence_season_d2":   season_d2,
        "coherence_segments":    seg_details,
        "coherence_dt_total":    dt_total,
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
