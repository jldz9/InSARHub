# -*- coding: utf-8 -*-
"""
Unified disk cache for pair-quality feature data.

All extractor modules share a single JSON file per folder:
    <folder>/.insarhub_quality_cache.json

Structure
---------
{
  "_schema_version": 2,
  "geometry":   { "<aoi_hash>": { ..., "_fetched_at": "ISO-8601" } },
  "landcover":  { "<aoi_hash>": { ..., "_fetched_at": "ISO-8601" } },
  "snow_modis": { "<lat>:<lon>:<date>": { ... } },
  "weather":    { "<lat>:<lon>:<date>": { ... } },
  "veg":        { "<source>:<lat>:<lon>:<year>:<month>": { ... } }
}

TTLs
----
  geometry / landcover : 365 days  (static data, but allow annual refresh)
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
_SCHEMA_VERSION = 2

# Sections that have a TTL; others never expire.
_TTL_DAYS: dict[str, int] = {
    "geometry":  365,
    "landcover": 365,
}


# ── Public helpers ────────────────────────────────────────────────────────────

def aoi_hash(wkt: str) -> str:
    """Return an 8-character SHA-256 digest of the WKT string."""
    return hashlib.sha256(wkt.encode()).hexdigest()[:8]


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
            return raw
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
