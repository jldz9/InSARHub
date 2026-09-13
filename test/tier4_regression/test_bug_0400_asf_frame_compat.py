"""Searches returned nothing after asf_search upgraded to 13.0.0.

Fixed in:   0.4.0
Changelog:  "Fixed empty downloader search results with `asf_search` 13.0.0."
Symptom:    Every frame-filtered search came back empty against asf_search 13,
            while the same query worked on 12.x.
Root cause: asf_search's `should_use_asf_frame()` stopped detecting a generic
            `platform=SENTINEL-1` query -- it checks for a `shortName[]` CMR key
            while the query emits `shortName`, and its `platform[]` fallback only
            lists SENTINEL-1A/-1B/-1C/-1D. So `frame` silently queried the ESA
            frame instead of the ASF frame and matched nothing.

Sentinel-1 / ALOS / NISAR frame filters are now routed to `asfFrame`
(FRAME_NUMBER) by InSARHub itself, which is correct on both 12.x and 13.x. This
test pins our own routing, deliberately not asf_search's behaviour, so it keeps
working whichever version is installed.
"""

from __future__ import annotations

import types

BUG = {
    "id": "0400-asf-frame-compat",
    "fixed_in": "0.4.0",
    "area": "downloader",
}


def _stub(cfg):
    """Bind the routing helpers onto a bare config holder.

    `_uses_asf_frame` / `_apply_asf_frame_compat` only read `self.config`, so a
    SimpleNamespace avoids netrc, network and full downloader construction.
    """
    from insarhub.downloader.asf_base import ASF_Base_Downloader

    stub = types.SimpleNamespace(config=cfg)
    stub._ASF_FRAME_TOKENS = ASF_Base_Downloader._ASF_FRAME_TOKENS
    stub._uses_asf_frame = lambda: ASF_Base_Downloader._uses_asf_frame(stub)
    stub._apply_asf_frame_compat = (
        lambda opts: ASF_Base_Downloader._apply_asf_frame_compat(stub, opts)
    )
    return stub


def test_sentinel1_is_recognised_as_asf_frame_platform():
    from insarhub.config import S1_SLC_Config

    assert _stub(S1_SLC_Config())._uses_asf_frame() is True


def test_generic_base_config_is_not():
    """A dataset-agnostic config must keep the ESA `frame` semantics."""
    from insarhub.config import ASF_Base_Config

    assert _stub(ASF_Base_Config())._uses_asf_frame() is False


def test_frame_is_routed_to_asfframe_for_sentinel1():
    """The actual fix: `frame` becomes `asfFrame` before the query is sent."""
    from insarhub.config import S1_SLC_Config

    opts = _stub(S1_SLC_Config())._apply_asf_frame_compat(
        {"relativeOrbit": 100, "frame": 466}
    )
    assert opts.get("asfFrame") == 466
    assert "frame" not in opts, "leaving `frame` in place is what returned nothing"


def test_explicit_asfframe_wins_when_both_are_set():
    from insarhub.config import S1_SLC_Config

    opts = _stub(S1_SLC_Config())._apply_asf_frame_compat({"asfFrame": 466, "frame": 999})
    assert opts.get("asfFrame") == 466
    assert "frame" not in opts


def test_frame_is_left_alone_for_non_asf_frame_platforms():
    """Routing everything to asfFrame would break the platforms that genuinely
    use the ESA frame."""
    from insarhub.config import ASF_Base_Config

    opts = _stub(ASF_Base_Config())._apply_asf_frame_compat({"frame": 466})
    assert opts.get("frame") == 466
    assert "asfFrame" not in opts
