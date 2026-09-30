"""Tier 2 -- HyP3 project naming, persistence and lookup.

One submission gives every job the same HyP3 name. That is what makes
``find_jobs(name=...)`` able to return the project in a single query -- the API
matches the name exactly, with no wildcards -- and it is also why the name can
no longer identify an individual interferogram. The pair comes from the job's
granules instead, which ``save()`` writes alongside the job IDs.

These tests pin both halves of that bargain, plus the fallback for job files
written before project names existed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from unittest.mock import patch

import pytest
from hyp3_sdk import Batch, HyP3, Job

from insarhub import Processor


G1 = "S1A_IW_SLC__1SDV_20230105T133227_20230105T133254_046689_0598E1_1234"
G2 = "S1A_IW_SLC__1SDV_20230117T133226_20230117T133253_046864_059EC5_ABCD"
G3 = "S1A_IW_SLC__1SDV_20230129T133226_20230129T133253_047039_05A4A2_5678"
PAIRS = [(G1, G2), (G2, G3)]


def _job(job_id, name, granules=None, status="SUCCEEDED"):
    return Job(
        job_type="INSAR_GAMMA",
        job_id=job_id,
        request_time=datetime.now(),
        status_code=status,
        user_id="user1",
        name=name,
        job_parameters={"granules": list(granules)} if granules else None,
    )


@pytest.fixture
def processor(tmp_path):
    """A Hyp3_S1 whose credentials, client and submission are all stubbed."""
    submitted = {}

    def fake_submit_queue(self, job_queue):
        submitted["queue"] = job_queue
        jobs = [
            _job(f"id-{i}", j["name"], j["job_parameters"]["granules"])
            for i, j in enumerate(job_queue)
        ]
        for j in jobs:
            self.job_ids["user1"].append(j.job_id)
        return {"user1": Batch(jobs)}

    with patch.object(HyP3, "__init__", lambda self, *a, **k: None), patch(
        "insarhub.processor.hyp3_base.Hyp3Base._hyp3_authorize", lambda self, pool=None: None
    ), patch(
        "insarhub.processor.hyp3_base.Hyp3Base._submit_job_queue", fake_submit_queue
    ):
        proc = Processor.create(
            "Hyp3_S1", workdir=tmp_path / "work", pairs=PAIRS, dry_run=True
        )
        proc.client = HyP3()
        proc._username_pool, proc._password_pool = ["user1"], ["pw"]
        yield proc, submitted



@pytest.fixture
def raw_processor(tmp_path):
    """Like `processor`, but with the real `_submit_job_queue` in place."""
    with patch.object(HyP3, "__init__", lambda self, *a, **k: None), patch(
        "insarhub.processor.hyp3_base.Hyp3Base._hyp3_authorize", lambda self, pool=None: None
    ):
        proc = Processor.create(
            "Hyp3_S1", workdir=tmp_path / "work", pairs=PAIRS, dry_run=True
        )
        proc._username_pool, proc._password_pool = ["user1"], ["pw"]
        yield proc

# ── One name per submission ──────────────────────────────────────────────────

def test_every_job_in_a_submission_shares_one_name(processor):
    proc, submitted = processor
    proc.config.project_name = "bryce"
    proc.submit()

    names = {j["name"] for j in submitted["queue"]}
    assert names == {"bryce"}, "find_jobs(name=...) matches exactly; one project, one name"


def test_project_name_defaults_to_ifg_date_and_time(processor):
    """Seconds, not just the date -- two stacks submitted the same day must
    not share a project, or find_jobs(name=...) returns both."""
    proc, _ = processor
    proc.submit()
    assert re.fullmatch(r"ifg_\d{8}_\d{6}", proc.project_name), proc.project_name


def test_blank_project_name_falls_back_to_the_default(processor):
    proc, _ = processor
    proc.config.project_name = "   "
    proc.submit()
    assert proc.project_name.startswith("ifg_")


def test_project_name_over_the_hyp3_limit_is_rejected(processor):
    """HyP3 caps `name` at 100 characters; failing here beats a 400 mid-stack."""
    proc, _ = processor
    proc.config.project_name = "x" * 101
    with pytest.raises(ValueError, match="at most 100"):
        proc.submit()


# ── The pair survives, via the granules rather than the name ─────────────────

def test_pair_label_distinguishes_jobs_that_share_a_name(processor):
    proc, _ = processor
    batches = proc.submit()
    labels = [proc._pair_label(j) for j in batches["user1"]]
    assert labels == ["20230105_20230117", "20230117_20230129"]


def test_pair_label_falls_back_to_saved_granules(processor):
    """A reloaded job has no job_parameters -- the saved file has to carry it."""
    proc, _ = processor
    proc._saved_pairs = {"id-0": [G1, G2]}
    assert proc._pair_label(_job("id-0", "bryce")) == "20230105_20230117"


def test_pair_label_degrades_to_the_job_id(processor):
    proc, _ = processor
    assert _job("abcdef1234", "bryce").job_id.startswith(proc._pair_label(_job("abcdef1234", "bryce")))


# ── Persistence ──────────────────────────────────────────────────────────────

def test_save_persists_the_project_name_and_the_granules(processor):
    proc, _ = processor
    proc.config.project_name = "bryce"
    proc.submit()
    payload = json.loads(proc.save().read_text())

    assert payload["project_name"] == "bryce"
    assert payload["jobs"]["user1"][0]["granules"] == [G1, G2]
    # kept so a file written here still loads in an older InSARHub
    assert payload["job_ids"]["user1"] == ["id-0", "id-1"]


# ── Lookup ───────────────────────────────────────────────────────────────────

def _reload_and_refresh(tmp_path, payload):
    """Load a saved job file and capture the kwargs refresh() queries with."""
    calls = []

    def fake_find_jobs(self, **kwargs):
        calls.append(kwargs)
        return Batch([_job("id-0", kwargs.get("name") or "old")])

    job_file = tmp_path / "jobs.json"
    job_file.write_text(json.dumps(payload))

    with patch.object(HyP3, "__init__", lambda self, *a, **k: None), patch.object(
        HyP3, "find_jobs", fake_find_jobs
    ), patch("insarhub.processor.hyp3_base.Hyp3Base._hyp3_authorize", lambda self, pool=None: None):
        proc = Processor.create(
            "Hyp3_S1", workdir=tmp_path / "work", saved_job_path=job_file, dry_run=True
        )
        proc._username_pool, proc._password_pool = ["user1"], ["pw"]
        proc.refresh()
    return proc, calls


def test_refresh_queries_by_project_name(tmp_path):
    proc, calls = _reload_and_refresh(tmp_path, {
        "project_name": "bryce_p42_f118",
        "job_ids": {"user1": ["id-0"]},
        "jobs": {"user1": [{"job_id": "id-0", "granules": [G1, G2]}]},
    })
    assert proc.project_name == "bryce_p42_f118"
    assert calls[0] == {"name": "bryce_p42_f118"}, "no date bound -- old stacks stay reachable"


def test_legacy_job_file_falls_back_to_the_time_window(tmp_path):
    """Files written before project names existed carry no name to search by."""
    _, calls = _reload_and_refresh(tmp_path, {"job_ids": {"user1": ["id-0"]}})
    assert "name" not in calls[0]
    assert "start" in calls[0]


# ── Duplicate submission guard ───────────────────────────────────────────────

G2_REPROCESSED = "S1A_IW_SLC__1SDV_20230117T133226_20230117T133253_046864_059EC5_ZZZZ"


def _record_submitted(workdir, granules, project="bryce_run1"):
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "hyp3_jobs.json").write_text(json.dumps({
        "project_name": project,
        "job_ids": {"user1": ["id-0"]},
        "jobs": {"user1": [{"job_id": "id-0", "granules": list(granules)}]},
    }))


def test_resubmitting_a_recorded_pair_is_refused(processor, tmp_path):
    proc, _ = processor
    _record_submitted(tmp_path / "work", [G1, G2])
    with pytest.raises(ValueError, match="already been submitted"):
        proc._check_duplicate_pairs([{"job_parameters": {"granules": [G1, G2]}}])


def test_duplicates_match_on_dates_not_granule_ids(processor, tmp_path):
    """A re-processed granule is still the same interferogram."""
    proc, _ = processor
    _record_submitted(tmp_path / "work", [G1, G2])
    with pytest.raises(ValueError, match="20230105_20230117"):
        proc._check_duplicate_pairs([{"job_parameters": {"granules": [G1, G2_REPROCESSED]}}])


def test_a_new_pair_is_not_refused(processor, tmp_path):
    proc, _ = processor
    _record_submitted(tmp_path / "work", [G1, G2])
    proc._check_duplicate_pairs([{"job_parameters": {"granules": [G2, G3]}}])


def test_the_guard_runs_on_the_shared_submit_path(raw_processor):
    proc = raw_processor
    calls = []
    with patch.object(type(proc), "_check_duplicate_pairs", lambda self, q: calls.append(q)):
        proc._submit_job_queue([])
        assert len(calls) == 1
        proc._submit_job_queue([], force=True)
        assert len(calls) == 1, "force=True must skip the check"


def test_force_submit_config_bypasses_the_guard(raw_processor):
    proc = raw_processor
    proc.config.force_submit = True
    calls = []
    with patch.object(type(proc), "_check_duplicate_pairs", lambda self, q: calls.append(q)):
        proc._submit_job_queue([])
    assert calls == []


def test_retry_forces_past_the_guard(processor):
    """Every retried pair is by definition already submitted."""
    proc, _ = processor
    proc.failed_jobs = [_job("x", "bryce", [G1, G2], status="FAILED")]
    seen = {}
    with patch.object(type(proc), "_submit_job_queue",
                      lambda self, q, force=False: seen.setdefault("force", force) or {}), \
         patch.object(type(proc), "save", lambda self, path=None: path):
        proc.retry()
    assert seen["force"] is True


def test_legacy_job_files_cannot_be_compared(processor, tmp_path):
    """Files without granules carry no pairs, so nothing can clash."""
    proc, _ = processor
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "hyp3_jobs.json").write_text(json.dumps({"job_ids": {"user1": ["id-0"]}}))
    assert proc._submitted_pair_labels() == {}
    proc._check_duplicate_pairs([{"job_parameters": {"granules": [G1, G2]}}])


# ── Backward compatibility with name_prefix ──────────────────────────────────

def test_name_prefix_still_constructs_and_forwards():
    """Existing scripts pass name_prefix=; that must not raise TypeError."""
    from insarhub.config.defaultconfig import Hyp3_S1_Config
    with pytest.warns(DeprecationWarning, match="name_prefix is deprecated"):
        cfg = Hyp3_S1_Config(name_prefix="ifg")
    assert cfg.project_name == "ifg"


def test_explicit_project_name_beats_the_deprecated_alias():
    from insarhub.config.defaultconfig import Hyp3_S1_Config
    with pytest.warns(DeprecationWarning):
        cfg = Hyp3_S1_Config(name_prefix="old", project_name="new")
    assert cfg.project_name == "new"


def test_no_warning_when_the_alias_is_unused(recwarn):
    from insarhub.config.defaultconfig import Hyp3_S1_Config
    cfg = Hyp3_S1_Config()
    assert cfg.project_name is None
    assert not [w for w in recwarn if w.category is DeprecationWarning]


def test_processor_create_accepts_the_deprecated_alias(tmp_path):
    with patch.object(HyP3, "__init__", lambda self, *a, **k: None), patch(
        "insarhub.processor.hyp3_base.Hyp3Base._hyp3_authorize", lambda self, pool=None: None
    ), pytest.warns(DeprecationWarning):
        proc = Processor.create("Hyp3_S1", workdir=tmp_path / "w",
                                name_prefix="ifg", dry_run=True)
    assert proc.config.project_name == "ifg"


def test_the_alias_is_never_persisted_or_given_a_flag():
    """A generated --name_prefix flag, or a persisted value, would keep the
    dead name alive in every later run."""
    from insarhub.cli.main import _SUBMIT_SKIP_FIELDS
    from insarhub.utils.local_processor_reload import _SAVED_CFG_SKIP
    assert "name_prefix" in _SUBMIT_SKIP_FIELDS
    assert "name_prefix" in _SAVED_CFG_SKIP
