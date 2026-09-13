"""`insarhub downloader --stacks 124:124_266256_IW2` selected nothing for S1_Burst.

Fixed in:   0.4.0
Changelog:  "Fixed CLI `--stacks PATH:FRAME` selecting nothing for `S1_Burst`."
Symptom:    An explicit burst stack selection matched zero stacks, and the run
            silently continued against every stack instead.
Root cause: ASF returns no `frameNumber` on SLC-BURST products, so burst stacks
            key on `fullBurstID` ("124_266256_IW2"). The CLI coerced both halves
            of a `PATH:FRAME` token to int, producing a target that could never
            equal a burst key.

Selectors are now kept as strings when they are not numeric, and matched through
`_stack_key_matches()` on the downloader.
"""

from __future__ import annotations

import pytest

BUG = {
    "id": "0400-burst-stack-selection",
    "fixed_in": "0.4.0",
    "area": "cli/downloader",
}


class _FakeResult:
    def __init__(self, start="2024-01-07T16:12:34.000000Z", **props):
        self.properties = {"startTime": start, "flightDirection": "ASCENDING", **props}


def _offline_downloader(cls):
    """A downloader with a default config and canned results -- no auth, no network."""
    dl = object.__new__(cls)
    dl.config = cls.default_config()
    dl._subset = None
    return dl


# The exact keys from the report.
KEY_IW2 = (124, "124_266256_IW2")
KEY_IW3 = (124, "124_266256_IW3")
KEY_PAD = (87, "087_185682_IW2")


def _match(key, selector):
    from insarhub.downloader.s1_burst import S1_Burst

    return S1_Burst._stack_key_matches(S1_Burst, key, (key[0], selector))


# ── The selector forms a user may type ───────────────────────────────────────

def test_accepts_full_burst_id():
    assert _match(KEY_IW2, "124_266256_IW2")
    assert _match(KEY_IW2, "124_266256_iw2"), "must be case-insensitive"


def test_accepts_index_and_subswath():
    assert _match(KEY_IW2, "266256_IW2")
    assert not _match(KEY_IW2, "266256_IW3")


def test_bare_index_spans_subswaths():
    """ASF reuses a burst index across subswaths, so a bare index is
    deliberately one-to-many; naming the subswath pins a single stack."""
    assert _match(KEY_IW2, "266256")
    assert _match(KEY_IW3, "266256")
    assert _match(KEY_IW2, 266256), "an int selector must work too"


def test_path_zero_padding_is_ignored():
    assert _match(KEY_PAD, "87_185682_IW2")
    assert _match(KEY_PAD, "087_185682_IW2")


def test_rejects_wrong_stack():
    from insarhub.downloader.s1_burst import S1_Burst

    assert not _match(KEY_IW2, "266257")
    assert not _match(KEY_IW2, "")
    # Same burst index, different path.
    assert not S1_Burst._stack_key_matches(S1_Burst, KEY_IW2, (87, "266256"))


def test_granule_fallback_key_matches_verbatim_only():
    """A product with no burst ID groups by granule name; it has no index or
    subswath to compare, so only an exact string can select it."""
    granule = "S1_266256_IW3_20240119T060058_VV_5638-BURST"
    assert _match((124, granule), granule)
    assert not _match((124, granule), "266256")


def test_frame_downloaders_still_match_exactly():
    """The fix must not loosen matching for the frame-keyed downloaders."""
    from insarhub.downloader.asf_base import ASF_Base_Downloader as B

    assert B._stack_key_matches(B, (124, 56), (124, 56))
    assert not B._stack_key_matches(B, (124, 56), (124, 57))


# ── End-to-end through filter() ──────────────────────────────────────────────

def _burst_downloader():
    from insarhub.downloader.s1_burst import S1_Burst

    dl = _offline_downloader(S1_Burst)
    dl.results = {
        KEY_IW2: [_FakeResult()],
        KEY_IW3: [_FakeResult()],
        KEY_PAD: [_FakeResult()],
    }
    return dl


def test_full_id_selects_one_stack():
    dl = _burst_downloader()
    dl.filter(path_frame=[KEY_IW2])
    assert set(dl.active_results) == {KEY_IW2}


def test_bare_index_selects_both_subswaths():
    dl = _burst_downloader()
    dl.filter(path_frame=[(124, "266256")])
    assert set(dl.active_results) == {KEY_IW2, KEY_IW3}


def test_multiple_tokens_across_paths():
    dl = _burst_downloader()
    dl.filter(path_frame=[(124, "266256_IW3"), (87, "87_185682_IW2")])
    assert set(dl.active_results) == {KEY_IW3, KEY_PAD}


# ── Labels ───────────────────────────────────────────────────────────────────

def test_stack_key_label_names_the_burst_id():
    """Printing "frame 124_266256_IW2" misnamed the value and implied a number
    the user could pass to --frame."""
    from insarhub.downloader.asf_base import ASF_Base_Downloader
    from insarhub.downloader.s1_burst import S1_Burst

    assert S1_Burst.stack_key_label == "Burst_ID"
    assert ASF_Base_Downloader.stack_key_label == "frame"
    # capitalize() would mangle "Burst_ID" into "Burst_id".
    assert S1_Burst._stack_key_label_title(S1_Burst) == "Burst_ID"
    assert ASF_Base_Downloader._stack_key_label_title(ASF_Base_Downloader) == "Frame"


@pytest.mark.parametrize(
    "downloader,label",
    [
        ("S1_SLC", "SLCs"),
        ("S1_Burst", "bursts"),
        ("NISAR_GSLC", "GSLCs"),
        ("NISAR_RSLC", "RSLCs"),
        ("NISAR_GUNW", "GUNWs"),
    ],
)
def test_product_label_is_per_downloader(downloader, label):
    """search() said "Searching for SLCs" whatever the dataset was."""
    import insarhub.downloader as dl_pkg

    assert getattr(dl_pkg, downloader).product_label == label
