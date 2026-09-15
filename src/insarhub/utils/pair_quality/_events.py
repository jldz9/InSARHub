# -*- coding: utf-8 -*-
"""
Pair quality as a list of events, not a score.

What this replaces
------------------
The old model computed a weighted 0-100 number and cut it into good / risky /
bad at two thresholds.  That number was not interpretable: a pair scored 47
because of some combination of a dozen weighted terms, and nothing in the
output said which.  Worse, the weights implied a precision the inputs do not
support — several of them were multiplied by features that were permanently
``None``, so the arithmetic ran but contributed nothing.

This module asks a different question: **did anything extreme happen during
either acquisition?**  A pair is flagged ``concern`` when at least one
*serious* event is detected, and is ``healthy`` otherwise.  There is deliberately no third label —
"bad" implied a confidence about unusable data that environmental proxies
cannot support.  Every event carries the measurement that triggered it, so the
answer is always traceable to a number a user can check.

What counts as extreme
----------------------
Thresholds below are grounded in the C-band literature where the literature
actually commits to one, and calibrated against the data otherwise.  Each
constant carries its justification.  Where no canonical threshold exists —
notably soil moisture and rainfall — the detector uses the *distribution of
the stack's own AOI* rather than inventing a universal number, because the
same absolute change means very different things in different climates (see
:func:`calibrate`).

Severity
--------
``serious`` — mechanisms that collapse C-band coherence outright.
``minor``   — mechanisms that degrade it or that are proxies with known
              false-positive rates.  Recorded and displayed, but a pair made
              only of minor events stays ``healthy``.

Units, which are not uniform and have bitten this code before
-------------------------------------------------------------
``snow_depth`` m · ``snowfall`` cm · ``rain``/``precip`` mm ·
``soil_moisture`` m³/m³ · ``temp``/``soil_temp`` °C · ``wind_gust`` km/h
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SERIOUS = "serious"
MINOR   = "minor"

HEALTHY = "healthy"
CONCERN   = "concern"


# ── Thresholds ────────────────────────────────────────────────────────────────

# Wet snow. The canonical C-band detector is a 3 dB backscatter drop relative
# to a dry-snow reference (Nagler & Rott 2000, IEEE TGRS 38(2):754-765): liquid
# water raises the dielectric loss so far that penetration collapses from
# metres to a few centimetres. Backscatter is not available here, so the
# trigger is the condition that produces it — snow on the ground with the
# surface at or above freezing. This is the single most destructive C-band
# mechanism, hence serious at a low depth threshold.
WET_SNOW_MIN_DEPTH_M   = 0.01    # 1 cm — enough to dominate the return
WET_SNOW_MIN_TEMP_C    = 0.0

# Dry snow. C-band penetrates dry snow well, so depth alone is a much weaker
# signal than wetness; volume scattering only becomes significant in a deep
# pack. Serious only well beyond the wet-snow depth.
DEEP_SNOW_M            = 0.25    # 25 cm
# Fresh snowfall resurfaces the scene between passes. Minor: a light dusting
# frequently leaves coherence intact.
FRESH_SNOWFALL_CM      = 1.0

# Snow depth CHANGE between the two acquisitions — a different question from
# how much snow is lying on either date. A pack that is deep but unchanged is
# comparatively benign (it is the same surface twice); a pack that grew or
# melted between passes is not.
#
# The scale comes from the InSAR SWE literature, which reads the same physics
# from the other side: dry snow adds a path delay proportional to SWE, and for
# C-band Sentinel-1 the ΔSWE ambiguity — one full 2-pi phase cycle — is
# 1.5-3.5 cm depending on incidence angle (Snow water equivalent retrieval over
# Idaho Part 1, The Cryosphere 18:559, 2024, which also accepts pixels only
# above gamma 0.35). Below one ambiguity the change is recoverable signal;
# beyond it the phase aliases, and where the accumulation is spatially uneven
# the pair decorrelates outright — the same work reports that "snowstorms
# reduce the temporal coherence significantly".
#
# ERA5 gives depth, not SWE, and Open-Meteo's snow_depth_water_equivalent is
# accepted by the API but returns all nulls with unit "undefined", so the
# conversion is done here with a bulk seasonal-snowpack density. 300 kg/m3 is
# mid-range for a settled pack (fresh 50-150, settled 200-400, ripe 350-500),
# which puts one C-band ambiguity at roughly 5-12 cm of depth.
DELTA_SNOW_SERIOUS_M = 0.10   # ~3 cm SWE at 300 kg/m3 — a full ambiguity
DELTA_SNOW_MINOR_M   = 0.05   # ~1.5 cm SWE — the low end of the ambiguity
SNOW_DENSITY_KGM3    = 300.0

# Freeze/thaw. Soil permittivity falls from ~15-25 to ~4-5 on freezing, and
# C-band is strongly sensitive to that transition (Rignot & Way 1994, RSE;
# corroborated by later Sentinel-1 freeze/thaw work). The state change between
# the two acquisitions is what matters, not the absolute temperature.
FREEZE_THAW_C          = 0.0

# Wind. Canopy motion decorrelates vegetated scenes between passes. No
# canonical C-band threshold exists, so this is set from the data: the variable
# is a DAILY MAXIMUM gust, which is much higher than a typical wind speed, and
# a first attempt at 40 km/h (Beaufort 6) turned out to be near the median —
# it fired on 63%/54%/41% of days at three test sites and appeared on almost
# every pair, which makes an event worthless. 70 km/h (Beaufort 8, gale) fires
# on 6%/2%/3% of days at the same three, rare and consistent across climates.
# Minor regardless: it is a proxy for a mechanism that only bites over tall
# vegetation, which this module cannot see now that land cover is not fetched.
WIND_GUST_KMH          = 70.0

# Expected coherence from the S1 global decay model (Kellndorfer et al. 2022,
# Sci Data 9:73), evaluated at this pair's temporal baseline. Practice in
# Sentinel-1 time-series processing puts usable coherence around 0.3-0.45:
# MintPy time-series inversion is commonly run at 0.4, land-cover-adaptive
# schemes use 0.35-0.45, and network selection floors sit near 0.12. A pair
# the decay curve already predicts below the inversion floor is flagged
# regardless of weather — nothing that happens on the day will rescue it.
COH_SERIOUS            = 0.20
COH_MINOR              = 0.35

# Rain and soil moisture have NO canonical C-band threshold in the literature
# — the searches that turned up 3 dB for wet snow and 0 degC for freeze/thaw
# return only qualitative statements for these two ("precipitation events
# affect soil moisture, which affects coherence"). Fixed millimetre and
# m³/m³ cut-offs would therefore be invented, and measurably climate-biased:
# a 12-day soil-moisture change of 0.10 m³/m³ occurs in 6% of summer pairs in
# semi-arid Utah and 42% in Iowa cropland. So these two are calibrated per AOI
# from the stack's own distribution — see calibrate(). The fallbacks below are
# used only when a stack is too short to calibrate against.
#
# What "heavy_rain" means here, since the name invites a meteorological reading:
# it is NOT the WMO classification. WMO grades intensity per hour (heavy =
# 7.6-50 mm/h) and daily totals put "heavy" at roughly 31-70 mm/day. Those
# describe how the rain felt; what matters to C-band is whether enough water
# reached the surface to change its dielectric and roughness between two
# passes, which is a lower bar and strongly place-dependent. Calibration
# against real AOIs bears that out — the p97 of 3-day rain came to 9 mm at a
# semi-arid Utah site, 21 mm in Iowa, 23 mm in the Netherlands and 83 mm in
# monsoon Kerala. A single WMO-derived constant would be silent in three of
# those four places.
#
# The event is not redundant with soil_moisture_change either: measured over
# 435 pairs per site, heavy_rain alone flagged 16 (Utah), 29 (Iowa), 29 (NL)
# and 19 (Kerala) pairs that the soil-moisture rule did not — rain also ponds,
# wets canopy and redistributes surface material, none of which shows up as a
# 0-7 cm moisture step.
FALLBACK_RAIN_3DAY_MM       = 20.0
FALLBACK_RAIN_3DAY_MINOR_MM = 5.0
FALLBACK_DELTA_SM           = 0.10
FALLBACK_DELTA_SM_MINOR     = 0.05

# Percentiles used when calibrating against the AOI's own distribution.
#
# These are high on purpose. A threshold at the Nth percentile of *dates* does
# not flag (100-N)% of *pairs*: every flagged date appears in N-1 of the
# N(N-1)/2 pairs, so flagging 10% of dates in a 30-scene stack reaches ~20% of
# pairs. Calibrating at p90 flagged 72% of pairs at a seasonal-snow site, which
# is not a useful label. p97/p90 keeps "extreme" genuinely rare.
CALIBRATION_SERIOUS_PCT = 97
CALIBRATION_MINOR_PCT   = 90
CALIBRATION_MIN_SAMPLES = 12


@dataclass(frozen=True)
class Event:
    """One extreme condition detected for a pair.

    ``detail`` carries the measurement that fired the rule, so a user can
    always see why a pair was flagged rather than trusting a number.
    """

    kind:     str
    severity: str
    detail:   str
    date:     str | None = None      # None for pair-level events

    def as_dict(self) -> dict:
        return {"kind": self.kind, "severity": self.severity,
                "detail": self.detail, "date": self.date}


@dataclass
class Thresholds:
    """Per-AOI thresholds for the two quantities the literature leaves open."""

    rain_3day_serious: float = FALLBACK_RAIN_3DAY_MM
    rain_3day_minor:   float = FALLBACK_RAIN_3DAY_MINOR_MM
    delta_sm_serious:  float = FALLBACK_DELTA_SM
    delta_sm_minor:    float = FALLBACK_DELTA_SM_MINOR
    calibrated:        bool  = False
    notes:             dict  = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "rain_3day_serious": round(self.rain_3day_serious, 3),
            "rain_3day_minor":   round(self.rain_3day_minor, 3),
            "delta_sm_serious":  round(self.delta_sm_serious, 4),
            "delta_sm_minor":    round(self.delta_sm_minor, 4),
            "calibrated":        self.calibrated,
            **self.notes,
        }


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile. Avoids a numpy import for a few dozen values."""
    if not values:
        raise ValueError("empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * (pct / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def calibrate(weather_by_date: dict[str, dict]) -> Thresholds:
    """Derive rain and soil-moisture thresholds from this AOI's own record.

    Both quantities lack a published C-band threshold, and a fixed one is
    climate-biased — measured over summer 2021, a 12-day soil-moisture change
    of at least 0.10 m³/m³ occurs in 6% of pairs at a semi-arid site and 42%
    at a humid cropland site. A single constant therefore either never fires
    in dry country or fires constantly in wet country.

    Taking the AOI's own high percentiles makes "extreme" mean *extreme for
    here*, which is what a user comparing pairs within one stack is asking.
    Falls back to the fixed constants when the stack is too short for the
    percentile to mean anything.
    """
    th = Thresholds()

    rains = [w["rain_3day"] for w in weather_by_date.values()
             if w.get("rain_3day") is not None]
    if len(rains) >= CALIBRATION_MIN_SAMPLES:
        # Floored at the fallback: in an arid AOI the high percentile of rain
        # is 0 mm, and an uncalibrated 0 would flag every faintly damp day.
        th.rain_3day_serious = max(_percentile(rains, CALIBRATION_SERIOUS_PCT),
                                   FALLBACK_RAIN_3DAY_MINOR_MM)
        th.rain_3day_minor   = max(_percentile(rains, CALIBRATION_MINOR_PCT), 1.0)
        th.calibrated = True

    # Soil moisture is thresholded on the CHANGE between two acquisitions, so
    # calibrate against the changes this AOI actually experiences between
    # consecutive acquisitions — not against the spread of the level, which
    # conflates a slow seasonal drift with the abrupt step that decorrelates.
    # Calibrating on the level spread made this event fire on half of all
    # pairs at two of three test sites.
    ordered = [w.get("soil_moisture") for _, w in sorted(weather_by_date.items())]
    deltas  = [abs(b - a) for a, b in zip(ordered, ordered[1:])
               if a is not None and b is not None]
    if len(deltas) >= CALIBRATION_MIN_SAMPLES:
        serious = _percentile(deltas, CALIBRATION_SERIOUS_PCT)
        minor   = _percentile(deltas, CALIBRATION_MINOR_PCT)
        if serious > 0:
            # Floored so a stable AOI does not flag millimetre-scale noise.
            th.delta_sm_serious = max(serious, 0.03)
            th.delta_sm_minor   = max(minor,   0.015)
            th.calibrated = True
            th.notes["soil_moisture_step_p97"] = round(serious, 4)

    th.notes["n_dates"] = len(weather_by_date)
    return th


# ── Per-date detectors ────────────────────────────────────────────────────────

def _date_events(date: str, w: dict, th: Thresholds) -> list[Event]:
    """Extreme conditions at one acquisition."""
    events: list[Event] = []
    if not w:
        return events

    depth    = w.get("snow_depth")
    snowfall = w.get("snowfall")
    temp     = w.get("temp") if w.get("temp") is not None else w.get("temp_max")

    # Wet snow — the C-band hard case. Checked before dry-snow depth so a wet
    # deep pack reports the mechanism that actually destroys coherence.
    if (depth is not None and depth >= WET_SNOW_MIN_DEPTH_M
            and temp is not None and temp >= WET_SNOW_MIN_TEMP_C):
        events.append(Event(
            "wet_snow", SERIOUS,
            f"{depth * 100:.0f} cm snow at {temp:+.1f} °C — liquid water "
            f"collapses C-band penetration", date,
        ))
    elif depth is not None and depth >= DEEP_SNOW_M:
        events.append(Event(
            "deep_snow", SERIOUS,
            f"{depth * 100:.0f} cm dry snow — volume scattering", date,
        ))

    if snowfall is not None and snowfall >= FRESH_SNOWFALL_CM:
        events.append(Event(
            "fresh_snowfall", MINOR,
            f"{snowfall:.1f} cm new snow resurfaced the scene", date,
        ))

    # Rain, liquid only — snow water equivalent is handled by the snow events.
    rain3 = w.get("rain_3day")
    if rain3 is not None:
        if rain3 >= th.rain_3day_serious:
            events.append(Event(
                "heavy_rain", SERIOUS,
                f"{rain3:.1f} mm rain over 3 days "
                f"(threshold {th.rain_3day_serious:.1f})", date,
            ))
        elif rain3 >= th.rain_3day_minor:
            events.append(Event(
                "rain", MINOR,
                f"{rain3:.1f} mm rain over 3 days", date,
            ))

    gust = w.get("wind_gust")
    if gust is not None and gust >= WIND_GUST_KMH:
        events.append(Event(
            "high_wind", MINOR,
            f"{gust:.0f} km/h gusts — canopy motion", date,
        ))

    return events


# ── Pair-level detectors ──────────────────────────────────────────────────────

def _freeze_thaw_event(w1: dict, w2: dict, d1: str, d2: str) -> Event | None:
    """Frozen/thawed state change between the two acquisitions.

    Uses soil temperature when available and falls back to air temperature,
    which is a materially worse proxy: at a Utah site over 86 consecutive
    overpasses the two disagreed on frozen/thawed state on 59% of days.
    """
    def _state(w: dict) -> tuple[bool | None, str]:
        st = w.get("soil_temp")
        if st is not None:
            return st < FREEZE_THAW_C, "soil"
        at = w.get("temp") if w.get("temp") is not None else w.get("temp_max")
        if at is not None:
            return at < FREEZE_THAW_C, "air"
        return None, "none"

    f1, src1 = _state(w1)
    f2, src2 = _state(w2)
    if f1 is None or f2 is None or f1 == f2:
        return None

    source = "soil" if src1 == src2 == "soil" else "air"
    order  = (d1, "frozen", d2, "thawed") if f1 else (d1, "thawed", d2, "frozen")
    return Event(
        "freeze_thaw", MINOR,
        f"{order[0]} {order[1]} → {order[2]} {order[3]} "
        f"({source} temperature) — permittivity shift",
    )


def _delta_snow_event(w1: dict, w2: dict, d1: str, d2: str) -> Event | None:
    """Change in snowpack depth between the two acquisitions.

    Distinct from the per-date snow events: ``deep_snow`` says a lot of snow is
    lying, ``fresh_snowfall`` says it snowed that day, and this says the two
    acquisitions did not see the same snowpack. A deep pack that is unchanged
    across the pair is comparatively benign; one that grew or melted is not.

    Reported as a depth change with the implied SWE alongside, because the
    threshold is set in SWE — see DELTA_SNOW_SERIOUS_M.
    """
    s1, s2 = w1.get("snow_depth"), w2.get("snow_depth")
    if s1 is None or s2 is None:
        return None
    delta = abs(s2 - s1)
    if delta < DELTA_SNOW_MINOR_M:
        return None

    swe_cm = delta * SNOW_DENSITY_KGM3 / 10.0      # m x kg/m3 -> mm, /10 -> cm
    direction = "accumulated" if s2 > s1 else "melted"
    detail = (f"{s1 * 100:.0f} → {s2 * 100:.0f} cm ({direction} "
              f"{delta * 100:.0f} cm, ~{swe_cm:.1f} cm SWE)")

    if delta >= DELTA_SNOW_SERIOUS_M:
        return Event(
            "delta_snow", SERIOUS,
            f"{detail} — at or beyond the C-band ΔSWE ambiguity",
        )
    return Event("delta_snow_shift", MINOR, detail)


def _soil_moisture_event(w1: dict, w2: dict, th: Thresholds) -> Event | None:
    """Change in near-surface soil moisture between the acquisitions.

    Dielectric change from moisture alters both phase and coherence (De Zan
    et al. 2014, IEEE TGRS 52(1):418-425). The threshold is per-AOI because no
    universal one exists — see calibrate().
    """
    m1, m2 = w1.get("soil_moisture"), w2.get("soil_moisture")
    if m1 is None or m2 is None:
        return None
    delta = abs(m2 - m1)
    if delta >= th.delta_sm_serious:
        return Event(
            "soil_moisture_change", SERIOUS,
            f"{m1:.3f} → {m2:.3f} m³/m³ (Δ{delta:.3f}, threshold "
            f"{th.delta_sm_serious:.3f})",
        )
    if delta >= th.delta_sm_minor:
        return Event(
            "soil_moisture_shift", MINOR,
            f"{m1:.3f} → {m2:.3f} m³/m³ (Δ{delta:.3f})",
        )
    return None


def _coherence_event(fv: dict) -> Event | None:
    """Expected coherence from the S1 global decay curve at this pair's dt.

    γ(t) = γ∞ + (γ0 − γ∞)·exp(−t/τ), fitted per pixel from the seasonal
    mosaics (Kellndorfer et al. 2022). This is the baseline the user asked to
    grade against: if the curve already puts the pair below the level at which
    time-series inversion is normally attempted, no weather on the day will
    save it.
    """
    coh = fv.get("coherence_expected")
    if coh is None:
        return None
    coh = float(coh)
    dt  = fv.get("dt_days")
    src = fv.get("coherence_source", "s3")
    where = f" at {dt:.0f}-day baseline" if dt else ""
    if coh < COH_SERIOUS:
        return Event(
            "low_coherence", SERIOUS,
            f"decay model predicts γ={coh:.2f}{where} "
            f"(below {COH_SERIOUS:.2f}; source {src})",
        )
    if coh < COH_MINOR:
        return Event(
            "marginal_coherence", MINOR,
            f"decay model predicts γ={coh:.2f}{where} (source {src})",
        )
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def detect(fv: dict, th: Thresholds | None = None) -> tuple[str, list[Event]]:
    """Return ``(status, events)`` for one pair's feature vector.

    ``status`` is :data:`CONCERN` when any event is ``serious``, else
    :data:`HEALTHY`. Events are returned most-severe first so a UI showing only
    the first one shows the one that decided the label.
    """
    th = th or Thresholds()
    d1, d2 = fv.get("date1"), fv.get("date2")
    w1, w2 = fv.get("weather_d1") or {}, fv.get("weather_d2") or {}

    events: list[Event] = []
    events += _date_events(d1, w1, th)
    events += _date_events(d2, w2, th)

    for maybe in (_coherence_event(fv),
                  _delta_snow_event(w1, w2, d1, d2),
                  _soil_moisture_event(w1, w2, th),
                  _freeze_thaw_event(w1, w2, d1, d2)):
        if maybe is not None:
            events.append(maybe)

    events.sort(key=lambda e: 0 if e.severity == SERIOUS else 1)
    status = CONCERN if any(e.severity == SERIOUS for e in events) else HEALTHY
    return status, events
