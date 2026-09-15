"""Contract tests for event-based pair quality.

A pair is ``concern`` when at least one *serious* extreme condition was detected
at either acquisition, and ``healthy`` otherwise. There is no score and no
third label. These tests pin the rules that decide that verdict, the units the
rules are written in, and the two data sources the whole subsystem is allowed
to reach.

They are contract tests rather than bug regressions: the questions they answer
("can wet snow ever fail to flag a pair?", "is any other host contacted?")
must keep holding for every future version, not just prove one report fixed.
"""

from __future__ import annotations

import pytest

from insarhub.utils.pair_quality import _events
from insarhub.utils.pair_quality._events import HEALTHY, MINOR, CONCERN, SERIOUS


def _w(**kw) -> dict:
    """A weather record with everything benign unless overridden."""
    base = {
        "temp": 10.0, "temp_max": 12.0, "temp_min": 5.0, "soil_temp": 9.0,
        "precip": 0.0, "rain": 0.0, "rain_3day": 0.0, "precip_3day": 0.0,
        "precip_7day": 0.0, "snow_depth": 0.0, "snowfall": 0.0,
        "soil_moisture": 0.20, "wind_gust": 10.0, "et0": 1.0,
    }
    base.update(kw)
    return base


def _fv(w1: dict | None = None, w2: dict | None = None, coh: float | None = 0.45) -> dict:
    return {
        "date1": "2021-01-08", "date2": "2021-01-20",
        "weather_d1": w1 or _w(), "weather_d2": w2 or _w(),
        "coherence_expected": coh, "coherence_source": "s3", "dt_days": 12,
    }


# ── The verdict rule itself ───────────────────────────────────────────────────

def test_a_quiet_pair_is_healthy_with_no_events():
    status, events = _events.detect(_fv())
    assert status == HEALTHY
    assert events == []


def test_one_serious_event_raises_a_concern():
    status, events = _events.detect(_fv(w1=_w(snow_depth=0.30, temp=2.0)))
    assert status == CONCERN
    assert any(e.severity == SERIOUS for e in events)


def test_minor_events_alone_never_raise_a_concern():
    """The whole point of the severity tier: proxies do not get to decide."""
    status, events = _events.detect(_fv(
        w1=_w(snowfall=5.0, wind_gust=100.0),      # fresh snowfall + gale
        w2=_w(soil_temp=-5.0, temp=-5.0),          # freeze-thaw against date 1
    ))
    assert events, "precondition: this pair must actually trigger minor events"
    assert all(e.severity == MINOR for e in events)
    assert status == HEALTHY


def test_there_is_no_third_label():
    """'bad' claimed a confidence about unusable data that proxies cannot support."""
    for coh in (0.0, 0.05, 0.5, 1.0):
        for depth in (0.0, 0.5, 3.0):
            status, _ = _events.detect(_fv(w1=_w(snow_depth=depth, temp=5.0), coh=coh))
            assert status in (HEALTHY, CONCERN), f"unexpected label {status!r}"


def test_serious_events_sort_first():
    """A UI showing only the first event must show the one that set the label."""
    _, events = _events.detect(_fv(
        w1=_w(snowfall=5.0, wind_gust=100.0, snow_depth=0.30, temp=1.0)))
    assert events[0].severity == SERIOUS


def test_every_event_carries_the_measurement_that_fired_it():
    """A verdict a user cannot trace back to a number is the old score again."""
    _, events = _events.detect(_fv(w1=_w(snow_depth=0.40, temp=3.0, rain_3day=50.0)))
    assert events
    for e in events:
        assert e.detail.strip(), f"{e.kind} has no detail"
        assert any(ch.isdigit() for ch in e.detail), (
            f"{e.kind} detail cites no measurement: {e.detail!r}"
        )


# ── Wet snow: the strongest C-band mechanism ─────────────────────────────────

@pytest.mark.parametrize("temp", [0.0, 0.5, 5.0])
def test_snow_above_freezing_is_always_serious(temp):
    """Nagler & Rott (2000): liquid water collapses C-band penetration."""
    _, events = _events.detect(_fv(w1=_w(snow_depth=0.05, temp=temp)))
    wet = [e for e in events if e.kind == "wet_snow"]
    assert wet and wet[0].severity == SERIOUS


def test_cold_shallow_snow_is_not_wet_snow():
    """Dry snow is largely transparent at C-band — it must not flag."""
    status, events = _events.detect(_fv(w1=_w(snow_depth=0.05, temp=-10.0)))
    assert not [e for e in events if e.kind == "wet_snow"]
    assert status == HEALTHY


def test_deep_dry_snow_is_minor_and_shallow_dry_snow_is_not_flagged():
    """C-band penetrates dry snow, so depth alone is not a verdict.

    A deep but *unchanged* dry pack is a stable target. On p100_f466 a third of
    the concerns came from exactly that, so deep_snow is recorded as minor:
    only wetness (wet_snow) or a changing pack (delta_snow) sets concern.
    """
    status, deep = _events.detect(_fv(
        w1=_w(snow_depth=0.40, temp=-10.0),
        w2=_w(snow_depth=0.40, temp=-10.0),   # same pack across the pair
    ))
    ds = [e for e in deep if e.kind == "deep_snow"]
    assert ds and all(e.severity == MINOR for e in ds)
    assert status == HEALTHY, "an unchanged dry pack must not flag a pair"

    _, shallow = _events.detect(_fv(w1=_w(snow_depth=0.05, temp=-10.0)))
    assert not [e for e in shallow if e.kind == "deep_snow"]


def test_wet_snow_is_reported_instead_of_deep_snow_not_alongside_it():
    """One mechanism per snowpack: report the one that destroys coherence."""
    _, events = _events.detect(_fv(w1=_w(snow_depth=0.40, temp=2.0)))
    kinds = [e.kind for e in events]
    assert "wet_snow" in kinds
    assert "deep_snow" not in kinds


# ── Units, which are not uniform and have caused a real bug here ─────────────

def test_snow_depth_is_read_as_metres_not_centimetres():
    """A previous scorer compared metre values against centimetre thresholds,
    so 80 cm of snow scored as though there were none."""
    _, events = _events.detect(_fv(w1=_w(snow_depth=0.80, temp=-10.0)))
    deep = [e for e in events if e.kind == "deep_snow"]
    assert deep, "0.80 m = 80 cm of snow must register as deep"
    assert "80 cm" in deep[0].detail


def test_snowfall_is_read_as_centimetres():
    _, events = _events.detect(_fv(w1=_w(snowfall=3.0)))
    fresh = [e for e in events if e.kind == "fresh_snowfall"]
    assert fresh and "3.0 cm" in fresh[0].detail


def test_rain_events_use_liquid_rain_not_total_precipitation():
    """`precipitation_sum` is rain + snow water equivalent. Scoring it as rain
    double-counts the same weather the snow events already flag."""
    # A heavy winter day: all the precipitation fell as snow.
    _, events = _events.detect(_fv(
        w1=_w(precip_3day=60.0, rain_3day=0.0, snowfall=8.0, temp=-8.0)))
    assert not [e for e in events if e.kind in ("heavy_rain", "rain")], (
        "snowfall was counted as rain"
    )


# ── Freeze/thaw uses the ground, not the air ─────────────────────────────────

def test_freeze_thaw_prefers_soil_temperature_over_air():
    """Air and soil disagreed on frozen/thawed state on 59% of days at a test
    site, so the air reading must not override an available soil reading."""
    # Air says both dates are frozen; soil says one thawed.
    status, events = _events.detect(_fv(
        w1=_w(temp=-3.0, soil_temp=-2.0),
        w2=_w(temp=-3.0, soil_temp=+2.0),
    ))
    ft = [e for e in events if e.kind == "freeze_thaw"]
    assert ft, "a soil-temperature transition was missed"
    assert "soil temperature" in ft[0].detail


def test_freeze_thaw_falls_back_to_air_and_says_so():
    _, events = _events.detect(_fv(
        w1=_w(temp=-3.0, soil_temp=None),
        w2=_w(temp=+3.0, soil_temp=None),
    ))
    ft = [e for e in events if e.kind == "freeze_thaw"]
    assert ft and "air temperature" in ft[0].detail


def test_no_freeze_thaw_when_both_dates_share_a_state():
    _, events = _events.detect(_fv(
        w1=_w(soil_temp=-5.0), w2=_w(soil_temp=-1.0)))
    assert not [e for e in events if e.kind == "freeze_thaw"]


# ── S1 decay-model coherence as the baseline ─────────────────────────────────

def test_coherence_below_the_inversion_floor_is_serious_on_its_own():
    """The user's rule: if the decay curve already says the pair is unusable,
    flag it regardless of the weather."""
    status, events = _events.detect(_fv(coh=0.10))
    low = [e for e in events if e.kind == "low_coherence"]
    assert low and low[0].severity == SERIOUS
    assert status == CONCERN


def test_marginal_coherence_is_minor_and_does_not_flag_alone():
    status, events = _events.detect(_fv(coh=0.30))
    marginal = [e for e in events if e.kind == "marginal_coherence"]
    assert marginal and marginal[0].severity == MINOR
    assert status == HEALTHY


def test_good_coherence_raises_no_event():
    _, events = _events.detect(_fv(coh=0.60))
    assert not [e for e in events if "coherence" in e.kind]


def test_missing_coherence_is_not_treated_as_low():
    """An S3 failure must not manufacture a verdict — absence is not zero."""
    status, events = _events.detect(_fv(coh=None))
    assert not [e for e in events if "coherence" in e.kind]
    assert status == HEALTHY


# ── Per-AOI calibration ──────────────────────────────────────────────────────

def test_rain_and_soil_moisture_thresholds_are_calibrated_per_aoi():
    """Neither has a published C-band threshold, and a fixed one is climate-
    biased: a 12-day soil-moisture change of 0.10 m3/m3 occurs in 6% of summer
    pairs at a semi-arid site and 42% at a humid one."""
    dry = {f"2021-01-{d:02d}": _w(rain_3day=0.0, soil_moisture=0.10 + 0.001 * d)
           for d in range(1, 21)}
    wet = {f"2021-01-{d:02d}": _w(rain_3day=5.0 * d, soil_moisture=0.20 + 0.02 * (d % 5))
           for d in range(1, 21)}

    th_dry = _events.calibrate(dry)
    th_wet = _events.calibrate(wet)

    assert th_dry.calibrated and th_wet.calibrated
    assert th_wet.rain_3day_serious > th_dry.rain_3day_serious, (
        "a wet AOI must demand more rain before calling it extreme"
    )
    assert th_wet.delta_sm_serious > th_dry.delta_sm_serious


def test_calibration_never_drops_below_a_floor():
    """In an arid AOI the high percentile is 0, which would flag everything."""
    flat = {f"2021-01-{d:02d}": _w(rain_3day=0.0, soil_moisture=0.10)
            for d in range(1, 21)}
    th = _events.calibrate(flat)
    assert th.rain_3day_serious >= _events.FALLBACK_RAIN_3DAY_MINOR_MM
    assert th.delta_sm_serious >= 0.03


def test_a_short_stack_falls_back_to_fixed_thresholds():
    th = _events.calibrate({"2021-01-01": _w()})
    assert not th.calibrated
    assert th.rain_3day_serious == _events.FALLBACK_RAIN_3DAY_MM


def test_thresholds_are_recorded_so_a_verdict_can_be_reproduced():
    th = _events.calibrate({f"2021-01-{d:02d}": _w(rain_3day=float(d))
                            for d in range(1, 21)})
    as_dict = th.as_dict()
    for key in ("rain_3day_serious", "delta_sm_serious", "calibrated", "n_dates"):
        assert key in as_dict


# ── Degradation ──────────────────────────────────────────────────────────────

def test_missing_weather_yields_no_events_rather_than_a_healthy_claim():
    """All-None weather must not read as "nothing happened" — the caller
    reports it through missing_dates instead."""
    empty = dict.fromkeys(_w(), None)
    status, events = _events.detect(_fv(w1=empty, w2=empty, coh=None))
    assert events == []
    assert status == HEALTHY   # but the DB marks _complete False via missing_dates
