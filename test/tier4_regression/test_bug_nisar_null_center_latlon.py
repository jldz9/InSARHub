"""select_pairs() crashed on NISAR products before forming a single pair.

Fixed in:   unreleased
Symptom:    Any NISAR_GSLC workflow died in select_pairs with
                TypeError: float() argument must be a string or a real number,
                           not 'NoneType'
            at utils/tool.py, inside _build_baseline_table. The search itself
            worked -- the four Parowan GSLC dates came back fine -- so it looked
            like a baseline/pair-selection problem rather than a data-shape one.
Root cause: `float(props.get("centerLat", 0))`. A .get() default only applies
            when the key is ABSENT. ASF returns centerLat/centerLon for NISAR
            GSLC as an explicit null -- the key is present with value None -- so
            the default never fired and float(None) raised.

Sentinel-1 products carry real values there, which is why every S1 workflow was
unaffected and this went unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BUG = {
    "id": "nisar-null-center-latlon",
    "fixed_in": "unreleased",
    "area": "utils/baseline",
}

TOOL = Path(__file__).resolve().parents[2] / "src" / "insarhub" / "utils" / "tool.py"


def test_get_default_does_not_protect_against_an_explicit_none():
    """Pin the premise: this is the language behaviour the bug relied on."""
    props = {"centerLat": None}

    assert props.get("centerLat", 0) is None, (
        "a .get() default applies only to an ABSENT key, never to a present None"
    )
    assert props.get("centerLat") or 0 == 0

    with pytest.raises(TypeError):
        float(props.get("centerLat", 0))


def test_center_latlon_are_read_none_safely():
    """No `float(... .get("centerLat", <number>))` anywhere in tool.py."""
    offenders = [
        line.strip() for line in TOOL.read_text().splitlines()
        if re.search(r'float\([^)]*\.get\("center(Lat|Lon)",\s*[0-9]', line)
    ]
    assert not offenders, (
        "centerLat/centerLon are read with a .get() default, which NISAR's "
        f"explicit nulls defeat: {offenders}"
    )


def test_center_latlon_still_have_a_fallback():
    """Guard against 'fixing' this by dropping the default entirely.

    float(None) and float(props["centerLat"]) both raise; the point is to land
    on 0, not to remove the guard.
    """
    text = TOOL.read_text()
    hits = re.findall(r'float\([^)]*\.get\("center(?:Lat|Lon)"\)\s*or\s*0\)', text)
    assert len(hits) >= 2, (
        "expected centerLat/centerLon to be read as `.get(...) or 0`; found "
        f"{len(hits)} such reads"
    )
