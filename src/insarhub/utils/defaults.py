# -*- coding: utf-8 -*-
"""
Single source of truth for shared default parameter values.

Import from here instead of repeating literals in function signatures,
Pydantic models, argparse definitions, and batch scripts.
"""

# ── Interferogram pair selection ──────────────────────────────────────────────

SELECT_PAIRS_DEFAULTS: dict = {
    "dt_targets":             (6, 12, 24, 36, 48, 72, 96),
    "dt_tol":                 3,
    "dt_max":                 120,
    "pb_max":                 150.0,
    "min_degree":             3,
    "max_degree":             5,
    "force_connect":          True,
    "max_workers":            8,
    "avoid_low_quality_days": True,
    "snow_threshold":         0.5,
    "precip_mm_threshold":    25.0,
}

# ── File download ─────────────────────────────────────────────────────────────

DOWNLOAD_DEFAULTS: dict = {
    "max_workers": 3,   # concurrent file downloads (ASF rate-limit friendly)
}

# ── Fallback AOI when no config can be found ──────────────────────────────────
# Represents a generic mid-latitude location (France/central Europe).
# Used as a last resort so code never crashes on missing geometry.

FALLBACK_AOI: dict = {
    "lat": 45.0,
    "lon": 0.0,
    "wkt": "POLYGON ((0 45, 1 45, 1 46, 0 46, 0 45))",
}

# ── Season mapping (Northern Hemisphere) ─────────────────────────────────────
# Used by pair-quality scoring, coherence modelling, and feature assembly.
# "autumn" is used consistently (not "fall").

SEASON_NH: dict[int, str] = {
    12: "winter",  1: "winter",  2: "winter",
     3: "spring",  4: "spring",  5: "spring",
     6: "summer",  7: "summer",  8: "summer",
     9: "autumn", 10: "autumn", 11: "autumn",
}

SEASON_ADJACENT: set = {
    frozenset({"winter", "spring"}),
    frozenset({"spring", "summer"}),
    frozenset({"summer", "autumn"}),
    frozenset({"autumn", "winter"}),
}
