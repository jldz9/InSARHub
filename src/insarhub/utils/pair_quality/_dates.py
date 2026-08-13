# -*- coding: utf-8 -*-
"""Shared, sensor-agnostic date parsing for pair-quality scoring.

SLC and BURST products carry their acquisition date in different places:

- SLC scene name : ``S1A_IW_SLC__1SDV_YYYYMMDDTHHMMSS_...``  (chars 17–24)
- BURST granule  : ``S1_118971_IW2_YYYYMMDDTHHMMSS_VV_DA9D-BURST`` (``[2]`` token)
- bare date id   : ``20250108`` (burst stacks key nodes by the date itself)

These helpers return an ISO ``YYYY-MM-DD`` (or ``YYYYMMDD``) string for any of
the three, so the pair-quality stack (``_db``, ``_baselines``,
``pair_quality``, ``_coherence``) never mangles burst dates the way a fixed
``name[17:25]`` slice does.
"""

from __future__ import annotations

import re


def _iso_ymd(ymd8: str) -> str:
    """'YYYYMMDD' -> 'YYYY-MM-DD'."""
    return f"{ymd8[:4]}-{ymd8[4:6]}-{ymd8[6:8]}"


def _date_id(name: str) -> str:
    """Return the ``YYYYMMDD`` date embedded in *name*, or ``""``.

    Accepts a bare date id, a full SLC scene name, or a SLC-BURST granule name.
    """
    if not name:
        return ""
    if re.fullmatch(r"\d{8}", name):
        return name
    # BURST granule: S1_118971_IW2_20260721T130904_VV_DA9D-BURST
    m = re.search(r"_(\d{8})T\d{6}_", name)
    if m:
        return m.group(1)
    # SLC scene name: S1A_IW_SLC__1SDV_20260721T130904_...
    if len(name) > 25:
        raw = name[17:25]
        if re.fullmatch(r"\d{8}", raw):
            return raw
    return ""


def _overpass_hour(name: str) -> int | None:
    """UTC hour of the acquisition, from a bare id / SLC name / BURST granule."""
    if not name:
        return None
    if re.fullmatch(r"\d{8}", name):
        return None                     # bare date has no time-of-day
    m = re.search(r"_(\d{8})T(\d{2})\d{4}_", name)
    if m:
        return int(m.group(2))
    if len(name) >= 28 and name[25] == "T":
        try:
            return int(name[26:28])
        except ValueError:
            return None
    return None


def scene_date(name: str) -> str:
    """ISO ``YYYY-MM-DD`` acquisition date for a bare id / SLC / BURST name."""
    d = _date_id(name)
    return _iso_ymd(d) if d else ""


def scene_date_compact(name: str) -> str:
    """``YYYYMMDD`` acquisition date for a bare id / SLC / BURST name."""
    return _date_id(name)


def scene_hour(name: str) -> int | None:
    """UTC overpass hour for a bare id / SLC / BURST name."""
    return _overpass_hour(name)
