"""Keeps the regression tier self-describing.

Every bug file must declare a `BUG` dict. That is not bureaucracy: it is what
makes "has this bug ever come back?" answerable by grep instead of by reading
every file, and it stops two people filing the same bug under two names.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent

# Fixed bugs whose regression test lives in another tier, because the fix is
# better expressed as a permanent invariant than as a single input to replay.
# Listed here so this directory remains the full index of fixed bugs.
ELSEWHERE = {
    "0400-cors-wildcard-contract": "tier2_basic/test_api_contract.py::test_no_cors_headers_by_default",
    "0400-ui-field-drift": "tier2_basic/test_registry_and_config.py::test_ui_fields_reference_real_dataclass_fields",
    "0400-nisar-analyzer-mapping": "tier2_basic/test_registry_and_config.py::test_analyzer_declares_a_real_compatible_processor",
}


def _bug_files() -> list[Path]:
    return sorted(p for p in HERE.glob("test_bug_*.py") if p.name != Path(__file__).name)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUG_FILES = _bug_files()


def test_there_are_regression_files():
    assert BUG_FILES, "tier4_regression contains no test_bug_*.py files"


@pytest.mark.parametrize("path", BUG_FILES, ids=[p.stem for p in BUG_FILES])
def test_every_bug_file_declares_metadata(path):
    module = _load(path)
    bug = getattr(module, "BUG", None)
    assert isinstance(bug, dict), f"{path.name} declares no BUG dict (see README.md)"
    for key in ("id", "fixed_in", "area"):
        assert bug.get(key), f"{path.name}: BUG['{key}'] is missing or empty"


@pytest.mark.parametrize("path", BUG_FILES, ids=[p.stem for p in BUG_FILES])
def test_every_bug_file_explains_itself(path):
    """The docstring is the only place the symptom and root cause are recorded;
    without it a future reader cannot tell what the test is protecting."""
    module = _load(path)
    doc = (module.__doc__ or "")
    assert len(doc.strip()) > 80, f"{path.name} has no meaningful module docstring"
    for section in ("Fixed in:", "Symptom:", "Root cause:"):
        assert section in doc, f"{path.name} docstring is missing a `{section}` line"


def test_bug_ids_are_unique():
    ids = {}
    for path in BUG_FILES:
        bug_id = getattr(_load(path), "BUG", {}).get("id")
        if bug_id:
            ids.setdefault(bug_id, []).append(path.name)
    dupes = {k: v for k, v in ids.items() if len(v) > 1}
    assert not dupes, f"duplicate BUG ids: {dupes}"

    overlap = set(ids) & set(ELSEWHERE)
    assert not overlap, f"ids listed both here and in ELSEWHERE: {sorted(overlap)}"


def test_index_report(record_property):
    """Print the index. Never fails -- it is how you see the tier at a glance."""
    print("\nTracked fixed bugs:")
    for path in BUG_FILES:
        bug = getattr(_load(path), "BUG", {})
        record_property(bug.get("id", path.stem), path.name)
        print(f"  {bug.get('fixed_in', '?'):8} {bug.get('id', '?'):34} {bug.get('area', '')}")
    for bug_id, location in sorted(ELSEWHERE.items()):
        print(f"  {'(other)':8} {bug_id:34} -> {location}")
