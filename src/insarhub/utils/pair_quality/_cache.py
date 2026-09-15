# -*- coding: utf-8 -*-
"""
Unified disk cache for pair-quality feature data.

All extractor modules share a single JSON file per folder:
    <folder>/.insarhub_quality_cache.json

Structure
---------
{
  "_schema_version": 3,
  "geometry":   { "<aoi_hash>": { ..., "_fetched_at": "ISO-8601" } },
  "s1_coherence": { "map": { "<cache_key>": { ... } } },
  "weather":    { "<lat>:<lon>:<date>": { ... } },   # AOI mean over the 0.1° grid
  "veg":        { "<source>:<lat>:<lon>:<year>:<month>": { ... } }
}

TTLs
----
  weather / s1_coherence : no TTL — ERA5 is immutable reanalysis and the S1
                           coherence mosaics are a fixed 2019-2020 product
  everything else      : no expiry (historical reanalysis / satellite, immutable)
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_FILE = ".insarhub_quality_cache.json"
# Schema 3 stores each weather date as the AOI-mean over the native 0.1° sample
# grid. Schema 2 held a single centroid-cell reading under the same key, so it
# is discarded rather than mixed with means.
_SCHEMA_VERSION = 3

# Sections that have a TTL; others never expire. Both live sections hold
# immutable data — ERA5 reanalysis and a fixed 2019-2020 coherence product —
# so nothing expires today. The table stays because a future mutable source
# would need one, and because its absence is what let failed fetches persist
# forever before has_measurements() was introduced.
_TTL_DAYS: dict[str, int] = {}


# ── Public helpers ────────────────────────────────────────────────────────────

def aoi_hash(wkt: str) -> str:
    """Return an 8-character SHA-256 digest of the WKT string."""
    return hashlib.sha256(wkt.encode()).hexdigest()[:8]


def has_measurements(feats: dict | None) -> bool:
    """True when *feats* carries at least one real value from a data source.

    A failed weather/snow fetch used to produce a dict of all-None fields.
    That dict is *truthy*, so a plain ``if feats:`` accepted it and wrote the
    failure into the cache — where, since the weather and snow sections have
    no TTL, it stayed forever and every later run read nulls back as if they
    were measurements. Anything deciding whether data is worth persisting has
    to ask this question, not test the dict for emptiness.
    """
    if not feats:
        return False
    return any(
        value is not None
        for key, value in feats.items()
        if not key.startswith("_") and key != "snow_source"
    )


def _drop_empty_measurements(raw: dict[str, Any]) -> dict[str, Any]:
    """Discard weather/snow entries that hold no measurement.

    Self-heal for caches written before failed fetches stopped being cached.
    Those entries are all-None, never expire (neither section has a TTL), and
    are read back as real "no snow, no rain" readings — so a folder scored
    during an outage stayed wrong forever. Dropping them on load turns that
    into a re-fetch on the next run. Sections holding genuinely expensive
    results (the S1 coherence decay maps) are left untouched.
    """
    dropped = 0
    for section in ("weather", "snow_modis"):
        entries = raw.get(section)
        if not isinstance(entries, dict):
            continue
        for key in [k for k, v in entries.items() if not has_measurements(v)]:
            del entries[key]
            dropped += 1
    if dropped:
        logger.warning(
            "Discarded %d cached weather/snow entries with no data — these were "
            "recorded from a failed fetch and will be re-fetched", dropped,
        )
    return raw


class CacheManager:
    """Read/write the quality cache for a single folder."""

    def __init__(self, folder: Path, force_refresh: bool = False):
        self._path = folder / CACHE_FILE
        self._force = force_refresh
        self._data: dict[str, Any] = self._load()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self._force or not self._path.exists():
            return {"_schema_version": _SCHEMA_VERSION}
        try:
            raw = json.loads(self._path.read_text())
            if raw.get("_schema_version") != _SCHEMA_VERSION:
                logger.info("Cache schema mismatch — starting fresh")
                return {"_schema_version": _SCHEMA_VERSION}
            return _drop_empty_measurements(raw)
        except Exception as exc:
            logger.warning("Could not read quality cache: %s", exc)
            return {"_schema_version": _SCHEMA_VERSION}

    def _section(self, section: str) -> dict:
        return self._data.setdefault(section, {})

    def _is_expired(self, section: str, value: dict) -> bool:
        ttl = _TTL_DAYS.get(section)
        if ttl is None:
            return False
        fetched_at = value.get("_fetched_at")
        if not fetched_at:
            return True
        try:
            age = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(fetched_at)).days
            return age > ttl
        except Exception:
            return True

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, section: str, key: str) -> dict | None:
        """Return cached value or None if missing / expired."""
        value = self._section(section).get(key)
        if value is None:
            return None
        if self._is_expired(section, value):
            return None
        return value

    def set(self, section: str, key: str, value: dict) -> None:
        """Store *value* under *section*/*key*, stamping _fetched_at."""
        if "_fetched_at" not in value and section in _TTL_DAYS:
            value = {**value, "_fetched_at": datetime.now(timezone.utc).isoformat()}
        self._section(section)[key] = value

    def save(self) -> None:
        """Flush the in-memory cache to disk."""
        try:
            self._path.write_text(json.dumps(self._data, indent=2))
        except Exception as exc:
            logger.warning("Could not save quality cache: %s", exc)
