"""Tier 2 -- the release build is quiet, and INSARHUB_DEBUG turns it all on.

The policy (src/insarhub/_logsetup.py):

  * release  -- InSARHub's own INFO/DEBUG suppressed; WARNING and above still
                reach the terminal; ``print()`` untouched
  * debug    -- INSARHUB_DEBUG=1 lowers the ``insarhub`` logger to DEBUG

These matter because logging state is process-global: a single stray
``basicConfig`` or a misplaced handler changes what every other module emits,
and nothing about the resulting silence looks like a bug.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap

import pytest

import _backends
from insarhub import _logsetup


@pytest.fixture
def clean_logging(monkeypatch):
    """Restore global logging state; these tests mutate it by design."""
    root = logging.getLogger()
    insarhub_logger = logging.getLogger(_logsetup.LOGGER_NAME)
    saved = (root.level, list(root.handlers),
             insarhub_logger.level, list(insarhub_logger.handlers))
    monkeypatch.delenv(_logsetup.ENV_VAR, raising=False)
    yield
    root.level, root.handlers, insarhub_logger.level, insarhub_logger.handlers = (
        saved[0], saved[1], saved[2], saved[3])


# ── The switch ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
     ("0", False), ("false", False), ("no", False), ("off", False), ("", False),
     ("  ", False)],
)
def test_env_var_parsing(monkeypatch, value, expected):
    monkeypatch.setenv(_logsetup.ENV_VAR, value)
    assert _logsetup.debug_enabled() is expected


def test_unset_env_var_means_release(monkeypatch):
    monkeypatch.delenv(_logsetup.ENV_VAR, raising=False)
    assert _logsetup.debug_enabled() is False
    assert _logsetup.level() == logging.WARNING


# ── Levels ───────────────────────────────────────────────────────────────────

def test_release_suppresses_info_and_debug(clean_logging):
    _logsetup.configure(install_handler=True)
    log = logging.getLogger(_logsetup.LOGGER_NAME)
    assert log.level == logging.WARNING
    assert not log.isEnabledFor(logging.INFO)
    assert not log.isEnabledFor(logging.DEBUG)


def test_release_still_emits_warning_and_error(clean_logging):
    """The chosen policy keeps these visible.

    Silencing them is not hypothetical: insarhub/__init__ once called
    logging.disable(CRITICAL), and `processor ... cancel` scancelled every job
    while printing nothing at all.
    """
    _logsetup.configure(install_handler=True)
    log = logging.getLogger(_logsetup.LOGGER_NAME)
    assert log.isEnabledFor(logging.WARNING)
    assert log.isEnabledFor(logging.ERROR)


def test_debug_env_var_enables_everything(clean_logging, monkeypatch):
    monkeypatch.setenv(_logsetup.ENV_VAR, "1")
    _logsetup.configure(install_handler=True)
    log = logging.getLogger(_logsetup.LOGGER_NAME)
    assert log.level == logging.DEBUG
    assert log.isEnabledFor(logging.DEBUG)


def test_debug_does_not_raise_the_root_logger(clean_logging, monkeypatch):
    """Third-party libraries must stay quiet even in debug mode.

    Lowering the root is what turns a debug session into a wall of matplotlib,
    botocore, rasterio and asyncio records.
    """
    monkeypatch.setenv(_logsetup.ENV_VAR, "1")
    _logsetup.configure(install_handler=True)
    assert logging.getLogger().level == logging.WARNING
    assert not logging.getLogger("matplotlib").isEnabledFor(logging.INFO)


def test_no_null_handler_on_the_insarhub_logger(clean_logging):
    """A NullHandler here silently swallows warnings and errors.

    The usual library idiom attaches one so unconfigured hosts see nothing, but
    it also satisfies logging's handler lookup, which stops `logging.lastResort`
    from firing -- and lastResort is what carries WARNING/ERROR to stderr when
    no handler is installed. Adding one dropped every insarhub warning and error
    in plain library use.
    """
    _logsetup.configure(install_handler=False)
    handlers = logging.getLogger(_logsetup.LOGGER_NAME).handlers
    assert not any(isinstance(h, logging.NullHandler) for h in handlers), (
        "a NullHandler on the insarhub logger suppresses WARNING/ERROR in "
        "library use by preempting logging.lastResort"
    )


def test_library_import_installs_no_handler_by_default(clean_logging):
    """Importing a library must not hijack the host's logging config."""
    root_handlers_before = list(logging.getLogger().handlers)
    _logsetup.configure()          # install_handler=None -> only when debugging
    assert logging.getLogger().handlers == root_handlers_before


# ── End to end, in a real interpreter ────────────────────────────────────────

_PROBE = textwrap.dedent("""
    import logging
    import insarhub
    log = logging.getLogger("insarhub.probe")
    log.debug("D-RECORD"); log.info("I-RECORD")
    log.warning("W-RECORD"); log.error("E-RECORD")
    print("PRINTOUT")
""")


def _probe(env_value: str | None) -> str:
    for dep in ("mintpy", "dask", "cdsapi"):
        if not _backends._importable(dep):
            pytest.skip(f"{dep} is not genuinely installed; a subprocess "
                        "`import insarhub` cannot run without it")
    env = dict(os.environ)
    env.pop(_logsetup.ENV_VAR, None)
    if env_value is not None:
        env[_logsetup.ENV_VAR] = env_value
    proc = subprocess.run([sys.executable, "-c", _PROBE],
                          capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout + proc.stderr


def test_release_output_is_printout_plus_warnings(clean_logging):
    out = _probe(None)
    assert "PRINTOUT" in out
    assert "W-RECORD" in out and "E-RECORD" in out
    assert "I-RECORD" not in out, "INFO leaked into a release run"
    assert "D-RECORD" not in out, "DEBUG leaked into a release run"


def test_debug_output_includes_everything(clean_logging):
    out = _probe("1")
    for marker in ("PRINTOUT", "D-RECORD", "I-RECORD", "W-RECORD", "E-RECORD"):
        assert marker in out, f"{marker} missing from an INSARHUB_DEBUG=1 run"
