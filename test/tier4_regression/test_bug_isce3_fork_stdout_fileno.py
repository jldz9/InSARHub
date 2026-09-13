"""ISCE3_Burst's background executor died instantly under pytest/Jupyter.

Fixed in:   unreleased
Symptom:    `insarhub processor -N ISCE3_Burst submit` appeared to start, then
            nothing ran. All stages sat at RUNNING forever and executor.log held
            only:

                [executor] local run failed: fileno
                io.UnsupportedOperation: fileno

            The analyzer then failed with "no unwrapped interferograms ... run
            the processor's 'unwrap' stage first", which points at the analyzer
            and not at the executor that never started.
Root cause: _start_local_background() forks, then redirects the child's output
            with os.dup2(_lf.fileno(), sys.stdout.fileno()). `sys.stdout` is
            only a real file when nothing has replaced it; the moment something
            has -- pytest's capture, Jupyter/Colab, or contextlib.redirect_stdout
            -- .fileno() raises io.UnsupportedOperation. That fired inside the
            forked child before any stage ran.

The fix is to dup2 onto the raw descriptors 1 and 2. In the child those are
still the inherited stdout/stderr whatever the Python-level objects point at.

The same pattern was present in ISCE2_Base and GMTSAR_S1 (10 call sites in
total) and was fixed everywhere, not just where it had been observed.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

BUG = {
    "id": "isce3-fork-stdout-fileno",
    "fixed_in": "unreleased",
    "area": "processor/background-executor",
}

PROCESSOR_DIR = Path(__file__).resolve().parents[2] / "src" / "insarhub" / "processor"
SOURCES = ["isce3_base.py", "isce2_base.py", "gmtsar_s1.py"]


@pytest.mark.parametrize("name", SOURCES)
def test_no_processor_dup2s_onto_sys_stdout_fileno(name):
    """The exact construct that broke, in every processor that forks."""
    text = (PROCESSOR_DIR / name).read_text()
    offenders = [
        line.strip() for line in text.splitlines()
        if re.search(r"os\.dup2\([^)]*sys\.std(out|err)\.fileno\(\)", line)
    ]
    assert not offenders, (
        f"{name} redirects a forked child onto sys.stdout/stderr.fileno(), which "
        f"raises io.UnsupportedOperation under pytest, Jupyter or "
        f"redirect_stdout: {offenders}"
    )


@pytest.mark.parametrize("name", SOURCES)
def test_processors_still_redirect_the_child(name):
    """Guard against 'fixing' this by dropping the redirect.

    Without it a failure in the forked child is lost entirely -- os._exit()
    never flushes Python's buffered stderr, which is why the redirect has to
    happen before any work.
    """
    text = (PROCESSOR_DIR / name).read_text()
    assert re.search(r"os\.dup2\(_lf\.fileno\(\),\s*1\)", text), (
        f"{name} no longer redirects its forked child's stdout to the log file"
    )
    assert re.search(r"os\.dup2\(_lf\.fileno\(\),\s*2\)", text), (
        f"{name} no longer redirects its forked child's stderr to the log file"
    )


def test_sys_stdout_fileno_really_raises_when_captured():
    """Pin the premise, so this file still explains itself if Python changes.

    This is what pytest, Jupyter and redirect_stdout all do to sys.stdout.
    """
    captured = io.StringIO()
    with pytest.raises(io.UnsupportedOperation):
        captured.fileno()
