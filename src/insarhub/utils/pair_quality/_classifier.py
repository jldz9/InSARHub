# -*- coding: utf-8 -*-
"""
Pair classification: healthy or concern, decided by detected events.

This is the thin layer between :mod:`_feature_assembler`, which gathers
measurements, and :mod:`_events`, which decides what counts as extreme. It
exists to produce the ``factors`` record that gets stored and displayed — the
events that fired, plus the raw measurements behind them, so a label can
always be traced back to numbers.

Replaces the weighted 0-100 score
---------------------------------
The previous implementation had three scoring functions (flat, land-cover
branching, coherence-based), each summing weighted normalised features into a
number that was then cut into good / risky / bad. That is gone, along with
``_scorer.py`` and ``_lc_scorer.py``. Two reasons beyond the interpretability
argument in :mod:`_events`:

* Several weighted terms were multiplied by features that were always
  ``None`` — ``snow_cover_frac`` in particular, which the batch fetch never
  populated — so the snow terms contributed exactly zero for every pair while
  appearing in the output as though they had been evaluated.
* The land-cover and NDVI branches needed WorldCover, MODIS and DEM fetches.
  Those data sources are no longer pulled; the pipeline is Open-Meteo plus the
  S1 global coherence dataset and nothing else.
"""

from __future__ import annotations

import logging

from insarhub.utils.pair_quality import _events
from insarhub.utils.pair_quality._events import Thresholds

logger = logging.getLogger(__name__)

__all__ = ["classify", "Thresholds", "calibrate"]

calibrate = _events.calibrate


def classify(fv: dict, thresholds: Thresholds | None = None) -> tuple[str, dict]:
    """Return ``(status, factors)`` for one pair.

    status
        ``"healthy"``, or ``"concern"`` when at least one serious event was
        detected.
    factors
        The events, plus the measurements they were derived from. Everything
        needed to explain the label without re-fetching anything.
    """
    status, events = _events.detect(fv, thresholds)

    w1 = fv.get("weather_d1") or {}
    w2 = fv.get("weather_d2") or {}

    def _obs(w: dict) -> dict:
        """The measurements behind the events, for display and audit."""
        return {
            "temp":          w.get("temp"),
            "soil_temp":     w.get("soil_temp"),
            "soil_moisture": w.get("soil_moisture"),
            "snow_depth":    w.get("snow_depth"),
            "snowfall":      w.get("snowfall"),
            "rain_3day":     w.get("rain_3day"),
            "precip_3day":   w.get("precip_3day"),
            "wind_gust":     w.get("wind_gust"),
        }

    factors: dict = {
        "status": status,
        "events": [e.as_dict() for e in events],
        # Flattened for callers that only want to know whether to worry.
        "serious_events": [e.kind for e in events if e.severity == _events.SERIOUS],

        # Context — shown alongside the label, never used to set it.
        "dt_days":            fv.get("dt_days"),
        "bperp_diff":         fv.get("bperp_diff"),
        "coherence_expected": fv.get("coherence_expected"),
        "coherence_abs":      fv.get("coherence_abs"),
        "coherence_source":   fv.get("coherence_source", "none"),

        "observations": {
            fv.get("date1"): _obs(w1),
            fv.get("date2"): _obs(w2),
        },
    }
    return status, factors
