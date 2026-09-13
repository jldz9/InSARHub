"""Local processors never recorded themselves in insarhub_config.json.

Fixed in:   unreleased
Symptom:    A workdir driven through the Python API ended up with
            {"analyzer": {...}} and no "processor" key, so the GUI showed the
            folder with an analyzer badge and a blank processor. Observed on a
            real ISCE3_Burst run over Parowan Valley.
Root cause: write_workflow_marker() was called by every downloader, every
            analyzer and Hyp3Base -- but not by ISCE2_S1, GMTSAR_S1 or the ISCE3
            processors. The GUI and CLI write the same marker themselves, which
            is why only the Python API path was affected and it went unnoticed.

This is the mirror of the 0.4.0 fix "GMTSAR_SBAS and both dolphin analyzers
never recording themselves in insarhub_config.json, so a CLI run left the folder
with no analyzer badge in the GUI" -- that one covered analyzers; processors had
the same gap.

The marker is written at submit(), not at construction: building a processor
merely to inspect it (--list-options, a GUI defaults lookup, --dry-run) must not
stamp a folder. Hyp3Base marks at construction and needs an explicit dry_run
guard for exactly that reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BUG = {
    "id": "processor-workflow-marker",
    "fixed_in": "unreleased",
    "area": "processor",
}

PROCESSOR_DIR = Path(__file__).resolve().parents[2] / "src" / "insarhub" / "processor"

# Files that own a submit() a user can reach, and must stamp the folder.
MARKING_SOURCES = ["hyp3_base.py", "isce2_s1.py", "gmtsar_s1.py", "isce3_base.py"]


@pytest.mark.parametrize("name", MARKING_SOURCES)
def test_processor_writes_the_workflow_marker(name):
    text = (PROCESSOR_DIR / name).read_text()
    assert "write_workflow_marker" in text, (
        f"{name} never calls write_workflow_marker(), so a workdir it produces "
        "records no processor and the GUI shows a blank processor badge"
    )


@pytest.mark.parametrize("name", ["isce2_s1.py", "gmtsar_s1.py", "isce3_base.py"])
def test_marker_records_the_processor_role(name):
    """It must record `processor=`, not just any role."""
    text = (PROCESSOR_DIR / name).read_text()
    assert re.search(r'"processor":\s*type\(self\)\.name', text), (
        f"{name} calls write_workflow_marker but does not pass "
        'processor=type(self).name'
    )


@pytest.mark.parametrize("name", ["isce2_s1.py", "gmtsar_s1.py", "isce3_base.py"])
def test_marker_is_written_from_submit_not_construction(name):
    """Constructing a processor to inspect it must not stamp a folder.

    --list-options, a GUI defaults lookup and --dry-run all build a processor
    without intending to touch the workdir.
    """
    text = (PROCESSOR_DIR / name).read_text()
    marker_at = text.index("write_workflow_marker(")
    submit_at = text.index("    def submit(")
    assert marker_at > submit_at, (
        f"{name} writes the workflow marker before submit(); a processor built "
        "only for inspection would stamp the folder"
    )
