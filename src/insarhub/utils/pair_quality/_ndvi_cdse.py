# -*- coding: utf-8 -*-
"""
NDVI high-resolution layer: Copernicus Data Space Ecosystem (CDSE) Sentinel-2 L2A.

Uses the CDSE Sentinel Hub Process API to evaluate an NDVI evalscript at a
point location — returns a single float per scene, no raster download.

Auth
----
Reads CDSE credentials from ~/.netrc (machine dataspace.copernicus.eu).
Falls back gracefully if credentials are absent.

Resolution / revisit
--------------------
Sentinel-2 L2A: 10 m spatial, ~5-day revisit (combined S2A+S2B).
"""

from __future__ import annotations

import json
import logging
import netrc
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_CDSE_HOST        = "dataspace.copernicus.eu"
_TOKEN_URL        = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_STAC_URL         = "https://catalogue.dataspace.copernicus.eu/stac/collections/SENTINEL-2/items"
_PROCESS_URL      = "https://sh.dataspace.copernicus.eu/api/v1/process"
_SEARCH_WINDOW    = 15   # days either side of target date
_MAX_CLOUD        = 80   # % cloud cover upper limit for candidate scenes

_EVALSCRIPT = """\
//VERSION=3
function setup() {
  return { input: ["B04", "B08", "dataMask"], output: { bands: 1 } };
}
function evaluatePixel(s) {
  if (!s.dataMask) return [-9999];
  var denom = s.B08 + s.B04;
  if (denom === 0) return [-9999];
  return [(s.B08 - s.B04) / denom];
}
"""


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _cdse_creds() -> Optional[tuple[str, str]]:
    """Return (username, password) from ~/.netrc or None."""
    try:
        nrc = netrc.netrc()
        auth = nrc.authenticators(_CDSE_HOST)
        if auth:
            return auth[0], auth[2]
    except Exception:
        pass
    return None


def _get_token(username: str, password: str) -> Optional[str]:
    """Exchange username/password for a short-lived bearer token."""
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id":  "cdse-public",
        "username":   username,
        "password":   password,
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read())
            return payload.get("access_token")
    except Exception as exc:
        logger.warning("CDSE token request failed: %s", exc)
        return None


# ── STAC scene search ─────────────────────────────────────────────────────────

def _search_scenes(lat: float, lon: float,
                   date: str, token: str) -> list[dict]:
    """Return STAC items for S2 L2A covering (lat, lon) within ±SEARCH_WINDOW days."""
    dt = datetime.fromisoformat(date)
    start = (dt - timedelta(days=_SEARCH_WINDOW)).strftime("%Y-%m-%dT00:00:00Z")
    end   = (dt + timedelta(days=_SEARCH_WINDOW)).strftime("%Y-%m-%dT23:59:59Z")

    params = urllib.parse.urlencode({
        "bbox":              f"{lon-0.01},{lat-0.01},{lon+0.01},{lat+0.01}",
        "datetime":          f"{start}/{end}",
        "collections":       "SENTINEL-2",
        "filter":            f"eo:cloud_cover < {_MAX_CLOUD} AND s2:processing_baseline >= '04.00'",
        "filter-lang":       "cql2-text",
        "limit":             20,
        "sortby":            "eo:cloud_cover",
    })
    url = f"{_STAC_URL}?{params}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("features", [])
    except Exception as exc:
        logger.warning("CDSE STAC search failed for %s: %s", date, exc)
        return []


def _pick_best(scenes: list[dict], target_date: str) -> Optional[dict]:
    """Pick scene closest in time to target_date among those with lowest cloud cover."""
    if not scenes:
        return None
    target_dt = datetime.fromisoformat(target_date)

    def _sort_key(s: dict) -> tuple:
        props = s.get("properties", {})
        cloud = props.get("eo:cloud_cover", 100)
        dt_str = props.get("datetime", "")[:10]
        try:
            diff = abs((datetime.fromisoformat(dt_str) - target_dt).days)
        except ValueError:
            diff = 999
        return (cloud // 20, diff)  # bucket cloud by 20%, then prefer closer date

    return sorted(scenes, key=_sort_key)[0]


# ── Process API call ──────────────────────────────────────────────────────────

def _eval_ndvi(lat: float, lon: float, scene: dict, token: str) -> Optional[float]:
    """Call Process API with evalscript; return NDVI float or None."""
    props    = scene.get("properties", {})
    scene_dt = props.get("datetime", "")[:10]

    payload = json.dumps({
        "input": {
            "bounds": {
                "bbox": [lon - 0.005, lat - 0.005, lon + 0.005, lat + 0.005],
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [{
                "dataFilter": {
                    "timeRange": {
                        "from": f"{scene_dt}T00:00:00Z",
                        "to":   f"{scene_dt}T23:59:59Z",
                    },
                    "maxCloudCoverage": _MAX_CLOUD,
                },
                "type": "sentinel-2-l2a",
            }],
        },
        "output": {
            "width": 1, "height": 1,
            "responses": [{"identifier": "default",
                           "format": {"type": "application/json"}}],
        },
        "evalscript": _EVALSCRIPT,
    }).encode()

    req = urllib.request.Request(
        _PROCESS_URL, data=payload,
        headers={
            "Authorization":  f"Bearer {token}",
            "Content-Type":   "application/json",
            "Accept":         "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            # Response is a nested list [[ndvi_value]]
            val = result[0][0] if result and result[0] else None
            if val is None or val == -9999:
                return None
            return float(max(0.0, min(1.0, val)))
    except Exception as exc:
        logger.warning("CDSE Process API failed: %s", exc)
        return None


# ── Public interface ──────────────────────────────────────────────────────────

def fetch_cdse_ndvi_batch(
    lat: float, lon: float,
    dates: list[str],
) -> dict[str, float]:
    """Fetch Sentinel-2 NDVI at (lat, lon) for each date in one token session.

    Returns dict mapping date string (YYYY-MM-DD) → NDVI float [0, 1].
    Returns empty dict if CDSE credentials are not available or all calls fail.
    """
    creds = _cdse_creds()
    if not creds:
        logger.debug("No CDSE credentials found; skipping high-res NDVI")
        return {}

    token = _get_token(*creds)
    if not token:
        return {}

    result: dict[str, float] = {}
    for date in dates:
        scenes = _search_scenes(lat, lon, date, token)
        scene  = _pick_best(scenes, date)
        if scene is None:
            logger.debug("No S2 scene found near %s for %s", date, (lat, lon))
            continue
        ndvi = _eval_ndvi(lat, lon, scene, token)
        if ndvi is not None:
            result[date] = ndvi

    return result


def cdse_creds_available() -> bool:
    """Return True if CDSE credentials exist in ~/.netrc."""
    return _cdse_creds() is not None
