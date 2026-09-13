"""Tier 2 -- the registry and config layer behave as the GUI and CLI assume.

Both front ends are generated from this metadata: the GUI renders a form from
each config's ``_ui_fields``, and the CLI generates flags from the same
dataclass. So a config that disagrees with itself shows up as an empty control
in the browser or a flag that silently does nothing -- never as an exception.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from insarhub import Analyzer, Downloader, Processor
import _backends


# ── Registry integrity ───────────────────────────────────────────────────────

def test_expected_workflows_are_registered():
    for spec in _backends.WORKFLOWS.values():
        assert spec["downloader"] in Downloader._registry
        assert spec["processor"] in Processor._registry
        assert spec["analyzer"] in Analyzer._registry


@pytest.mark.parametrize("alias,target", sorted(Analyzer._aliases.items()))
def test_legacy_analyzer_alias_resolves(alias, target):
    """Renamed analyzers keep their old names working.

    0.4.0 renamed the dolphin analyzers; saved configs and scripts in the wild
    still say ISCE3_Dolphin_PL / Dolphin_SBAS, so every alias must land on a
    real class.
    """
    assert target in Analyzer._registry, f"alias {alias} points at unknown {target}"
    assert Analyzer._registry[alias] is Analyzer._registry[target]


def test_aliases_do_not_shadow_canonical_names():
    """An alias whose name is also a canonical name would make resolution
    order-dependent."""
    canonical = {
        name for name, cls in Analyzer._registry.items()
        if name not in Analyzer._aliases
    }
    overlap = canonical & set(Analyzer._aliases)
    assert not overlap, f"these names are both canonical and aliases: {sorted(overlap)}"


@pytest.mark.parametrize("name", sorted(Processor._registry))
def test_processor_declares_a_compatible_downloader(name):
    """The GUI uses this to pick which downloader tab to show."""
    cls = Processor._registry[name]
    declared = getattr(cls, "compatible_downloader", None)
    assert declared, f"{name} declares no compatible_downloader"
    if declared != "all":
        assert declared in Downloader._registry, (
            f"{name}.compatible_downloader={declared!r} is not a registered downloader"
        )


@pytest.mark.parametrize("name", sorted(Analyzer._registry))
def test_analyzer_declares_a_real_compatible_processor(name):
    """0.4.0 fixed ISCE3_NISAR.compatible_analyzer resolving to the Sentinel-1
    analyzer; this keeps the mapping honest in both directions."""
    cls = Analyzer._registry[name]
    declared = getattr(cls, "compatible_processor", None)
    if declared in (None, "all"):
        pytest.skip(f"{name} is processor-agnostic")
    assert declared in Processor._registry, (
        f"{name}.compatible_processor={declared!r} is not a registered processor"
    )


# ── Config integrity ─────────────────────────────────────────────────────────

def _concrete_configs():
    """default_config of every registered component.

    Abstract bases legitimately forward-reference fields their subclasses add,
    so only concrete registered configs are checked.
    """
    out = {}
    for registry in (Downloader, Processor, Analyzer):
        for name, cls in registry._registry.items():
            cfg = getattr(cls, "default_config", None)
            if cfg and dataclasses.is_dataclass(cfg):
                out.setdefault(f"{name}:{cfg.__name__}", cfg)
    return out


CONFIGS = _concrete_configs()


def test_configs_were_discovered():
    assert CONFIGS, "no registered configs found -- registries did not populate"


@pytest.mark.parametrize("cfg", CONFIGS.values(), ids=list(CONFIGS))
def test_ui_fields_reference_real_dataclass_fields(cfg):
    """Every key the GUI renders must be a field the config stores.

    Otherwise the browser shows a control whose value is dropped on save -- the
    "empty field in the GUI" bug.
    """
    if not hasattr(cfg, "_ui_fields"):
        pytest.skip(f"{cfg.__name__} declares no _ui_fields")
    fields = {f.name for f in dataclasses.fields(cfg)}
    extra = set(cfg._ui_fields) - fields
    assert not extra, (
        f"{cfg.__name__}._ui_fields references field(s) that are not on the "
        f"dataclass: {sorted(extra)}. Add them as dataclass fields, or remove "
        f"them from _ui_fields."
    )


@pytest.mark.parametrize("cfg", CONFIGS.values(), ids=list(CONFIGS))
def test_config_is_instantiable_with_defaults(cfg):
    """The CLI and GUI both build a default config before showing options; if
    that raises, the component vanishes from the UI with no error."""
    instance = cfg()
    assert instance is not None


@pytest.mark.parametrize("cfg", CONFIGS.values(), ids=list(CONFIGS))
def test_container_default_is_not_a_floating_tag(cfg):
    """Released builds must not point users at a mutable :dev image.

    Shipping :dev means every user pulls whatever was last pushed, so a run is
    not reproducible and cannot be tied to a release.
    """
    default = getattr(cfg, "container_default", None)
    if not isinstance(default, str) or not default:
        pytest.skip(f"{cfg.__name__} has no container_default")
    if ".dev" in __import__("insarhub").__version__ or "rc" in __import__("insarhub").__version__:
        pytest.skip("prerelease build may legitimately point at :dev")
    assert not default.endswith(":dev"), (
        f"{cfg.__name__}.container_default={default!r} pins the floating :dev "
        "tag. Point releases at an immutable, versioned tag."
    )


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _release_triple(version: str) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` of an X.Y.Z version, or None if it is not one.

    Only the numeric prefix is read, so "0.4.2.dev0" and "0.4.2rc1" parse the
    same as "0.4.2" -- the prerelease suffix is handled by the skips below.
    """
    m = _VERSION_RE.match(version)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


@pytest.mark.parametrize("cfg", CONFIGS.values(), ids=list(CONFIGS))
def test_container_default_tag_is_this_release_series(cfg):
    """The pinned image tag must come from THIS release series (same X.Y).

    The tags are written out literally (``...insarhub-base:0.4.0``) rather than
    interpolated from ``__version__``, because the tag has to name an image that
    was actually built and pushed -- deriving it would silently promise an image
    for every dev version that will never exist.

    Requiring an *exact* match, which this test used to do, cannot hold on
    release day. A ``docker/release/*`` image installs InSARHub from conda-forge
    and asserts the installed version equals ``INSARHUB_VERSION``, so ``:X.Y.Z``
    is unbuildable until the feedstock has ``X.Y.Z`` -- which lands a day or more
    after the tag is pushed. Exact equality therefore forced a choice between
    tagging a release with red CI and pointing users at an image that does not
    exist. 0.4.1 hit precisely that and shipped defaulting to the 0.4.0 images.

    So a patch-level lag *within* one series is allowed: 0.4.1 may point at
    ``:0.4.0``. That is safe because a patch release by definition carries no
    processor or analyzer change large enough to matter to what runs inside the
    image -- if it ever does, build the new patch images and re-tag.

    What is still caught, because this is where images and code genuinely
    diverge, is a lag across a **minor or major** bump: 0.5.0 may not ship
    pointing at ``:0.4.x``. A tag running *ahead* of ``__version__`` is caught
    too, since that names an image nobody has built yet.
    """
    import insarhub

    default = getattr(cfg, "container_default", None)
    if not isinstance(default, str) or ":" not in default:
        pytest.skip(f"{cfg.__name__} has no tagged container_default")
    if ".dev" in insarhub.__version__ or "rc" in insarhub.__version__:
        pytest.skip("prerelease build may legitimately point at an older tag")

    tag = default.rsplit(":", 1)[1]
    tag_v = _release_triple(tag)
    this_v = _release_triple(insarhub.__version__)
    assert tag_v is not None, (
        f"{cfg.__name__}.container_default is tagged {tag!r}, which is not an "
        "X.Y.Z version. Releases must pin an immutable, versioned tag."
    )
    assert this_v is not None, (
        f"insarhub.__version__ is {insarhub.__version__!r}, which is not X.Y.Z "
        "and carries no .dev/rc marker either -- fix _version.py."
    )

    assert tag_v[:2] == this_v[:2], (
        f"{cfg.__name__}.container_default is tagged {tag!r} but this is "
        f"InSARHub {insarhub.__version__}. A patch-level lag is fine, but a "
        f"minor or major bump means the images no longer match the code: build "
        f"and push the {this_v[0]}.{this_v[1]}.x images and re-tag every "
        "container_default -- see docker/README.md."
    )
    assert tag_v[2] <= this_v[2], (
        f"{cfg.__name__}.container_default is tagged {tag!r}, which is ahead of "
        f"InSARHub {insarhub.__version__}. That names an image that has not been "
        "built; container_default may lag this version, never lead it."
    )


# ── GUI visibility ───────────────────────────────────────────────────────────

def _consumers(downloader_name: str) -> list[str]:
    """Processors that declare this downloader as their input."""
    return sorted(
        name for name, cls in Processor._registry.items()
        if getattr(cls, "compatible_downloader", None) == downloader_name
    )


@pytest.mark.parametrize("name", sorted(Downloader.available()))
def test_gui_offers_only_downloaders_a_processor_can_consume(name):
    """A downloader in the GUI must lead somewhere.

    NISAR_RSLC and NISAR_GUNW can be searched and downloaded, but no processor
    consumes them yet -- offering them in the UI walks the user into a dead end
    after a large download. They carry `gui_hidden = True` instead of being
    unregistered, so the Python API and CLI keep working.

    This pins the flag to the actual registry rather than to a hand-kept list:
    add a processor for one of them and forget to drop the flag, and this fails.
    """
    cls = Downloader._registry[name]
    hidden = bool(getattr(cls, "gui_hidden", False))
    consumers = _consumers(name)

    if consumers:
        assert not hidden, (
            f"{name} is hidden from the GUI but {consumers} can process it -- "
            "remove `gui_hidden` from the downloader class"
        )
    else:
        assert hidden, (
            f"{name} is offered in the GUI but no processor consumes it. Either "
            "add `gui_hidden = True` to the class, or register a processor "
            "declaring compatible_downloader = {name!r}."
        )


def test_gui_downloader_list_matches_the_flags():
    """What the GUI is actually served, not just what the classes declare."""
    import insarhub.app.state as state

    offered = set(state._DOWNLOADERS_META)
    hidden = {
        name for name in Downloader.available()
        if getattr(Downloader._registry[name], "gui_hidden", False)
    }
    assert not (offered & hidden), f"hidden downloaders still offered: {sorted(offered & hidden)}"
    assert offered, "the GUI is offered no downloaders at all"


def test_hiding_a_downloader_does_not_unregister_it():
    """The CLI and Python API must still reach a hidden downloader."""
    for name in ("NISAR_RSLC", "NISAR_GUNW"):
        assert name in Downloader._registry, (
            f"{name} was removed from the registry; hiding it from the GUI must "
            "not break `insarhub downloader -N " + name + "`"
        )


# ── Cross-processor API consistency ──────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(Processor._registry))
def test_watch_takes_refresh_interval_on_every_processor(name):
    """One spelling for the poll interval, across every processor.

    These drifted to three names for the same concept -- Hyp3Base/ISCE2_Base
    took `refresh_interval`, GMTSAR_S1 `poll_interval`, ISCE3_Base `interval` --
    which meant no caller could drive them uniformly. The CLI worked around it
    by passing two spellings and discarding the wrong one, and still missed
    ISCE3's, so `insarhub processor -N ISCE3_Burst watch --interval N` silently
    used the default. Anything simpler raised TypeError.
    """
    import inspect

    cls = Processor._registry[name]
    watch = getattr(cls, "watch", None)
    assert callable(watch), f"{name} has no watch()"

    params = inspect.signature(watch).parameters
    assert "refresh_interval" in params, (
        f"{name}.watch() does not accept `refresh_interval`; every processor "
        f"must spell the poll interval the same way. Got: {list(params)}"
    )


@pytest.mark.parametrize("name", sorted(Processor._registry))
def test_watch_interval_is_the_first_positional_parameter(name):
    """A positional call must work everywhere too.

    Generic callers pass the interval positionally to avoid depending on the
    keyword at all; that only holds while it stays the first real parameter.
    """
    import inspect

    params = [p for p in inspect.signature(Processor._registry[name].watch).parameters
              if p != "self"]
    assert params and params[0] == "refresh_interval", (
        f"{name}.watch()'s first parameter is {params[:1]}, not refresh_interval"
    )


@pytest.mark.parametrize(
    "name,legacy", [("GMTSAR_S1", "poll_interval"), ("ISCE3_Burst", "interval")]
)
def test_legacy_watch_keyword_still_accepted(name, legacy):
    """Renaming must not break scripts already using the old keyword."""
    import inspect

    params = inspect.signature(Processor._registry[name].watch).parameters
    assert legacy in params, (
        f"{name}.watch() no longer accepts the legacy `{legacy}` keyword; "
        "existing callers would break"
    )


@pytest.mark.parametrize("name", sorted(Processor._registry))
def test_builds_own_network_is_declared_consistently(name):
    """Whether a processor takes pairs is one attribute, read by all front ends.

    The CLI (cli/main.py `_needs_pairs`) and the GUI (routes/processor.py
    `_needs_stack_file`) both branch on `builds_own_network`. A processor that
    derives its network from slc/ -- ISCE3_Burst and ISCE3_NISAR, which hand the
    stack to dolphin phase linking -- must say so, or a caller will try to pair
    its scenes up first. For NISAR that fails outright: ASF publishes no
    baseline stack for GSLC, so pair selection returns nothing.
    """
    cls = Processor._registry[name]
    # Absent means False -- that is the contract both front ends use
    # (`getattr(cls, "builds_own_network", False)`), so pair-based processors
    # are not required to declare it. What must not happen is a non-bool.
    raw = getattr(cls, "builds_own_network", False)
    assert isinstance(raw, bool), (
        f"{name}.builds_own_network is {raw!r}; it must be a bool so the CLI, "
        "the GUI and the Python API agree on whether it takes pairs"
    )
    if raw:
        assert getattr(cls, "input_glob", None), (
            f"{name} builds its own network but declares no input_glob, so "
            "nothing says which products in slc/ it reads"
        )
