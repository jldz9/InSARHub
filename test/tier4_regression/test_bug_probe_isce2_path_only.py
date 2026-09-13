"""Tier 1 reported a working conda ISCE2 install as "backend missing".

Fixed in:   unreleased
Symptom:    `scripts/test_install.py --flavor isce2` failed on a correctly
            installed environment: isce2 2.6.5 was present, `import isce`
            worked, and InSARHub could run it -- but
            test_env_claiming_a_backend_actually_has_it reported isce2 missing,
            so every ISCE2 test would have skipped forever.
Root cause: has_isce2() checked `shutil.which("topsApp.py")` only. conda-forge's
            isce2 ships an activation script that sets $ISCE_HOME and keeps
            topsApp.py in site-packages/isce/applications/ rather than on PATH,
            so the PATH check was always False for a conda install -- the most
            common way users install it, and the way the docs recommend.

The probe now mirrors processor/isce2_base.py::_check_isce2, which resolves
topsApp.py via an explicit isce_home, then $ISCE_HOME/applications, then PATH.
A probe that disagrees with the code it is probing is worse than no probe: it
turns a working install into a silent skip.
"""

from __future__ import annotations


import pytest

import _backends

BUG = {
    "id": "probe-isce2-path-only",
    "fixed_in": "unreleased",
    "area": "test/_backends",
}


def test_isce_home_layout_is_accepted(monkeypatch, tmp_path):
    """The conda-forge layout: ISCE_HOME set, topsApp.py NOT on PATH."""
    isce_home = tmp_path / "site-packages" / "isce"
    (isce_home / "applications").mkdir(parents=True)
    (isce_home / "applications" / "topsApp.py").write_text("#!/usr/bin/env python\n")

    monkeypatch.setattr(_backends, "_importable", lambda name: name == "isce")
    monkeypatch.setattr(_backends, "_on_path", lambda exe: False)
    monkeypatch.setenv("ISCE_HOME", str(isce_home))
    _backends.has_isce2.cache_clear()

    assert _backends.has_isce2() is True, (
        "a conda-forge ISCE2 install (ISCE_HOME set, topsApp.py not on PATH) "
        "must be detected"
    )


def test_flat_isce_home_layout_is_accepted(monkeypatch, tmp_path):
    """Source builds put topsApp.py directly under ISCE_HOME."""
    isce_home = tmp_path / "isce"
    isce_home.mkdir(parents=True)
    (isce_home / "topsApp.py").write_text("#!/usr/bin/env python\n")

    monkeypatch.setattr(_backends, "_importable", lambda name: name == "isce")
    monkeypatch.setattr(_backends, "_on_path", lambda exe: False)
    monkeypatch.setenv("ISCE_HOME", str(isce_home))
    _backends.has_isce2.cache_clear()

    assert _backends.has_isce2() is True


def test_path_only_layout_is_still_accepted(monkeypatch):
    """The original case must keep working: topsApp.py on PATH, no ISCE_HOME."""
    monkeypatch.setattr(_backends, "_importable", lambda name: name == "isce")
    monkeypatch.setattr(_backends, "_on_path", lambda exe: exe == "topsApp.py")
    monkeypatch.delenv("ISCE_HOME", raising=False)
    _backends.has_isce2.cache_clear()

    assert _backends.has_isce2() is True


def test_absent_isce2_is_still_reported_missing(monkeypatch):
    """The fix must not make the probe answer yes to everything."""
    monkeypatch.setattr(_backends, "_importable", lambda name: False)
    monkeypatch.setattr(_backends, "_on_path", lambda exe: False)
    monkeypatch.delenv("ISCE_HOME", raising=False)
    _backends.has_isce2.cache_clear()

    assert _backends.has_isce2() is False


def test_importable_but_unresolvable_is_missing(monkeypatch, tmp_path):
    """`import isce` alone is not enough -- the processor shells out to
    topsApp.py, so an env where it cannot be resolved cannot run ISCE2."""
    monkeypatch.setattr(_backends, "_importable", lambda name: name == "isce")
    monkeypatch.setattr(_backends, "_on_path", lambda exe: False)
    monkeypatch.setenv("ISCE_HOME", str(tmp_path / "nowhere"))
    _backends.has_isce2.cache_clear()

    assert _backends.has_isce2() is False


@pytest.fixture(autouse=True)
def _reset_cache():
    """has_isce2 is lru_cached; leaking a monkeypatched result would poison
    every later test in the session."""
    yield
    _backends.has_isce2.cache_clear()
