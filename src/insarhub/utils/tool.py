#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import requests
import time
import logging
import zipfile
import shutil

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from dateutil.parser import isoparse
from pathlib import Path
from typing import Optional, Union, List, Dict
from threading import Lock

import geopandas as gpd
import matplotlib
matplotlib.use('Agg')   # non-interactive backend — required for background-thread plotting
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx
import numpy as np
import rasterio
from rasterio.mask import mask
from asf_search import ASFProduct, ASFSearchError
from asf_search.baseline.calc import calculate_perpendicular_baselines
from colorama import Fore
from shapely.geometry import box
from shapely import wkt
from tqdm import tqdm


logger = logging.getLogger(__name__)

# Sentinel value used when a baseline cannot be determined.
# Large enough to fail every filter condition.
_MISSING: float = 10_000.0
_WKT_MAX_LEN = 2000

# ═══════════════════════════════════════════════════════════════════════════
#  TYPE ALIASES
# ═══════════════════════════════════════════════════════════════════════════

SceneID   = str
DateFloat = float   # Unix timestamp (seconds)
Pair      = tuple[SceneID, SceneID]
BaselineEntry = tuple[float, float]   # (dt_days, bperp_m)
BaselineTable = dict[Pair, BaselineEntry]
PairGroup = dict[tuple[int, int], list[Pair]]


# ═══════════════════════════════════════════════════════════════════════════
#  WORKFLOW MARKER
# ═══════════════════════════════════════════════════════════════════════════

_CONFIG_FILE = "insarhub_config.json"

def write_workflow_marker(workdir: Path, **roles: str) -> None:
    """Write or update role types in ``insarhub_config.json`` in *workdir*.

    Each keyword argument is a role→class-name pair, e.g.::

        write_workflow_marker(self.output_dir, processor="Hyp3_S1")
        write_workflow_marker(self.workdir,    analyzer="Hyp3_SBAS")

    Existing entries are preserved so the file accumulates as the pipeline runs.
    """
    path = Path(workdir) / _CONFIG_FILE
    try:
        existing: dict = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        existing = {}
    for role, cls_name in roles.items():
        section = existing.setdefault(role, {})
        section["type"] = cls_name
    existing["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    try:
        path.write_text(json.dumps(existing, indent=2, default=str))
    except Exception as exc:
        logger.warning("Could not write %s: %s", path, exc)


# ═══════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _has_local_baseline(p: ASFProduct) -> bool:
    """
    Return True if *p* carries enough on-product data to compute bperp
    locally without calling the ASF API.

    Sentinel-1 (CALCULATED type):
        p.baseline['stateVectors']['positions'] and ['velocities'] must exist.

    ALOS / ERS / RADARSAT (PRE_CALCULATED type):
        p.baseline['insarBaseline'] (a scalar float) must exist.
    """
    b = getattr(p, "baseline", None)
    if not b:
        return False
    if "stateVectors" in b:
        sv = b["stateVectors"]
        return bool(sv.get("positions") and sv.get("velocities"))
    if "insarBaseline" in b:
        return True
    return False


def _fetch_stack_with_retry(
    ref: ASFProduct,
    max_attempts: int = 10,
) -> tuple[SceneID, list[ASFProduct]]:
    """
    Fetch the ASF stack for *ref* with exponential-backoff retry.

    Returns (scene_name, stack_products).
    Raises ASFSearchError after *max_attempts* consecutive failures.
    """
    rid = ref.properties["sceneName"]
    for attempt in range(1, max_attempts + 1):
        try:
            return rid, ref.stack()
        except ASFSearchError:
            if attempt == max_attempts:
                logger.error(
                    "Stack fetch failed for %s after %d attempts.", rid, max_attempts
                )
                raise
            wait = 0.5 * 2 ** (attempt - 1)
            logger.debug(
                "Attempt %d failed for %s; retrying in %.1f s.", attempt, rid, wait
            )
            time.sleep(wait)
    raise ASFSearchError(f"Unreachable: failed to fetch stack for {rid}")


def _build_baseline_table_local(
    prods: list[ASFProduct],
    ids: set[SceneID],
    id_time_dt: dict[SceneID, DateFloat],
) -> tuple[BaselineTable, dict]:
    """
    Compute the full pairwise (dt_days, bperp_m) table from data already
    stored on each ASFProduct — **zero network calls**.

    Algorithm
    ---------
    1. Call ``calculate_perpendicular_baselines(reference=prods[0], secondaries=prods)``
       once.  This is the same function asf_search uses internally inside
       ``ref.stack()``, but we call it directly so no HTTP request is made.
       It returns bperp for every scene relative to ``prods[0]`` as the anchor.

    2. Pairwise bperp between scenes A and B equals
       ``|bp_vector[A] - bp_vector[B]|``
       because perpendicular baseline is a linear function of orbital
       separation and the common anchor cancels out.

    3. Temporal baseline is computed directly from pre-parsed Unix timestamps.
    """
    B: BaselineTable = {}
    if not prods:
        return B

    try:
        anchored = calculate_perpendicular_baselines(
            reference=prods[0].properties['sceneName'],
            stack=prods,
        )
        bp_vector: dict[SceneID, float | None] = {
            p.properties["sceneName"]: p.properties.get("perpendicularBaseline")
            for p in anchored
        }
        # The reference scene itself is not returned with a self-baseline by
        # calculate_perpendicular_baselines; inject it as 0.0 so pairs
        # involving the anchor are not discarded.
        ref_name = prods[0].properties["sceneName"]
        if ref_name not in bp_vector:
            bp_vector[ref_name] = 0.0
    except Exception as exc:
        logger.warning(
            "Local baseline calculation failed (%s); table will be empty.", exc
        )
        return B, {}

    for i, a in enumerate(prods):
        for b in prods[i + 1:]:
            aid = a.properties["sceneName"]
            bid = b.properties["sceneName"]
            if aid not in ids or bid not in ids:
                continue
            # Temporal baseline in days
            dt = abs(id_time_dt[bid] - id_time_dt[aid]) / 86_400.0
            # Pairwise bperp = |bp_relative_to_anchor[B] - bp_relative_to_anchor[A]|
            bp_a, bp_b = bp_vector.get(aid), bp_vector.get(bid)
            bp = (
                abs(bp_b - bp_a)
                if (bp_a is not None and bp_b is not None)
                else _MISSING
            )

            early, late = (
                (aid, bid) if id_time_dt[aid] <= id_time_dt[bid] else (bid, aid)
            )
            B[(early, late)] = (dt, bp)

    logger.info(
        "Local baseline table: %d pairs from %d scenes.", len(B), len(prods)
    )
    # Return signed per-scene bperp (relative to anchor prods[0]) alongside the table
    scene_bperp: dict[SceneID, float] = {
        k: float(v) for k, v in bp_vector.items() if v is not None
    }
    return B, scene_bperp


def _build_baseline_table_api(
    prods: list[ASFProduct],
    ids: set[SceneID],
    id_time_dt: dict[SceneID, DateFloat],
    max_workers: int,
) -> BaselineTable:
    """
    Fallback: fetch baselines via ``ref.stack()`` in a thread pool.

    Only called for products that are missing local baseline data.
    Threads are used because ``ref.stack()`` is network-bound (the GIL is
    released during I/O, so threads genuinely run concurrently).

    Lock strategy: each thread builds a local dict without any locking, then
    acquires the shared lock once to batch-write — minimising contention.
    ``setdefault`` ensures the first writer wins on any race.
    """
    B: BaselineTable = {}
    B_lock = Lock()

    def _process_ref(ref: ASFProduct) -> int:
        rid, stacks = _fetch_stack_with_retry(ref)
        local: BaselineTable = {}

        for sec in stacks:
            sid = sec.properties["sceneName"]
            if sid not in ids or sid == rid:
                continue
            a, b = (
                (rid, sid) if id_time_dt[rid] <= id_time_dt[sid] else (sid, rid)
            )
            if (a, b) in B:
                continue
            dt = sec.properties.get("temporalBaseline")
            bp = sec.properties.get("perpendicularBaseline")
            local[(a, b)] = (
                abs(dt) if dt is not None else _MISSING,
                abs(bp) if bp is not None else _MISSING,
            )

        if local:
            with B_lock:
                for k, v in local.items():
                    B.setdefault(k, v)   # first writer wins; values are identical

        return len(local)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process_ref, ref): ref for ref in prods}
        with tqdm(
            as_completed(futures),
            total=len(futures),
            desc="Fetching stacks (API fallback)",
            unit="scene",
        ) as bar:
            for fut in bar:
                ref = futures[fut]
                try:
                    n = fut.result()
                    bar.set_postfix(
                        pairs=len(B),
                        new=n,
                        scene=ref.properties["sceneName"][-10:],
                    )
                except Exception as exc:
                    logger.error(
                        "Error processing %s: %s",
                        ref.properties["sceneName"], exc,
                    )
                    raise

    logger.info(
        "API baseline table: %d pairs from %d scenes.", len(B), len(prods)
    )
    return B


def _build_baseline_table(
    prods: list[ASFProduct],
    ids: set[SceneID],
    id_time_dt: dict[SceneID, DateFloat],
    max_workers: int,
) -> tuple[BaselineTable, dict]:
    """
    Route each product to the fastest available baseline source.

    - Products with stateVectors or insarBaseline  →  local (no network)
    - Products missing that data                   →  API fallback (threaded)

    Returns (BaselineTable, scene_bperp) where scene_bperp maps scene name
    to signed perpendicular baseline relative to the anchor scene.
    """
    local_prods = [p for p in prods if _has_local_baseline(p)]
    api_prods   = [p for p in prods if not _has_local_baseline(p)]

    if not api_prods:
        logger.info(
            "All %d products have local baseline data — no API calls needed.",
            len(prods),
        )
    else:
        logger.warning(
            "%d / %d products missing local baseline data — API fallback for those.",
            len(api_prods), len(prods),
        )

    B: BaselineTable = {}
    scene_bperp: dict = {}

    if local_prods:
        local_B, local_bp = _build_baseline_table_local(local_prods, ids, id_time_dt)
        B.update(local_B)
        scene_bperp.update(local_bp)

    if api_prods:
        B.update(_build_baseline_table_api(api_prods, ids, id_time_dt, max_workers))

    return B, scene_bperp


def _download_isce_stacktool(base: Path) -> Path:
    """Download topsStack from GitHub into ``{base}/share/isce2/topsStack/``.

    Args:
        base: The ISCE2 environment root (e.g. the conda env prefix or ISCE_HOME).

    Returns:
        Path to the downloaded ``stackSentinel.py``.

    Raises:
        RuntimeError: If the GitHub API is unreachable or the download fails.
    """
    target_dir = Path(base) / "components" / "contrib" / "stack" / "topsStack"
    target_dir = target_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    api_url = "https://api.github.com/repos/isce-framework/isce2/contents/contrib/stack/topsStack"
    headers = {"User-Agent": "insarhub/1.0"}

    print(f"  topsStack not found — downloading from GitHub into {target_dir} …")
    response = requests.get(api_url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub API returned status {response.status_code}. "
            "Check your internet connection or download topsStack manually from "
            "https://github.com/isce-framework/isce2/tree/main/contrib/stack/topsStack"
        )

    files_to_download = [item for item in response.json() if item["type"] == "file"]
    for file_info in tqdm(files_to_download, desc="Downloading topsStack", unit="file"):
        file_data = requests.get(file_info["download_url"], headers=headers).content
        (target_dir / file_info["name"]).write_bytes(file_data)

    sentinel = target_dir / "stackSentinel.py"
    if not sentinel.exists():
        raise RuntimeError(
            f"Download completed but stackSentinel.py not found in {target_dir}."
        )
    print(f"  topsStack ready → {target_dir}")
    return sentinel

def _enforce_connectivity(
    pairs: set[Pair],
    B: BaselineTable,
    names: list[SceneID],
    id_time_dt: dict[SceneID, DateFloat],
    min_degree: int,
    max_degree: int,
    pb_max: float,
    dt_max: float,
    force_connect: bool
) -> set[Pair]:
    """
    Enforce min_degree / max_degree connectivity on the interferogram graph.

    Step A — boost under-connected scenes
        For each scene with fewer than *min_degree* connections, add the
        nearest-time neighbours that satisfy *pb_max* and *dt_max* until
        the scene reaches *min_degree* or candidates are exhausted.

    Step B — trim over-connected scenes
        For each scene exceeding *max_degree*, remove the worst pair
        (highest dt, then highest bperp) as long as doing so does not drop
        the other endpoint below *min_degree*.  Stops early and logs a
        warning if trimming is impossible without violating min_degree.

    This function is intentionally single-threaded: each modification to
    *neighbors* affects subsequent decisions, so the operations are
    order-dependent and cannot be safely parallelised.

    Pre-sorting candidate lists once per scene (O(N log N) total) avoids
    re-sorting on every degree-enforcement iteration.
    """
    neighbors: dict[SceneID, set[SceneID]] = defaultdict(set)
    for a, b in pairs:
        neighbors[a].add(b)
        neighbors[b].add(a)

    # Pre-sort candidates by |Δt| for every scene — paid once, reused many times
    sorted_cands: dict[SceneID, list[tuple[SceneID, float]]] = {
        n: sorted(
            ((m, abs(id_time_dt[m] - id_time_dt[n])) for m in names if m != n),
            key=lambda x: x[1],
        )
        for n in names
    }

    # ── Step A: boost under-connected scenes ─────────────────────────────
    if force_connect:
        for n in names:
            if len(neighbors[n]) >= min_degree:
                continue

            logger.debug(
                "Scene %s: degree %d < min_degree %d; searching for more pairs.",
                n, len(neighbors[n]), min_degree,
            )

            for m, _ in sorted_cands[n]:
                if len(neighbors[n]) >= min_degree:
                    break
                if m in neighbors[n]:
                    continue

                a, b = (n, m) if id_time_dt[n] <= id_time_dt[m] else (m, n)
                entry = B.get((a, b))
                if entry is None:
                    continue

                dt_val, bp_val = entry
                if bp_val > pb_max or dt_val > dt_max:
                    continue

                pairs.add((a, b))
                neighbors[a].add(b)
                neighbors[b].add(a)
                logger.debug(
                    "  force-added %s – %s  (dt=%.0f d, bp=%.1f m)",
                    a, b, dt_val, bp_val,
                )

            if len(neighbors[n]) < min_degree:
                logger.warning(
                    "Scene %s: only %d / %d connections available in baseline table.",
                    n, len(neighbors[n]), min_degree,
                )

    # ── Step B: trim over-connected scenes ───────────────────────────────
    for n in names:
        while len(neighbors[n]) > max_degree:
            # Rank neighbours: worst = highest dt, then highest bperp
            ranked = sorted(
                neighbors[n],
                key=lambda m: B.get(
                    (n, m) if id_time_dt[n] <= id_time_dt[m] else (m, n),
                    (0.0, 0.0),
                ),
                reverse=True,
            )

            removed = False
            
            for min_other in (min_degree + 1, min_degree):
                for worst in ranked:
                    if len(neighbors[worst]) < min_other:
                        continue
                    a, b = (
                        (n, worst) if id_time_dt[n] <= id_time_dt[worst]
                        else (worst, n)
                    )
                    pairs.discard((a, b))
                    neighbors[n].discard(worst)
                    neighbors[worst].discard(n)
                    removed = True
                    break
                if removed:
                    break

            if not removed:
                # Every neighbour is at min_degree — impossible to trim further.
                # This happens when min_degree and max_degree conflict,
                # e.g. min_degree=5, max_degree=3.
                logger.warning(
                    "Scene %s: cannot trim to max_degree=%d — all %d neighbours "
                    "are at min_degree=%d. Consider increasing max_degree or "
                    "decreasing min_degree.",
                    n, max_degree, len(neighbors[n]), min_degree,
                )
                break

    return pairs

def _simplify_to_fit(geom, max_len: int = _WKT_MAX_LEN):
    """Progressively simplify a Shapely geometry until its WKT fits within max_len chars."""
    wkt_str = wkt.dumps(geom, rounding_precision=5)
    if len(wkt_str) <= max_len:
        return wkt_str
    for tol in (0.001, 0.005, 0.01, 0.05, 0.1):
        simplified = geom.simplify(tol, preserve_topology=True)
        wkt_str = wkt.dumps(simplified, rounding_precision=5)
        if len(wkt_str) <= max_len:
            return wkt_str
    # Last resort: convex hull is always compact
    return wkt.dumps(geom.convex_hull, rounding_precision=5)

def _to_wkt(geom_input) -> str | None:
    """
    Converts various input types to a WKT string.
    Supported: 
    1. List/Tuple of 4 numbers [min_lon, min_lat, max_lon, max_lat]
    2. String path to a spatial file (GeoJSON, SHP, etc.)
    3. Valid WKT string
    """
    if isinstance(geom_input, (list, tuple)):
        if len(geom_input) != 4:
            raise ValueError(f"BBox list must have exactly 4 elements, got {len(geom_input)}")
        
        if not all(isinstance(n, (int, float)) for n in geom_input):
            raise TypeError("All elements in BBox list must be int or float.")
        
        return wkt.dumps(box(*geom_input), rounding_precision=5)
    
    if isinstance(geom_input, str):
        geom_input = geom_input.strip()
        is_file_path = False
        if len(geom_input) < 255: 
            try:
                is_file_path = Path(geom_input).exists()
            except OSError:
                pass
        
        if is_file_path:
            try:
                gdf = gpd.read_file(geom_input)
                # Combine all geometries in the file into one
                geom = gdf.geometry.union_all()
                return _simplify_to_fit(geom)
            except Exception as e:
                raise ValueError(f"Could not read spatial file at {geom_input}: {e}")
              
        try:
            # Try to load it to see if it's valid WKT
            decoded = wkt.loads(geom_input)
            return _simplify_to_fit(decoded)
        except Exception:
            raise ValueError(
                "Input string is neither a valid file path nor a valid WKT string."
            )
    if not geom_input:
        return None
    raise TypeError(f"Unsupported input type: {type(geom_input)}. Expected list, tuple, or str.")


# ═══════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

from insarhub.utils.defaults import SELECT_PAIRS_DEFAULTS as _SP

def select_pairs(
    search_results: Union[dict[tuple[int, int], list[ASFProduct]], list[ASFProduct]],
    dt_targets: tuple[int, ...]  = _SP["dt_targets"],
    dt_tol: int                  = _SP["dt_tol"],
    dt_max: int                  = _SP["dt_max"],
    pb_max: float                = _SP["pb_max"],
    min_degree: int              = _SP["min_degree"],
    max_degree: int              = _SP["max_degree"],
    force_connect: bool          = _SP["force_connect"],
    max_workers: int             = _SP["max_workers"],
    avoid_low_quality_days: bool = _SP["avoid_low_quality_days"],
    snow_threshold: float        = _SP["snow_threshold"],
    precip_mm_threshold: float   = _SP["precip_mm_threshold"],
    aoi_wkt: str | None = None,
) -> Union[PairGroup, list[Pair]]:
    
    """
    Select interferogram pairs based on temporal and perpendicular baseline.

    This function selects interferogram pairs according to temporal spacing 
    and perpendicular baseline constraints, optionally enforcing connectivity 
    rules per scene.

    Supported sensors:
    - Sentinel-1 (CALCULATED)  : stateVectors + ascendingNodeTime → local
    - ALOS / ERS / RADARSAT (PRE_CALCULATED) : insarBaseline scalar → local
    - Any product missing data : ref.stack() API call → fallback

    Args:
        search_results (list[ASFProduct] | dict[tuple[int,int], list[ASFProduct]]):
            Either a flat list (single stack) or a dictionary keyed by (path, frame).
        dt_targets (list[float], optional):
            Preferred temporal spacings in days. A candidate pair passes if 
            |dt - target| <= dt_tol for at least one target.
        dt_tol (float, optional):
            Tolerance in days added to each entry in dt_targets.
        dt_max (float, optional):
            Maximum temporal baseline in days.
        pb_max (float, optional):
            Maximum perpendicular baseline in meters.
        min_degree (int, optional):
            Minimum interferogram connections per scene. Enforced when force_connect is True.
        max_degree (int, optional):
            Maximum interferogram connections per scene.
        force_connect (bool, optional):
            If a scene falls below min_degree after primary selection, add its nearest-time 
            neighbors that satisfy pb_max and dt_max. May introduce lower-quality pairs; a warning is logged.
        max_workers (int, optional):
            Number of threads for API fallback. Has no effect if all products have local baseline
            data (common for Sentinel-1 and ALOS). Set to 1 to disable threading (useful for debugging).
        avoid_low_quality_days (bool, optional):
            If True, fetch weather and snow cover for every acquisition date and
            remove scenes whose date fails quality thresholds before building the
            network. Removed scenes are logged as warnings. Defaults to False.
        snow_threshold (float, optional):
            Snow-cover fraction [0–1] above which a scene is considered unusable.
            Also triggers a wet-snow hard-kill (snow_frac > 0.3 AND temp > 0 °C).
            Defaults to 0.5.
        precip_mm_threshold (float, optional):
            3-day accumulated precipitation (mm) above which a scene is considered
            unusable. Defaults to 20.0 mm.
        aoi_wkt (str, optional):
            WKT geometry of the area of interest.  When provided the centroid of
            this geometry is used for weather/snow lookups instead of the union
            centroid of the scene footprints, ensuring the same location is used
            here as in FeatureAssembler.  Defaults to None.

    Returns:
        tuple of three elements:

        pairs (list[Pair] | dict[tuple[int,int], list[Pair]]):
            A flat list of Pair tuples ``(earlier_scene, later_scene)`` if
            *search_results* was a list, or a dict keyed by ``(path, frame)``
            with lists of Pair tuples if *search_results* was a dict.

        baselines (BaselineTable | dict[tuple[int,int], BaselineTable]):
            Pairwise baseline table mapping ``(scene_a, scene_b)`` →
            ``(dt_days, bperp_m)`` where ``bperp_m = |bp_a − bp_b|`` (always
            positive). Mirrors the structure of *pairs* (flat or grouped).

        scene_bperp (dict[str, float] | dict[tuple[int,int], dict[str, float]]):
            Signed perpendicular baseline for each scene relative to the
            anchor (earliest) scene, as returned by
            ``calculate_perpendicular_baselines``. Values can be negative or
            positive. Use this for network plots to reproduce the MintPy-style
            y-axis (negative/positive spread around zero). Mirrors the
            structure of *pairs* (flat or grouped).
    """

    # ── bad-day pre-filter ────────────────────────────────────────────────
    def _bad_scene_names(
        prods: list,
        snow_thr: float,
        precip_thr: float,
    ) -> tuple[set[str], dict[str, dict], dict[str, dict], float, float]:
        """Return (bad_names, weather_dict, snow_dict, lat, lon).

        weather_dict / snow_dict are {date: feats} for ALL unique dates — not
        just the bad ones — so callers can seed FeatureAssembler's cache and
        avoid a second fetch during scoring.

        Flags a date bad when:
          - wet snow  : snow_frac > 0.3 AND temp_max > 0 °C  (C-band hard kill)
          - heavy snow: snow_frac >= snow_thr
          - heavy rain: precip_3day (or daily precip fallback) >= precip_thr mm
        """
        from shapely.geometry import shape as _shape
        from shapely.ops import unary_union
        from insarhub.utils.pair_quality._weather import fetch_weather_batch
        from insarhub.utils.pair_quality._snow_modis import fetch_snow_features_batch

        # Prefer the explicit AOI centroid (same geometry FeatureAssembler uses).
        # Fall back to the union centroid of all scene footprints.
        try:
            from shapely import wkt as _wkt
            if aoi_wkt:
                c = _wkt.loads(aoi_wkt).centroid
            else:
                union = unary_union([_shape(p.geometry) for p in prods])
                c = union.centroid
            lat, lon = c.y, c.x
        except Exception as exc:
            logger.warning("avoid_low_quality_days: could not extract centroid (%s) — skipping filter", exc)
            return set(), {}, {}, 0.0, 0.0

        # Use startTime property (already ISO-8601) rather than parsing the scene
        # name at fixed character positions — more reliable across sensor types.
        date_of: dict[str, str] = {
            p.properties["sceneName"]: (p.properties.get("startTime") or "")[:10]
            for p in prods
        }
        unique_dates = list({d for d in date_of.values() if len(d) == 10})

        if not unique_dates:
            logger.warning("avoid_low_quality_days: no valid dates extracted — skipping filter")
            return set(), {}, {}, lat, lon

        logger.info("avoid_low_quality_days: fetching weather/snow for %d dates …", len(unique_dates))
        try:
            weather = fetch_weather_batch(lat, lon, unique_dates)
        except Exception as exc:
            logger.warning("avoid_low_quality_days: weather fetch failed (%s) — skipping filter", exc)
            weather = {}
        try:
            snow = fetch_snow_features_batch(lat, lon, unique_dates)
        except Exception as exc:
            logger.warning("avoid_low_quality_days: snow fetch failed (%s) — skipping filter", exc)
            snow = {}

        bad_dates: set[str] = set()
        for date in unique_dates:
            w = weather.get(date, {})
            s = snow.get(date, {})
            temp      = w.get("temp_max")
            # precip_3day can be None when the API returns null for precipitation_sum.
            # Fall back to the daily precip so the check is never silently bypassed.
            precip3   = w.get("precip_3day")
            precip1   = w.get("precip")
            precip_mm = (precip3 if precip3 is not None else precip1) or 0.0
            snow_frac = s.get("snow_cover_frac") or 0.0

            wet_snow   = (temp is not None and temp > 0 and snow_frac > 0.30)
            heavy_snow = snow_frac >= snow_thr
            heavy_rain = precip_mm >= precip_thr

            if wet_snow or heavy_snow or heavy_rain:
                reasons = []
                if wet_snow:   reasons.append(f"wet snow (frac={snow_frac:.2f}, temp={temp:.1f}°C)")
                if heavy_snow: reasons.append(f"heavy snow (frac={snow_frac:.2f} ≥ {snow_thr})")
                if heavy_rain: reasons.append(f"heavy rain (precip={precip_mm:.1f} mm ≥ {precip_thr} mm)")
                logger.warning("avoid_low_quality_days: dropping date %s — %s", date, ", ".join(reasons))
                bad_dates.add(date)

        bad_names = {name for name, d in date_of.items() if d in bad_dates}
        return bad_names, weather, snow, lat, lon

    # ── normalise input ───────────────────────────────────────────────────
    input_is_list = isinstance(search_results, list)
    if input_is_list:
        working_dict: dict[tuple[int, int], list[ASFProduct]] = {
            (0, 0): search_results   # type: ignore[arg-type]
        }
    elif isinstance(search_results, dict):
        working_dict = search_results
    else:
        raise TypeError(
            f"search_results must be a list or dict of ASFProducts, "
            f"got {type(search_results)}"
        )

    # ── primary filter helpers (defined once, closed over threshold args) ─
    def _near_target(dt: float) -> bool:
        return any(abs(dt - t) <= dt_tol for t in dt_targets)

    def _passes_primary(dt: float, bp: float) -> bool:
        return _near_target(dt) and dt <= dt_max and bp <= pb_max

    pairs_group: PairGroup = defaultdict(list)
    baseline_group: dict[tuple[int, int], BaselineTable] = {}
    scene_bperp_group: dict[tuple[int, int], dict] = {}
    # Keyed by (path, frame): {"weather": {date: feats}, "snow": {date: feats}, "lat": float, "lon": float}
    prefetch_cache: dict[tuple[int, int], dict] = {}

    # ── process each (path, frame) key ───────────────────────────────────
    for key, search_result in working_dict.items():
        if not input_is_list:
            logger.info(
                "%sSearching pairs for path %d frame %d …",
                Fore.GREEN, key[0], key[1],
            )

        # Sort by acquisition time so `names` is chronologically ordered
        prods = sorted(search_result, key=lambda p: p.properties["startTime"])

        if not prods:
            logger.warning("No products for key %s — skipping.", key)
            continue

        # Pre-parse acquisition datetimes to Unix timestamps (done once;
        # reused in sort keys, dt calculations, and pair ordering)
        id_time_raw: dict[SceneID, str] = {
            p.properties["sceneName"]: p.properties["startTime"] for p in prods
        }
        id_time_dt: dict[SceneID, DateFloat] = {
            sid: isoparse(t).timestamp() for sid, t in id_time_raw.items()
        }
        ids: set[SceneID] = set(id_time_raw)
        names: list[SceneID] = [p.properties["sceneName"] for p in prods]

        # ── 0. Drop bad-weather/snow acquisition dates ────────────────────
        if avoid_low_quality_days:
            bad, w_cache, s_cache, pc_lat, pc_lon = _bad_scene_names(
                prods, snow_threshold, precip_mm_threshold
            )
            prefetch_cache[key] = {
                "weather": w_cache,
                "snow":    s_cache,
                "lat":     pc_lat,
                "lon":     pc_lon,
            }
            if bad:
                before = len(prods)
                prods  = [p for p in prods if p.properties["sceneName"] not in bad]
                names  = [n for n in names if n not in bad]
                ids    = set(names)
                id_time_raw = {k: v for k, v in id_time_raw.items() if k not in bad}
                id_time_dt  = {k: v for k, v in id_time_dt.items()  if k not in bad}
                logger.warning(
                    "Key %s — avoid_low_quality_days: removed %d / %d scenes.",
                    key, before - len(prods), before,
                )

        # ── 1. Build pairwise baseline table ─────────────────────────────
        B, scene_bp = _build_baseline_table(prods, ids, id_time_dt, max_workers=max_workers)
        baseline_group[key] = B
        scene_bperp_group[key] = scene_bp
        # ── 2. Primary pair selection ─────────────────────────────────────
        pairs: set[Pair] = {
            e for e, (dt, bp) in B.items() if _passes_primary(dt, bp)
        }
        logger.info(
            "Key %s — primary selection: %d / %d candidate pairs.",
            key, len(pairs), len(B),
        )

        # ── 3. Connectivity enforcement ───────────────────────────────────
        pairs = _enforce_connectivity(
            pairs,
            B,
            names,
            id_time_dt,
            min_degree=min_degree,
            max_degree=max_degree,
            pb_max=pb_max,
            dt_max=float(dt_max),
            force_connect=force_connect
        )

        pairs_group[key] = sorted(pairs)
        logger.info(
            "Key %s — final pair count: %d.", key, len(pairs_group[key])
        )
    pairs = pairs_group[(0, 0)] if input_is_list else pairs_group
    scene_bperp = scene_bperp_group.get((0, 0), {}) if input_is_list else scene_bperp_group
    prefetch = prefetch_cache.get((0, 0), {}) if input_is_list else prefetch_cache

    return pairs, baseline_group, scene_bperp, prefetch

def get_config(config_path=None):

    """A function to load config file in TOML format"""
    if config_path is None:
        config_path = Path(__file__).parent.joinpath('config.toml')        
    config_path = Path(config_path)
    if config_path.is_file():
        try:
            with open(config_path, 'rb') as f:
                toml = tomllib.load(f)
                cfg = Config(toml)
                return cfg
        except Exception as e:
                raise ValueError(f"Error loading config file with error {e}, is this a valid config file in TOML format?")
    else:
        raise FileNotFoundError(f"Config file not found under {config_path}")
    

def plot_pair_network(
    pairs: list[Pair] | PairGroup,
    baselines: BaselineTable,
    scene_baselines: dict | None = None,
    title: str = "Interferogram Network",
    figsize: tuple[int, int] = (18, 7),
    save_path: str | Path | None = None,
    quality_scores: dict[str, float] | None = None,
    quality_factors: dict[str, dict] | None = None,
) -> plt.Figure | dict:

    """
    Plot an interferogram network along with per-scene connection statistics.

    This function visualizes the relationships between SAR acquisitions in
    terms of temporal and perpendicular baselines. The network graph is
    shown on the left, while a horizontal bar chart summarizes the number
    of connections per scene on the right.

    The layout is as follows:
        - Left  : Network graph (x-axis = days since first acquisition,
                  y-axis = perpendicular baseline [m])
        - Right : Horizontal bar chart showing the number of connections per SAR scene

    Args:
        pairs (list[Pair] | PairGroup):
            A flat list of pairs or a dictionary keyed by (path, frame)
            with lists of pairs. Each pair is a tuple `(earlier_scene, later_scene)`.
        baselines (BaselineTable):
            Table or mapping containing temporal and perpendicular baseline
            information for each interferogram pair.
        title (str, optional):
            Main title of the network plot. Defaults to "Interferogram Network".
        figsize (tuple[int, int], optional):
            Figure size (width, height) in inches. Defaults to (18, 7).
        save_path (str | Path | None, optional):
            Path to save the generated figure. If None, figure is not saved.
            Defaults to None.

    Returns:
        matplotlib.figure.Figure:
            The created matplotlib figure containing the network and
            per-scene connection histogram.

    Raises:
        TypeError:
            If any scene name in `pairs` is not a string.
        ValueError:
            If a scene name cannot be parsed into a valid date.

    Notes:
        - Node positions: x = days since first acquisition, y = perpendicular baseline.
        - Node color represents the node degree (number of connections).
        - Edge color and width represent temporal baseline.
        - Scenes with fewer than 2 connections are highlighted in red in the histogram.
        - Legends show node degree, temporal baseline, and path/frame grouping.
        - The top axis of the network plot shows real acquisition dates for reference.
    """

    # ── 0. Normalise input ────────────────────────────────────────────────
    
    if isinstance(pairs, dict):
        figures = {}

        save_path_obj = None
        save_is_dir = False

        if save_path is not None:
            save_path_obj = Path(save_path).expanduser()

            if save_path_obj.suffix == "":
                save_is_dir = True
                save_path_obj.mkdir(parents=True, exist_ok=True)
            else:
                # Has suffix → treat as file template
                save_path_obj.parent.mkdir(parents=True, exist_ok=True)

        for (path, frame), group_pairs in pairs.items():
            group_title = f"{title} — P{path}/F{frame}"
            group_save_path = None

            if save_path_obj is not None:
                if save_is_dir:
                    # Case 1: directory given
                    group_save_path = (
                        save_path_obj.joinpath(f"network_P{path}_F{frame}.png")
                    )
                else:
                    # Case 2: file with suffix given
                    group_save_path = (
                        save_path_obj.parent
                        / f"{save_path_obj.stem}_P{path}_F{frame}{save_path_obj.suffix}"
                    )

            fig = plot_pair_network(
                    pairs=group_pairs,
                    baselines=baselines[(path, frame)],
                    scene_baselines=scene_baselines.get((path, frame)) if isinstance(scene_baselines, dict) else scene_baselines,
                    title=group_title,
                    figsize=figsize,
                    save_path=group_save_path,
                    quality_scores=quality_scores,
                    quality_factors=quality_factors,
                )

            figures[(path, frame)] = fig

        return figures
        


    flat_pairs = pairs
    subtitle = f"{len(flat_pairs)} pairs"
    if save_path is not None:
        save_path = Path(save_path).expanduser()
    # ── 1. Parse dates ────────────────────────────────────────────────────
    scenes: set[SceneID] = set()
    for a, b in flat_pairs:
        scenes.update([a, b])

    def _parse_date(scene_name: str) -> datetime:
        if not isinstance(scene_name, str):
            raise TypeError(
                f"Expected str, got {type(scene_name).__name__}: {scene_name!r}."
            )
        m = re.search(r"(\d{8})", scene_name)
        if m:
            return datetime.strptime(m.group(1), "%Y%m%d")
        m = re.search(r"(\d{4}-\d{2}-\d{2})", scene_name)
        if m:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        raise ValueError(f"Cannot parse date from scene name: {scene_name}")

    id_time: dict[SceneID, datetime] = {s: _parse_date(s) for s in scenes}
    t0      = min(id_time.values())
    id_days: dict[SceneID, float] = {
        s: (id_time[s] - t0).total_seconds() / 86_400.0 for s in scenes
    }

    # ── 2. Build graph ────────────────────────────────────────────────────
    G = nx.Graph()
    G.add_nodes_from(scenes)

    if isinstance(pairs, dict):
        for (path, frame), pair_list in pairs.items():
            for a, b in pair_list:
                dt, bp = baselines.get((a, b), (_MISSING, _MISSING))
                G.add_edge(a, b, dt=dt, bp=bp, path=path, frame=frame)
    else:
        for a, b in flat_pairs:
            dt, bp = baselines.get((a, b), (_MISSING, _MISSING))
            G.add_edge(a, b, dt=dt, bp=bp, path=0, frame=0)

    # ── 3. Node positions (x=days, y=bperp) ──────────────────────────────
    if scene_baselines:
        # Use signed per-scene bperp relative to anchor — same as MintPy display
        # (negative = scene orbited closer than anchor, positive = further)
        bperp_pos: dict[SceneID, float] = {
            s: float(scene_baselines.get(s, 0.0)) for s in scenes
        }
    else:
        # Fallback: reconstruct from pairwise table (loses sign info, may trend upward)
        bperp_accum: dict[SceneID, list[float]] = defaultdict(list)
        for (a, b), (dt, bp) in baselines.items():
            if bp >= _MISSING:
                continue
            bperp_accum[a].append(-bp / 2.0)
            bperp_accum[b].append(+bp / 2.0)
        bperp_pos = {
            s: float(np.mean(v)) if v else 0.0
            for s, v in bperp_accum.items()
        }
        sorted_by_time = sorted(scenes, key=lambda s: id_days[s])
        offset = bperp_pos.get(sorted_by_time[0], 0.0)
        bperp_pos = {s: bperp_pos.get(s, 0.0) - offset for s in scenes}

    pos: dict[SceneID, tuple[float, float]] = {
        s: (id_days[s], bperp_pos[s]) for s in scenes
    }

    # ── 4. Visual attributes ──────────────────────────────────────────────
    # 3-category quality colours matching the GUI thresholds (Hanssen 2001)
    _Q_GOOD  = '#4caf50'
    _Q_RISKY = '#ffc107'
    _Q_BAD   = '#f44336'
    _Q_NONE  = '#888888'  # unscored

    def _quality_colour(sc: float | None) -> str:
        if sc is None:  return _Q_NONE
        # support both 0-1 (coherence) and 0-100 (pair quality) scales
        v = sc if sc <= 1 else sc / 100.0
        if v >= 0.60:  return _Q_GOOD
        if v >= 0.30:  return _Q_RISKY
        return _Q_BAD

    # ── Extract per-class scores from quality_factors ─────────────────────
    _LC_CLASSES = [
        ("stable",     "🏗 Stable"),
        ("vegetation", "🌾 Vegetation"),
        ("forest",     "🌲 Forest"),
    ]
    _class_scores: dict[str, dict[str, float]] = {}   # {class: {pair_key: coh}}
    if quality_factors:
        for pair_key, fct in quality_factors.items():
            by_cls = fct.get("coherence_by_class") or {}
            for cls_name, coh_val in by_cls.items():
                _class_scores.setdefault(cls_name, {})[pair_key] = float(coh_val)
                # Also store reverse key
                parts = pair_key.split(":", 1)
                if len(parts) == 2:
                    _class_scores[cls_name][f"{parts[1]}:{parts[0]}"] = float(coh_val)

    degrees      = dict(G.degree())
    max_deg      = max(degrees.values(), default=1)
    node_colours = [plt.cm.RdYlGn(degrees[n] / max_deg) for n in G.nodes()]

    edge_dts     = [G[a][b]["dt"] for a, b in G.edges()]
    max_dt       = max((d for d in edge_dts if d < _MISSING), default=1.0)

    if quality_scores:
        edge_colours = []
        edge_widths  = []
        for a, b in G.edges():
            sc = quality_scores.get(f"{a}:{b}") or quality_scores.get(f"{b}:{a}")
            edge_colours.append(_quality_colour(sc))
            sv = (sc / 100.0 if sc is not None and sc > 1 else sc)
            edge_widths.append(2.0 if sv is not None and sv >= 0.60 else
                               1.2 if sv is not None and sv >= 0.30 else 0.7)
    else:
        edge_colours = [plt.cm.RdYlGn_r(min(dt, max_dt) / max_dt) for dt in edge_dts]
        edge_widths  = [0.5 + 2.5 * (1.0 - min(dt, max_dt) / max_dt) for dt in edge_dts]

    if isinstance(pairs, dict):
        group_keys  = list(pairs.keys())
        linestyles  = ["-", "--", "-.", ":"] * (len(group_keys) // 4 + 1)
        key_style   = {k: linestyles[i] for i, k in enumerate(group_keys)}
        edge_styles = [
            key_style[(G[a][b]["path"], G[a][b]["frame"])] for a, b in G.edges()
        ]
    else:
        edge_styles = ["-"] * len(G.edges())

    # ── 5. Figure layout ─────────────────────────────────────────────────
    # Main figure: network + histogram.
    # Per-class figures are saved separately when class data is available.
    _has_class_data = bool(_class_scores)
    fig = plt.figure(figsize=figsize)
    gs  = fig.add_gridspec(1, 2, width_ratios=[3, 1], wspace=0.35)
    ax_net  = fig.add_subplot(gs[0])
    ax_hist = fig.add_subplot(gs[1])

    # ── 6. Draw network ───────────────────────────────────────────────────
    edges_by_style: dict[str, list] = defaultdict(list)
    for (a, b), style, colour, width in zip(
        G.edges(), edge_styles, edge_colours, edge_widths
    ):
        edges_by_style[style].append((a, b, colour, width))

    for style, edge_data in edges_by_style.items():
        nx.draw_networkx_edges(
            G, pos, ax=ax_net,
            edgelist=[(a, b) for a, b, _, _ in edge_data],
            edge_color=[c for _, _, c, _ in edge_data],
            width=[w for _, _, _, w in edge_data],
            style=style,
            alpha=0.7,
        )

    nx.draw_networkx_nodes(
        G, pos, ax=ax_net,
        node_color=node_colours,
        node_size=80,
        linewidths=0.5,
        edgecolors="black",
    )
    nx.draw_networkx_labels(
        G, pos,
        labels={s: s[-8:] for s in G.nodes()},
        ax=ax_net,
        font_size=5,
    )

    # ── 7. Network axes ───────────────────────────────────────────────────
    ax_net.set_xlabel("Days since first acquisition", fontsize=11)
    ax_net.set_ylabel("Perpendicular baseline [m]", fontsize=11)    # ✅ real unit
    ax_net.set_title(
        f"{title}\n{subtitle}\n"
        f"{len(scenes)} scenes · {len(flat_pairs)} pairs · "
        f"mean degree {np.mean(list(degrees.values())):.1f}",
        fontsize=11,
    )
    ax_net.tick_params(left=True, bottom=True, labelleft=True, labelbottom=True)
    ax_net.set_frame_on(True)

    # real date ticks on top axis
    x_vals  = [p[0] for p in pos.values()]
    x_ticks = np.linspace(min(x_vals), max(x_vals), min(8, len(pos)))
    ax2 = ax_net.twiny()
    ax2.set_xlim(ax_net.get_xlim())
    ax2.set_xticks(x_ticks)
    ax2.set_xticklabels(
        [
            (t0 + __import__("datetime").timedelta(days=d)).strftime("%Y-%m-%d")
            for d in x_ticks
        ],
        rotation=30, ha="left", fontsize=7,
    )
    ax2.set_xlabel("Acquisition date (UTC)", fontsize=9)

    # ── 8. Per-scene connection histogram ─────────────────────────────────
    # Sort scenes by date so the histogram reads chronologically top→bottom
    sorted_scene_names = sorted(scenes, key=lambda s: id_days[s])
    scene_degrees      = [degrees[s] for s in sorted_scene_names]
    short_names        = [s[-12:] for s in sorted_scene_names]   # trim for readability
    y_positions        = range(len(sorted_scene_names))

    bar_colours = [plt.cm.RdYlGn(degrees[s] / max_deg) for s in sorted_scene_names]

    bars = ax_hist.barh(
        y_positions,
        scene_degrees,
        color=bar_colours,
        edgecolor="white",
        linewidth=0.4,
        height=0.7,
    )

    # annotate each bar with connection count
    for bar, count in zip(bars, scene_degrees):
        ax_hist.text(
            bar.get_width() + 0.1,
            bar.get_y() + bar.get_height() / 2,
            str(count),
            va="center", fontsize=7,
        )

    # vertical line at mean degree
    mean_deg = np.mean(scene_degrees)
    ax_hist.axvline(
        mean_deg, color="steelblue", linestyle="--", linewidth=1.0, alpha=0.8
    )
    ax_hist.text(
        mean_deg + 0.1, len(sorted_scene_names) - 0.5,
        f"mean\n{mean_deg:.1f}",
        color="steelblue", fontsize=7, va="top",
    )

    # mark scenes below min connectivity in red
    for i, (s, deg) in enumerate(zip(sorted_scene_names, scene_degrees)):
        if deg < 2:
            ax_hist.get_children()[i].set_edgecolor("red")
            ax_hist.get_children()[i].set_linewidth(1.5)

    ax_hist.set_yticks(y_positions)
    ax_hist.set_yticklabels(short_names, fontsize=6)
    ax_hist.set_xlabel("Number of connections", fontsize=9)
    ax_hist.set_title("Connections\nper scene", fontsize=10)
    ax_hist.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_hist.set_frame_on(True)
    # match vertical order to network: earliest at top
    ax_hist.invert_yaxis()

    # ── 9. Legends ────────────────────────────────────────────────────────
    deg_legend = ax_net.legend(
        handles=[
            mpatches.Patch(color=plt.cm.RdYlGn(v / max_deg), label=f"degree {v}")
            for v in sorted(set(degrees.values()))
        ],
        title="Node degree", loc="upper left", fontsize=7, title_fontsize=8,
    )
    ax_net.add_artist(deg_legend)

    if quality_scores:
        ax_net.legend(
            handles=[
                mpatches.Patch(color=_Q_GOOD,  label="Good"),
                mpatches.Patch(color=_Q_RISKY, label="Risky"),
                mpatches.Patch(color=_Q_BAD,   label="Bad"),
                mpatches.Patch(color=_Q_NONE,  label="Unscored"),
            ],
            title="Pair quality", loc="lower right", fontsize=7, title_fontsize=8,
        )
    else:
        ax_net.legend(
            handles=[
                mpatches.Patch(
                    color=plt.cm.RdYlGn_r(v / max_dt), label=f"{v:.0f} days"
                )
                for v in [0, max_dt * 0.33, max_dt * 0.66, max_dt]
            ],
            title="Temporal baseline", loc="lower right", fontsize=7, title_fontsize=8,
        )

    if isinstance(pairs, dict):
        ax_net.add_artist(
            ax_net.legend(
                handles=[
                    mpatches.Patch(
                        linestyle=key_style[k], fill=False,
                        edgecolor="grey", label=f"P{k[0]}/F{k[1]}",
                    )
                    for k in group_keys
                ],
                title="Path / Frame", loc="upper right", fontsize=7, title_fontsize=8,
            )
        )

    if save_path:
        fig.savefig(save_path.as_posix(), dpi=300, bbox_inches="tight")
        print(f"Saved → {save_path}")

    # ── 10. Per-LC-class figures (separate PNGs) ──────────────────────────
    if _has_class_data and save_path:
        sp   = Path(save_path)
        stem = sp.stem
        ext  = sp.suffix

        for cls_name, cls_label in _LC_CLASSES:
            cls_map = _class_scores.get(cls_name, {})
            if not cls_map:
                continue

            c_colours, c_widths = [], []
            for a, b in G.edges():
                sc = cls_map.get(f"{a}:{b}") or cls_map.get(f"{b}:{a}")
                c_colours.append(_quality_colour(sc))
                sv = (sc / 100.0 if sc is not None and sc > 1 else sc)
                c_widths.append(2.0 if sv is not None and sv >= 0.60 else
                                1.2 if sv is not None and sv >= 0.30 else 0.7)

            fig_c = plt.figure(figsize=figsize)
            gs_c  = fig_c.add_gridspec(1, 2, width_ratios=[3, 1], wspace=0.35)
            ax_c  = fig_c.add_subplot(gs_c[0])
            ax_h  = fig_c.add_subplot(gs_c[1])

            # Network
            nx.draw_networkx_edges(
                G, pos, ax=ax_c,
                edgelist=list(G.edges()),
                edge_color=c_colours,
                width=c_widths,
                alpha=0.7,
            )
            nx.draw_networkx_nodes(
                G, pos, ax=ax_c,
                node_color=node_colours,
                node_size=80,
                linewidths=0.5,
                edgecolors="black",
            )
            nx.draw_networkx_labels(
                G, pos,
                labels={s: s[-8:] for s in G.nodes()},
                ax=ax_c,
                font_size=5,
            )

            ax_c.set_xlabel("Days since first acquisition", fontsize=11)
            ax_c.set_ylabel("Perpendicular baseline [m]", fontsize=11)
            ax_c.set_title(
                f"{title} — {cls_label}\n{subtitle}\n"
                f"{len(scenes)} scenes · {len(flat_pairs)} pairs",
                fontsize=11,
            )
            ax_c.tick_params(left=True, bottom=True, labelleft=True, labelbottom=True)
            ax_c.set_frame_on(True)

            ax2_c = ax_c.twiny()
            ax2_c.set_xlim(ax_c.get_xlim())
            ax2_c.set_xticks(x_ticks)
            ax2_c.set_xticklabels(
                [(t0 + __import__("datetime").timedelta(days=d)).strftime("%Y-%m-%d")
                 for d in x_ticks],
                rotation=30, ha="left", fontsize=7,
            )
            ax2_c.set_xlabel("Acquisition date (UTC)", fontsize=9)

            ax_c.legend(
                handles=[
                    mpatches.Patch(color=_Q_GOOD,  label="Good  (≥0.60)"),
                    mpatches.Patch(color=_Q_RISKY, label="Risky (0.30–0.60)"),
                    mpatches.Patch(color=_Q_BAD,   label="Bad   (<0.30)"),
                    mpatches.Patch(color=_Q_NONE,  label="Unscored"),
                ],
                title=f"{cls_label} coherence", loc="lower right",
                fontsize=7, title_fontsize=8,
            )

            # Histogram (same degree info — identical across class figures)
            ax_h.barh(
                y_positions, scene_degrees,
                color=bar_colours, edgecolor="white", linewidth=0.4, height=0.7,
            )
            for bar, count in zip(ax_h.patches, scene_degrees):
                ax_h.text(bar.get_width() + 0.1,
                          bar.get_y() + bar.get_height() / 2,
                          str(count), va="center", fontsize=7)
            ax_h.axvline(mean_deg, color="steelblue", linestyle="--",
                         linewidth=1.0, alpha=0.8)
            ax_h.set_yticks(y_positions)
            ax_h.set_yticklabels(short_names, fontsize=6)
            ax_h.set_xlabel("Number of connections", fontsize=9)
            ax_h.set_title("Connections\nper scene", fontsize=10)
            ax_h.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
            ax_h.set_frame_on(True)
            ax_h.invert_yaxis()

            cls_path = sp.parent / f"{stem}_{cls_name}{ext}"
            fig_c.savefig(cls_path.as_posix(), dpi=300, bbox_inches="tight")
            plt.close(fig_c)
            print(f"Saved → {cls_path}")

    return fig

def earth_credit_pool(earthdata_credentials_pool_path = Path.home().joinpath('.credit_pool')) -> dict:
    """
    Load Earthdata credentials from a local credit pool file.

    The function reads a simple key-value file where each line contains
    `username:password` (or `key:value`) pairs, and returns them as a dictionary.

    Args:
        earthdata_credentials_pool_path (Path, optional):
            Path to the Earthdata credentials file. Defaults to
            `~/.credit_pool`. The path is expanded and resolved to an absolute path.

    Returns:
        dict:
            Dictionary mapping credential keys to their corresponding values.
            Example:
            ```
            {
                "username1": "password1",
                "username2": "password2",
            }
            ```

    Raises:
        FileNotFoundError:
            If the specified credentials file does not exist.
        ValueError:
            If any line in the file does not contain a single ':' separating key and value.
        OSError:
            For any other I/O related errors while reading the file.

    Notes:
        - Each line of the file must be formatted as `key:value`.
        - Leading/trailing whitespace is stripped from both key and value.
        - Useful for managing multiple Earthdata credentials for automated downloads.
    """
    earthdata_credentials_pool_path = Path(earthdata_credentials_pool_path).expanduser().resolve()
    earthdata_credentials_pool = {}
    with open(earthdata_credentials_pool_path, 'r') as f:
        for line in f:
            key, value = line.strip().split(':')
            earthdata_credentials_pool[key] = value
    return earthdata_credentials_pool

def clip_hyp3_s1(workdir: Path | str, aoi: list[float] | str | Path, file_suffixes=None):
    """
    Clips Hyp3 zip contents to an AOI and saves them in a MintPy-ready structure.
    
    Args:
        workdir (Path | str): Path to folder containing Hyp3 .zip files.
        aoi (list[float] | str | Path): AOI coordinates or path to AOI file (geojson/shp). Can be EPSG:4326.
        file_suffixes (list): List of file suffixes to process. 
                              Default includes standard MintPy requirements.
    """
    if file_suffixes is None:
        file_suffixes = [
            '_unw_phase.tif', '_corr.tif', '_dem.tif', 
            '_lv_theta.tif', '_lv_phi.tif', '_water_mask.tif'
        ]
    print(f"Loading AOI: {aoi}")
    raw_aoi = gpd.read_file(aoi) if isinstance(aoi, (str, Path)) else gpd.GeoDataFrame({'geometry': [box(*aoi, ccw=True)]}, crs="EPSG:4326")

    if isinstance(workdir, str):
        workdir = Path(workdir).expanduser().resolve()

    outdir = workdir.joinpath('clipped')
    outdir.mkdir(exist_ok=True)
    zip_files = list(workdir.glob("*.zip"))

    with tqdm(zip_files, unit="zip") as pbar:

        for zip_path in pbar:
            
            base_name = zip_path.stem
            pbar.set_description(f"Processing {base_name[:20]}...")
            temp_dir = outdir.joinpath("temp_processing", base_name)
            temp_dir.mkdir(parents=True, exist_ok=True)

            files_processed_count = 0
            for suffix in file_suffixes:
                internal_path = f"{base_name}/{base_name}{suffix}"
                full_zip_url = f"zip+file://{zip_path.as_posix()}!/{internal_path}"
                temp_output_path = temp_dir / f"{base_name}{suffix}"

                try:
                    with rasterio.open(full_zip_url) as src:
                        # --- CRS & CLIP ---
                        # Project AOI to match raster CRS
                        aoi_projected = raw_aoi.to_crs(src.crs)

                        # Clip
                        # If AOI doesn't overlap, mask() raises ValueError
                        out_image, out_transform = mask(src, aoi_projected.geometry, crop=True)
                        out_meta = src.meta.copy()

                        pred = 3 if "float" in out_meta['dtype'] else 2

                        out_meta.update({
                            "driver": "GTiff",
                            "height": out_image.shape[1],
                            "width": out_image.shape[2],
                            "transform": out_transform,
                            "compress": "deflate", 
                            "predictor": pred,
                            "tiled": True
                        })

                        with rasterio.open(temp_output_path, "w", **out_meta) as dest:
                            dest.write(out_image)

                        files_processed_count += 1
                except ValueError:
                # Skip if file missing in zip or AOI doesn't overlap
                    pass


            try:
                with zipfile.ZipFile(zip_path, 'r') as z:
                    # The file is usually named "S1AA_... .txt"
                    txt_filename = f"{base_name}/{base_name}.txt"
                    try:
                        # Extract to temp_dir, but zipfile extracts with full path structure
                        # so we need to move it or read/write it manually to flatten structure
                        source = z.read(txt_filename)
                        with open(temp_dir / f"{base_name}.txt", 'wb') as f:
                            f.write(source)
                    except KeyError:
                        tqdm.write(f"Warning: {txt_filename} not found in {zip_path.name}")
            except Exception as e:
               tqdm.write(f"Error reading zip {zip_path.name}: {e}")

            if files_processed_count > 0:
                new_zip_path = outdir / zip_path.name
                
                with zipfile.ZipFile(new_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for file_path in temp_dir.glob("*"):
                        # Force internal zip structure to use forward slashes
                        # irrespective of the OS running the script
                        arcname = f"{base_name}/{file_path.name}"
                        zf.write(file_path, arcname=arcname)
                
            else:
                 tqdm.write(f"Skipping {base_name} (No AOI overlap)")

            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    parent_temp = outdir / "temp_processing"
    if parent_temp.exists():
        shutil.rmtree(parent_temp)

    print("\nBatch Processing Complete.")


@dataclass
class Slurmjob_Config:
    """Configuration for a SLURM job submission script.
    
    This class encapsulates all parameters needed to generate a SLURM batch script,
    including resource allocation, job settings, environment configuration, and
    execution commands.
    
    Attributes:
        job_name: Name of the SLURM job.
        output_file: Path for standard output. Use %j for job ID.
        error_file: Path for standard error. Use %j for job ID.
        time: Maximum wall time in HH:MM:SS format.
        partition: SLURM partition name to submit to.
        nodes: Number of nodes to allocate.
        ntasks: Number of tasks to run.
        cpus_per_task: CPUs per task.
        mem: Memory allocation per node (e.g., "4G", "500M").
        nodelist: Specific nodes to use (e.g., "node[01-05]").
        gpus: GPU allocation (e.g., "1", "2", "1g").
        array: Array job specification (e.g., "0-9", "1-100%10").
        dependency: Job dependency condition (e.g., "afterok:123456").
        mail_user: Email address for job notifications.
        mail_type: When to send email notifications (BEGIN, END, FAIL, ALL).
        account: Account to charge resources to.
        qos: Quality of Service specification.
        modules: List of environment modules to load.
        conda_env: Name of conda environment to activate.
        export_env: Dictionary of environment variables to export.
        command: Bash command(s) to execute.
    
    Examples:
        Basic job configuration:
        
        >>> config = SlurmJobConfig(
        ...     job_name="my_analysis",
        ...     time="02:00:00",
        ...     command="python analyze.py"
        ... )
        >>> config.to_script("analysis.slurm")
        PosixPath('analysis.slurm')
        
        GPU job with conda environment:
        
        >>> config = SlurmJobConfig(
        ...     job_name="training",
        ...     time="12:00:00",
        ...     mem="32G",
        ...     gpus="2",
        ...     conda_env="pytorch",
        ...     modules=["cuda/11.8"],
        ...     command="python train.py --epochs 100"
        ... )
        >>> config.to_script("train.slurm")
        PosixPath('train.slurm')
        
        Array job with environment variables:
        
        >>> config = SlurmJobConfig(
        ...     job_name="param_sweep",
        ...     array="0-99",
        ...     export_env={"PARAM_ID": "$SLURM_ARRAY_TASK_ID"},
        ...     command="python run_experiment.py $PARAM_ID"
        ... )
        >>> config.to_script()
        PosixPath('job.slurm')
    """
    job_name: str = "my_job"
    output_file: str = "job_%j.out"
    error_file: str = "job_%j.err"
    time: str = "04:00:00"
    partition: str = "all"
    nodes: int = 1
    ntasks: int = 1
    cpus_per_task: int = 1
    mem: str = "4G"
    
    # Optional parameters
    nodelist: Optional[str] = None
    gpus: Optional[str] = None
    array: Optional[str] = None
    dependency: Optional[str] = None
    mail_user: Optional[str] = None
    mail_type: str = "ALL"
    account: Optional[str] = None
    qos: Optional[str] = None
    
    # Environment
    modules: List[str] = field(default_factory=list)
    conda_env: Optional[str] = None
    export_env: Dict[str, str] = field(default_factory=dict)
    
    # Execution
    command: str = "echo Hello SLURM!"
    
    def to_script(self, filename: str = "job.slurm") -> Path:
        """Generate the SLURM script file."""
        lines = ["#!/bin/bash"]
        
        # Required directives
        lines.extend([
            f"#SBATCH --job-name={self.job_name}",
            f"#SBATCH --output={self.output_file}",
            f"#SBATCH --error={self.error_file}",
            f"#SBATCH --time={self.time}",
            f"#SBATCH --partition={self.partition}",
            f"#SBATCH --nodes={self.nodes}",
            f"#SBATCH --ntasks={self.ntasks}",
            f"#SBATCH --cpus-per-task={self.cpus_per_task}",
            f"#SBATCH --mem={self.mem}",
        ])
        
        # Optional directives
        if self.gpus:
            lines.append(f"#SBATCH --gres=gpu:{self.gpus}")
        if self.array:
            lines.append(f"#SBATCH --array={self.array}")
        if self.dependency:
            lines.append(f"#SBATCH --dependency={self.dependency}")
        if self.mail_user:
            lines.append(f"#SBATCH --mail-user={self.mail_user}")
            lines.append(f"#SBATCH --mail-type={self.mail_type}")
        if self.account:
            lines.append(f"#SBATCH --account={self.account}")
        if self.qos:
            lines.append(f"#SBATCH --qos={self.qos}")
        if self.nodelist:
            lines.append(f"#SBATCH --nodelist={self.nodelist}")
        
        lines.append("")
        
        # Environment setup
        lines.extend([f"module load {mod}" for mod in self.modules])
        if self.conda_env:
            lines.append(f"source activate {self.conda_env}")
        lines.extend([f"export {k}={v}" for k, v in self.export_env.items()])
        
        lines.append("")
        
        # Execution
        lines.extend([
            'echo "Starting job on $(date)"',
            self.command,
            'echo "Job finished on $(date)"'
        ])
        
        filepath = Path(filename).expanduser().resolve()
        filepath.write_text("\n".join(lines))

        return filepath


# Pattern for an ASF-style granule name (Sentinel-1, ALOS, etc.):
# no spaces, at least 20 chars, alphanumeric + underscores/hyphens.
_SCENE_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_\-]{19,}$')


def _extract_scene_names(tokens: list[str]) -> list[str]:
    """Filter *tokens* to those matching the granule-name pattern, strip extensions."""
    seen: dict[str, None] = {}
    for token in tokens:
        token = token.strip()
        if not token or token.startswith('#'):
            continue
        stem = Path(token).stem if '.' in token else token
        if _SCENE_NAME_RE.match(stem):
            seen[stem] = None
    return list(seen.keys())


def parse_scene_names_from_file(file_path: str) -> list[str]:
    """Read a file and return a deduplicated list of clean scene/granule names.

    Supported formats:

    * **CSV** (``.csv``) — scene names may appear in any column.
    * **Excel** (``.xlsx``, ``.xls``) — all sheets are scanned; names may be
      in any cell.
    * **Plain text** (``.txt``, or any other extension) — one token per line;
      whitespace-separated tokens on a line are each tested.

    Extensions such as ``.zip`` or ``.SAFE`` are stripped automatically, so
    the returned names are ready for ``ASF_Base_Downloader.search_by_name()``.
    Lines / cells that are blank or start with ``#`` are ignored.

    Args:
        file_path: Path to the input file.

    Returns:
        Deduplicated list of scene name strings.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If no valid scene names were found, or the format is
            unsupported and cannot be parsed.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    tokens: list[str] = []

    if suffix == '.csv':
        import csv as _csv
        with open(path, newline='', encoding='utf-8-sig') as fh:
            for row in _csv.reader(fh):
                tokens.extend(row)

    elif suffix in ('.xlsx', '.xls'):
        try:
            import openpyxl as _openpyxl
        except ImportError as exc:
            raise ImportError(
                "openpyxl is required to read Excel files: pip install openpyxl"
            ) from exc
        wb = _openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    if cell is not None:
                        tokens.append(str(cell))
        wb.close()

    else:
        # Plain text or unknown extension — split on whitespace / newlines
        text = path.read_text(encoding='utf-8-sig', errors='replace')
        tokens = text.split()

    names = _extract_scene_names(tokens)
    if not names:
        raise ValueError(f"No valid scene names found in {path}")
    return names


def parse_scene_names_from_csv(csv_path: str) -> list[str]:
    """Convenience wrapper — parse scene names from a CSV file.

    Calls :func:`parse_scene_names_from_file` internally.  Prefer that
    function for new code, as it also handles ``.xlsx`` and ``.txt`` files.
    """
    return parse_scene_names_from_file(csv_path)

