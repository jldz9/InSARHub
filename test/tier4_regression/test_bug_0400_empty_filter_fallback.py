"""A stack filter that matched nothing silently ran against *every* stack.

Fixed in:   0.4.0
Changelog:  "Fixed `filter()` silently falling back to the unfiltered search
            when no stack matched."
Symptom:    The user excluded stacks, nothing matched, and the summary, pair
            selection and download all ran on the full set behind one warning
            line -- so a typo in `--stacks` downloaded everything.
Root cause: `_subset` was assigned only in the non-empty branch, so it stayed
            `None` on a total miss and `active_results` fell back to `results`.

This is the dangerous half of the burst-selection bug: the selector failing to
match was recoverable, silently processing the wrong data was not.
"""

from __future__ import annotations

BUG = {
    "id": "0400-empty-filter-fallback",
    "fixed_in": "0.4.0",
    "area": "downloader",
}


class _FakeResult:
    def __init__(self, start="2024-01-07T16:12:34.000000Z", **props):
        self.properties = {"startTime": start, "flightDirection": "ASCENDING", **props}


def _offline_downloader(cls):
    dl = object.__new__(cls)
    dl.config = cls.default_config()
    dl._subset = None
    return dl


def _burst_downloader():
    from insarhub.downloader.s1_burst import S1_Burst

    dl = _offline_downloader(S1_Burst)
    dl.results = {
        (124, "124_266256_IW2"): [_FakeResult()],
        (124, "124_266256_IW3"): [_FakeResult()],
        (87, "087_185682_IW2"): [_FakeResult()],
    }
    return dl


def test_empty_filter_does_not_fall_back_to_unfiltered():
    """The core of the bug: a total miss must commit an empty subset."""
    dl = _burst_downloader()
    assert len(dl.active_results) == 3, "precondition: all stacks present"

    out = dl.filter(path_frame=[(124, "999999_IW2")])

    assert out == {}
    assert dl.active_results == {}, (
        "a filter that matched nothing must yield nothing -- falling back to "
        "the unfiltered set silently processes data the user excluded"
    )


def test_a_matching_filter_still_narrows():
    """Guard against 'fixing' the above by making filter() always empty."""
    dl = _burst_downloader()
    dl.filter(path_frame=[(124, "124_266256_IW2")])
    assert set(dl.active_results) == {(124, "124_266256_IW2")}


def test_frame_keyed_downloader_has_the_same_guarantee():
    from insarhub.downloader.s1_slc import S1_SLC

    dl = _offline_downloader(S1_SLC)
    dl.results = {(124, 56): [_FakeResult()], (87, 527): [_FakeResult()]}

    dl.filter(path_frame=[(124, 56)])
    assert set(dl.active_results) == {(124, 56)}

    dl2 = _offline_downloader(S1_SLC)
    dl2.results = {(124, 56): [_FakeResult()]}
    dl2.filter(path_frame=[(999, 999)])
    assert dl2.active_results == {}
