"""Shared infrastructure for ISCE3 + COMPASS local processors.

``ISCE3_Base`` is the ISCE3-side analogue of :mod:`insarhub.processor.isce2_base`
(which serves ISCE2's stackSentinel processors) and ``Hyp3Base`` (the cloud
side): it carries the machinery every ISCE3/COMPASS processor needs -- the
isce3 conda env discovery, the per-stage status-marker convention, the
detached-background stage runner, and job persistence -- so a concrete processor like
:class:`~insarhub.processor.ISCE3_Burst` (and later an ISCE3_NISAR variant)
only implements its own stages.

Status bookkeeping mirrors GMTSAR_S1's ``.stack_status`` marker convention
rather than ISCE2_Base's ``.status`` files: one directory per stage under
``workdir/.stage_status/`` holding a ``.succeeded`` or ``.failed`` marker, so
each stage can be inspected, deleted and re-run on its own.

A subclass declares its stage order via ``STAGES``, marks any not-yet-
implemented stage by leaving it out of ``_IMPLEMENTED``, and implements one
``run_<stage>(self, force=False) -> bool`` method per implemented stage; the
base ``submit()`` discovers them by name. Imports of isce3/COMPASS/dolphin
stay deferred inside the stage methods on purpose -- those live in their own
conda env, not necessarily the one InSARHub runs from -- so this module (and
``import insarhub``) must not import them at module scope.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from colorama import Fore, Style

from insarhub.config.paths import ISCE3Paths
from insarhub.core.base import LocalProcessor

logger = logging.getLogger(__name__)

#: marker files a stage writes, mirroring GMTSAR_S1's .stack_status convention
_SUCCEEDED, _FAILED, _RUNNING, _PENDING = "SUCCEEDED", "FAILED", "RUNNING", "PENDING"


def _find_isce3_bin(configured: str | None = None) -> Path | None:
    """Locate a bin/ containing COMPASS's ``s1_cslc.py``.

    Order: explicit config -> the running interpreter's own bin -> sibling conda
    envs. Returns None when nothing is found; callers degrade to bare PATH so
    the failure surfaces as "command not found" rather than a wrong guess.
    """
    if configured:
        p = Path(configured)
        if (p / "s1_cslc.py").exists():
            return p
        logger.warning("ISCE3_Base: isce3_env_bin=%s has no s1_cslc.py", p)
    here = Path(sys.executable).parent
    if (here / "s1_cslc.py").exists():
        return here
    envs = here.parent.parent          # .../envs/<name>/bin -> .../envs
    if envs.name == "envs" and envs.is_dir():
        for cand in sorted(envs.iterdir()):
            if (cand / "bin" / "s1_cslc.py").exists():
                return cand / "bin"
    return None


class ISCE3_Base(LocalProcessor):
    """Base class for ISCE3 + COMPASS local processors.

    Subclasses set ``name``/``description``/``compatible_downloader``/
    ``default_config``, declare their stage order via ``STAGES``, and
    implement ``run_<stage>(self, force=False) -> bool`` for each implemented
    stage. Everything else -- env discovery, status markers, the stage loop,
    refresh/retry/save -- lives here.
    """

    #: This processor derives its own interferogram network from the assembled
    #: input products in slc/ (dolphin forms it from the phase-linked SLCs), so
    #: it does NOT require a select_pairs step / stack_p*_f*.json in the workdir
    #: -- the input is the downloaded stack itself. The GUI submit route keys on
    #: this to look for slc/<input_glob> instead of a stack file.
    builds_own_network: bool = True

    #: glob identifying the downloaded input products in slc/. ISCE3_Burst
    #: consumes S1_Burst's assembled ``*.SAFE``; ISCE3_NISAR consumes NISAR
    #: ``*GSLC*.h5`` granules. The GUI submit route uses this instead of
    #: hardcoding one downloader's file type.
    input_glob: str = "*.SAFE"

    #: stage order. Anything in STAGES but absent from _IMPLEMENTED is
    #: skipped with a note by submit(). Subclasses override.
    STAGES: tuple[str, ...] = ()
    _IMPLEMENTED: tuple[str, ...] = ()

    #: job-file name written by save() into workdir. Kept per-subclass so a
    #: workdir running both ISCE3 processors keeps their job files apart
    #: (ISCE3_Burst writes isce3_burst_jobs.json; a future ISCE3_NISAR writes
    #: its own).
    JOBS_FILE: str = "isce3_jobs.json"

    #: written to sbatch_options.json when a workdir has none. One entry per
    #: stage, since stages differ enormously in what they need -- cslc is the
    #: heavy geocoder, unwrap is snaphu, dem/tec are network-bound.
    SBATCH_DEFAULT_TEMPLATE: dict = {}

    #: python modules a child job's stages import. Checked BEFORE submitting,
    #: because every child re-enters via `sys.executable` -- so a submission
    #: made from an environment that happens to carry insarhub but not the
    #: processing stack builds jobs that all fail identically, minutes later,
    #: with a ModuleNotFoundError buried in a per-job log.
    REQUIRED_MODULES: tuple[str, ...] = ()

    def __init__(self, pairs: list | None = None, config=None):
        super().__init__(config=config)
        self.pairs = list(pairs or [])
        self.jobs: dict = {}
        wd = getattr(self.config, "workdir", None) or Path.cwd()
        self.workdir = Path(wd).expanduser().resolve()
        self._paths = ISCE3Paths(self.workdir)

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    def _p(self, field: str, default: str) -> Path:
        v = getattr(self.config, field, None)
        return Path(v).expanduser() if v else self.workdir / default

    @property
    def _nw(self) -> int:
        return max(1, int(getattr(self.config, "max_workers", 3)))

    # ------------------------------------------------------------------
    # stage status (marker files)
    # ------------------------------------------------------------------

    def _status_dir(self, stage: str) -> Path:
        return self._paths.stage_status_dir(stage)

    def _set_status(self, stage: str, status: str) -> None:
        d = self._status_dir(stage)
        d.mkdir(parents=True, exist_ok=True)
        for n in (".succeeded", ".failed", ".running"):
            (d / n).unlink(missing_ok=True)
        if status == _SUCCEEDED:
            (d / ".succeeded").touch()
        elif status == _FAILED:
            (d / ".failed").touch()
        elif status == _RUNNING:
            # Only written here (local background executor) so refresh() can
            # report a stage as RUNNING while the detached child is mid-stage.
            (d / ".running").touch()

    def _get_status(self, stage: str) -> str:
        d = self._status_dir(stage)
        if (d / ".succeeded").exists():
            return _SUCCEEDED
        if (d / ".failed").exists():
            return _FAILED
        if (d / ".running").exists():
            return _RUNNING
        return _PENDING

    # ------------------------------------------------------------------
    # environment
    # ------------------------------------------------------------------

    def _env(self) -> dict:
        env = dict(os.environ)
        b = _find_isce3_bin(getattr(self.config, "isce3_env_bin", None))
        if b:
            env["PATH"] = f"{b}{os.pathsep}{env.get('PATH', '')}"
        return env

    # ------------------------------------------------------------------
    # LocalProcessor surface
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # HPC (SLURM)
    # ------------------------------------------------------------------

    def hpc_phases(self, stage: str) -> list[tuple[str, list[str]]]:
        """``[(phase_name, [shell command, ...]), ...]`` for one stage.

        Each command is one SLURM child job, so this is where a stage decides
        how finely it parallelises. Phases within a stage run STRICTLY IN
        ORDER (manager chains to manager); the commands inside a phase run
        concurrently, at most ``max_concurrent_hpc`` at a time.

        Two phases are what a map/reduce stage needs -- e.g. `static` geocodes
        every burst in parallel, then a second, single-command phase mosaics
        the result. Without that split the reduce could start before its
        inputs existed, because a sliding window gives no ordering guarantee
        inside a phase.

        Subclasses implement this. Returning ``[]`` means "no HPC decomposition
        known", and :meth:`_submit_hpc` refuses rather than guessing.
        """
        return []

    def _check_child_interpreter(self, tag: str) -> None:
        """Refuse to submit if this interpreter cannot run the child jobs.

        Child jobs re-enter via ``sys.executable``, so they inherit whichever
        environment ran ``submit`` -- which is not necessarily one that can do
        the work. Installing insarhub into a second env for an unrelated
        processor is enough: submitting ISCE3_Burst from an env without dolphin
        produced jobs that queued, started, and died on
        ``ModuleNotFoundError: No module named 'dolphin'``, one log deep.

        Checked in-process rather than by launching a probe: these modules are
        already importable here if they are importable at all, and the child
        uses this very interpreter.
        """
        import importlib.util

        missing = [m for m in self.REQUIRED_MODULES
                   if importlib.util.find_spec(m) is None]
        if not missing:
            return
        raise EnvironmentError(
            f"{tag}: this interpreter cannot import {', '.join(missing)}, and "
            f"every SLURM child job re-enters through it:\n"
            f"    {sys.executable}\n"
            f"  The jobs would submit, start, then fail with ModuleNotFoundError.\n"
            f"  Re-run submit from an environment that has {tag}'s processing "
            f"stack installed.")

    def _check_no_live_jobs(self, tag: str, stages: list[str], force: bool) -> None:
        """Refuse to submit a stage that already has jobs in the queue.

        Nothing else notices: completion markers are cleared on every
        submission (they must be -- see _invalidate_stale_markers), and the
        manager only skips work marked ``.done``. So resubmitting a stage whose
        previous run is still executing quietly starts the SAME work a second
        time. For a single-job stage like ISCE3_Burst's `ifg` that means two
        processes writing one output directory -- <burst>/linked_phase/ and
        <burst>/interferograms/ -- with no error from either, and neither result
        trustworthy.

        This happened: an `ifg` resubmitted 26 minutes into a live run queued a
        duplicate child, and cancelling the duplicate then killed the new
        manager (a vanished child reads as a failed child), leaving an orphan
        doing the real work with no supervisor.

        ``force=True`` overrides, for the case where the queued jobs are known
        to be dead or unwanted -- cancel them first if you actually want a
        clean restart.
        """
        live = self._live_job_names()
        if not live:
            return
        busy: dict[str, list[str]] = {}
        for jid, name in live.items():
            for s in stages:
                # children are b_<stage>_<idx>, managers b_<stage>_mgr; a phase
                # of a stage is b_<stage>__<phase>_*, so prefix-match the stage.
                if name.startswith(f"b_{s}"):
                    busy.setdefault(s, []).append(jid)
        if not busy or force:
            if busy and force:
                print(f"{Fore.YELLOW}[{tag}] force: submitting anyway with "
                      f"{sum(len(v) for v in busy.values())} job(s) still live "
                      f"-- they will run the same work concurrently."
                      f"{Style.RESET_ALL}")
            return
        lines = "\n".join(f"    {s}: {len(v)} job(s) -- {' '.join(sorted(v)[:6])}"
                          for s, v in sorted(busy.items()))
        raise RuntimeError(
            f"{tag}: these stages already have jobs in the SLURM queue:\n"
            f"{lines}\n"
            f"  Submitting again would run the same work a second time, "
            f"concurrently, into the same output directories.\n"
            f"  Wait for them, or cancel first:\n"
            f"    insarhub processor -N {tag} -w {self.workdir} cancel\n"
            f"  Pass force=True (CLI: --force) only if you know these jobs are dead.")

    def stage_progress(self, stage: str) -> tuple[int, int]:
        """``(done, total)`` for a stage from its PRODUCTS, or (0, 0) if the
        subclass does not define one. Overridden by concrete processors; the
        (0, 0) default makes refresh() fall back to counting the last
        submission's job markers."""
        return (0, 0)

    def stage_units(self, stage: str) -> list[tuple[str, bool]]:
        """``[(unit label, done), ...]`` for ``--ls``, or [] if undefined."""
        return []

    def _hpc_dir(self) -> Path:
        return self._paths.hpc_dir

    def _manager_paths(self, key: str) -> tuple[Path, Path]:
        """Where a phase's manager script WILL be written, and its id file.

        Must match build_sliding_window_manager's own convention exactly
        (``<sbatch_dir>/manager.sbatch``): each phase's chain trailer sbatch's
        the NEXT phase's script by path, and it is built after this is needed,
        so the path is predicted rather than returned. Guessing a different
        name here makes every chain step submit a file that does not exist --
        the run would appear to succeed and then silently stop after phase 1.
        """
        d = self._hpc_dir() / key
        return d / "sbatch" / "manager.sbatch", d / "chained_job_id.txt"

    def _submit_hpc(self, stages: list[str], force: bool = False) -> dict:
        """Build one sliding-window manager per phase and submit only the first.

        Every manager chain-submits the next from inside its own success path
        (``chain_submit_lines``) rather than all of them being pre-submitted
        with ``--dependency=afterok``. Pre-submitting leaves every not-yet-
        running manager sitting in the queue as its own job, counting against
        the account's QOS submitted-jobs-per-user limit while doing nothing --
        the same problem that was fixed for ISCE2_S1.
        """
        import subprocess

        from insarhub.processor.isce2_base import load_or_init_sbatch_options
        from insarhub.utils.slurm_manager import build_sliding_window_manager

        from insarhub.utils.slurm_manager import qos_max_submit_jobs

        tag = type(self).name
        max_concurrent = max(1, int(getattr(self.config, "max_concurrent_hpc", 12)))
        self._check_child_interpreter(tag)
        self._check_no_live_jobs(tag, stages, force)

        # ── expand stages into phases, skipping ones already satisfied ────
        plan: list[tuple[str, str, list[str]]] = []      # (stage, key, commands)
        deferred: list[tuple[str, str]] = []             # (stage, why)
        for stage in stages:
            if self._get_status(stage) == _SUCCEEDED and not force:
                print(f"[{tag}] {stage}: already SUCCEEDED, skipping")
                continue
            # A stage's job count comes from its INPUT directory, which a later
            # stage in the same submission has not produced yet. Rather than
            # failing the whole plan, submit everything that IS enumerable and
            # name the rest -- the chain then continues with a second command
            # once its inputs exist.
            try:
                phases = self.hpc_phases(stage)
            except (FileNotFoundError, RuntimeError) as exc:
                deferred.append((stage, str(exc).split(".")[0]))
                continue
            if not phases or not any(c for _n, c in phases):
                deferred.append((stage, "nothing to run yet"))
                continue
            for pname, cmds in phases:
                if not cmds:
                    continue
                plan.append((stage, f"{stage}__{pname}" if pname else stage, cmds))

        def _deferred_note() -> None:
            if not deferred:
                return
            names = " ".join(s for s, _ in deferred)
            print(f"\n[{tag}] deferred -- their inputs do not exist yet:")
            for s, why in deferred:
                print(f"    {s:<10} {why}")
            print(f"  Submit them once the stages above finish:\n"
                  f"    insarhub processor -N {tag} -w {self.workdir} --config "
                  f"--hpc-mode submit --worker {max_concurrent} --step {names}")

        if not plan:
            if deferred:
                print(f"[{tag}] nothing could be submitted yet.")
                _deferred_note()
            else:
                print(f"[{tag}] nothing to submit -- every requested stage is done.")
            return self.jobs

        per_step = load_or_init_sbatch_options(
            self.workdir, step_key=plan[0][0], step_label=plan[0][0],
            default_template=self.SBATCH_DEFAULT_TEMPLATE)
        if per_step is None:
            raise RuntimeError(
                f"No sbatch_options.json found -- a default was written to "
                f"{self.workdir / 'sbatch_options.json'}. Review the resources "
                f"for each stage, then submit again.")

        def _print_plan(scripts: list | None = None) -> None:
            print(f"\n[{tag}] HPC plan ({max_concurrent} concurrent job(s) max):")
            for i, (stage, key, cmds) in enumerate(plan):
                name = f"   {scripts[i].name}" if scripts else ""
                print(f"    {key:<24} {len(cmds):>4} job(s){name}")
                # Threads are derived from cpus_per_task (see below), so the
                # two cannot disagree any more -- report the pairing instead.
                cpus = int(self._stage_slurm_kwargs(per_step, stage)
                           .get("cpus_per_task", 0) or 0)
                if cpus:
                    print(f"      {cpus} core(s) x {len(cmds)} job(s)"
                          + (f", {min(len(cmds), max_concurrent)} at a time"
                             if len(cmds) > 1 else ""))

        if getattr(self.config, "dry_run", False):
            _print_plan()
            print(f"[{tag}] dry run -- nothing submitted, nothing written.")
            _deferred_note()
            return self.jobs

        # ── precompute every phase's manager path so each can point at the
        #    next, then build them all (none submitted yet) ──────────────
        #
        # Everything below WRITES to hpc/<phase>/sbatch/. That is why the dry
        # run returns above rather than building and skipping the sbatch: a
        # manager script is rebuilt from whatever stage list THIS invocation
        # was given, so a dry run over a subset would overwrite a live run's
        # manager with one whose chain trailer points somewhere else -- or
        # nowhere. That silently stopped a running chain dead after the stage
        # it had reached.
        keys = [k for _s, k, _c in plan]
        paths = [self._manager_paths(k) for k in keys]

        scripts: list[Path] = []
        for i, (stage, key, cmds) in enumerate(plan):
            d = self._hpc_dir() / key
            (d / "logs").mkdir(parents=True, exist_ok=True)
            (d / "sbatch").mkdir(parents=True, exist_ok=True)
            self._invalidate_stale_markers(d, cmds)
            nxt_script, nxt_jobfile = paths[i + 1] if i + 1 < len(plan) else (None, None)

            status_dir = self._status_dir(stage)
            status_dir.mkdir(parents=True, exist_ok=True)
            # Translate the manager's SUCCEEDED/FAILED into the marker files
            # refresh() already reads, so an HPC run and a local run report
            # status through exactly one mechanism.
            write_status_fn = (
                f'STATUS_DIR="{status_dir}"\n'
                'write_status() {\n'
                '    rm -f "$STATUS_DIR/.succeeded" "$STATUS_DIR/.failed"\n'
                '    if [[ "$1" == "SUCCEEDED" ]]; then touch "$STATUS_DIR/.succeeded";\n'
                '    else touch "$STATUS_DIR/.failed"; fi\n'
                '}'
            )
            # Threads inside each child come from THIS stage's cpus_per_task,
            # not from a global max_workers. The two must agree or the job
            # either leaves cores idle or oversubscribes them, and a single
            # global value cannot satisfy stages that reserve different amounts
            # (stitch/filt take 2, unwrap 4, ifg 8). Appending the override to
            # the re-entry command makes each child self-describing -- the
            # sbatch script says how many cores it asked for and the command on
            # the next line uses exactly that many.
            cpus = int(self._stage_slurm_kwargs(per_step, stage)
                       .get("cpus_per_task", 0) or 0)
            if cpus:
                cmds = [f"{c} --max_workers {cpus}" if "run-stage-unit" in c else c
                        for c in cmds]

            scripts.append(build_sliding_window_manager(
                job_name_base=f"b_{key}"[:24],
                commands=cmds,
                log_dir=d / "logs",
                sbatch_dir=d / "sbatch",
                max_concurrent=max_concurrent,
                slurm_kwargs=self._stage_slurm_kwargs(per_step, stage),
                env_lines=self._hpc_env_lines(),
                write_status_fn=write_status_fn,
                # A manager only has to outlive its children, so it takes the
                # longest-walltime partition available rather than the one the
                # real work runs on. Its own sizing is fixed by slurm_manager.
                manager_partition=((per_step or {}).get("manager") or {}).get("partition"),
                next_manager_script=nxt_script,
                next_job_id_file=nxt_jobfile,
                file_prefix=key, label=key,
            ))

        # A manager counts against the same ceiling as its children, so the
        # usable window is (limit - 1). Checked BEFORE submitting: exceeding it
        # does not fail gracefully -- sbatch refuses the first child past the
        # cap and the manager abandons the stage partway through.
        part = self._stage_slurm_kwargs(per_step, plan[0][0]).get("partition")
        limit, qos = qos_max_submit_jobs(part)
        if limit is not None:
            # Clamp to limit-2 rather than limit-1: one slot goes to the
            # manager itself, and the second is headroom for the moment a
            # chain fires, when the finishing manager and the next one both
            # briefly exist. Running right at the ceiling makes that handover
            # the thing that fails.
            usable = max(1, limit - 2)
            if max_concurrent > usable:
                print(f"{Fore.YELLOW}[{tag}] --worker {max_concurrent} exceeds "
                      f"what partition {part!r} allows: its QOS {qos!r} caps you "
                      f"at {limit} SUBMITTED jobs. Reducing to {usable} "
                      f"(leaving one slot for the manager and one for the "
                      f"chain handover).{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}  The cap counts EVERY job you have queued, "
                      f"not just this run -- lower --worker further if you have "
                      f"others pending.{Style.RESET_ALL}")
                max_concurrent = usable
            else:
                print(f"[{tag}] QOS {qos!r} on partition {part!r} allows {limit} "
                      f"submitted job(s); using {max_concurrent} + 1 manager")

        _print_plan(scripts)

        out = subprocess.run(["sbatch", str(scripts[0])], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if out.returncode != 0:
            raise RuntimeError(f"sbatch failed for {scripts[0]}: {out.stdout}")
        jid = "".join(c for c in out.stdout if c.isdigit())
        print(f"[{tag}] submitted {scripts[0].name} -> job {jid}; "
              f"{len(plan) - 1} later phase(s) chain from it.")
        _deferred_note()

        for stage, key, cmds in plan:
            self.jobs[stage] = {"stage": stage, "status": _RUNNING,
                                "hpc": True, "manager_job_id": jid if key == keys[0] else None}
        self.save()
        return self.jobs

    @staticmethod
    def _invalidate_stale_markers(phase_dir: Path, commands: list[str]) -> None:
        """Drop ``cmd_*.done``/``.fail`` markers left by a DIFFERENT command list.

        Markers are keyed by position (``cmd_0007.done``), but the command list
        is re-derived on every submission and already-finished work is filtered
        out of it -- so index 7 routinely means a different job than it did last
        time. A manager skips any index whose ``.done`` exists, so stale markers
        make it skip real work while reporting success.

        This is not hypothetical: a resumed cslc stage silently skipped 19 of
        192 burst-dates that way. Run 1 covered the first 19 of the unfiltered
        list and left cmd_0000-0018.done behind; run 2's list had those removed,
        so its own cmd_0000-0018 -- 19 completely different dates -- were
        treated as already done. The stage reported SUCCEEDED with 19 products
        missing.

        The command list is recorded next to the markers; when it changes, the
        markers describe work that no longer exists and are cleared.
        """
        stamp = phase_dir / "logs" / "commands.txt"
        current = "\n".join(commands)
        # NOTE: no early return when the command list is unchanged. Markers are
        # cleared on EVERY submission of a stage, because reaching here already
        # means the stage is going to run -- _submit_hpc skips anything already
        # SUCCEEDED long before this.
        #
        # Keying only on the command list was wrong in the other direction: a
        # stage whose command is a fixed string (ISCE3_Burst's `ifg` is just
        # `run-stage-unit --stage ifg`) keeps identical markers forever, so
        # after deleting its products and resubmitting, the manager skipped its
        # one command as "already done", finished in 0 seconds, and wrote
        # SUCCEEDED for a stage that had produced nothing.
        #
        # Resume does not depend on these markers: stages that support it
        # (cslc, and anything else with expensive units) filter finished work
        # out of the command list itself -- see _prepare_cslc_runs -- so
        # clearing markers never re-runs work the list already excluded.
        stale = (list((phase_dir / "logs").glob("cmd_*.done"))
                 + list((phase_dir / "logs").glob("cmd_*.fail")))
        for f in stale:
            f.unlink(missing_ok=True)
        # Stale CHILD SCRIPTS matter too, though only cosmetically: refresh()
        # takes a phase's job count from how many *.sbatch files exist, so
        # leftovers from a longer previous list inflate the denominator and
        # report work as PENDING that no longer exists (a completed 19-job
        # resume read as "19/192, cmd_0019-0191 pending"). Only scripts beyond
        # the new list are removed -- the rest get overwritten in place.
        dropped = 0
        for f in (phase_dir / "sbatch").glob("*_[0-9][0-9][0-9][0-9].sbatch"):
            try:
                if int(f.stem.rsplit("_", 1)[1]) >= len(commands):
                    f.unlink(missing_ok=True)
                    dropped += 1
            except (ValueError, IndexError):
                continue
        if stale or dropped:
            print(f"  {phase_dir.name}: cleared {len(stale)} completion "
                  f"marker(s)" + (f" and {dropped} orphaned child script(s)"
                                  if dropped else "")
                  + " so this submission actually runs")
        stamp.write_text(current)

    def _stage_slurm_kwargs(self, per_step: dict, stage: str) -> dict:
        """Child-job resources for one stage, from sbatch_options.json.

        ``default`` is a BASE that every stage inherits, with the stage's own
        entry layered on top -- not a fallback used only when the stage is
        absent. That distinction matters for site-wide settings: a partition or
        account belongs in ``default`` once, while each stage overrides only the
        time/cpus/mem it actually needs. Treating ``default`` as an either/or
        (the original behaviour) meant any stage with its own entry silently
        lost the partition, and every job landed on the cluster default.
        """
        import dataclasses as _dc

        from insarhub.utils.tool import Slurmjob_Config

        base = dict((per_step or {}).get("default") or {})
        base.update((per_step or {}).get(stage) or {})
        fields = {f.name for f in _dc.fields(Slurmjob_Config)}
        skip = {"job_name", "output_file", "error_file", "dependency",
                "command", "modules", "conda_env", "export_env", "array"}
        return {k: v for k, v in base.items() if k in fields and k not in skip}

    def _hpc_env_lines(self) -> list[str]:
        """Bash lines putting COMPASS/ISCE3 on PATH inside a child job.

        A SLURM child starts from a login shell, not this process, so the
        interpreter that auto-detection found here is not on its PATH by
        default -- and the subprocess half of the pipeline (s1_cslc.py,
        s1_static_layers.py, sardem) would fail with "command not found".
        """
        b = _find_isce3_bin(getattr(self.config, "isce3_env_bin", None))
        lines = []
        if b:
            lines.append(f'export PATH="{b}:$PATH"')
        return lines

    def cancel(self) -> None:
        """Stop this workdir's jobs: SLURM via scancel, local via SIGTERM.

        Local (non-HPC) runs are detached background processes tracked by
        ``executor.pid`` (written by _start_local_background /
        _reinvoke_via_container); HPC runs are manager/child SLURM jobs whose
        names all start with ``b_``.
        """
        import signal
        import subprocess

        ids = set()
        for f in self._hpc_dir().rglob("chained_job_id.txt"):
            t = f.read_text().strip()
            if t.isdigit():
                ids.add(t)
        for j in self.jobs.values():
            if j.get("manager_job_id"):
                ids.add(str(j["manager_job_id"]))
        # Children are submitted by the manager at run time, so their ids are
        # only discoverable from the queue -- match on the job-name stem.
        for jid, name in self._live_job_names().items():
            if name.startswith("b_"):
                ids.add(str(jid))
        if ids:
            subprocess.run(["scancel", *sorted(ids)], text=True)
            print(f"[{type(self).name}] cancelled {len(ids)} job(s): {' '.join(sorted(ids))}")
            return

        # No SLURM jobs on record: fall back to a local background executor.
        pid_file = self._paths.executor_pid
        if not pid_file.exists():
            print(f"[{type(self).name}] no SLURM jobs or local executor to cancel.")
            return
        try:
            pid = int(pid_file.read_text().strip())
        except ValueError:
            pid_file.unlink(missing_ok=True)
            print(f"[{type(self).name}] stale executor.pid removed.")
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            print(f"[{type(self).name}] sent SIGTERM to local executor (PID {pid}).")
        except (ProcessLookupError, PermissionError):
            print(f"[{type(self).name}] local executor already finished.")
        pid_file.unlink(missing_ok=True)
        for s in self.STAGES:
            if self._get_status(s) == _RUNNING:
                self._set_status(s, _FAILED)

    def submit(self, steps: list[str] | None = None, force: bool = False) -> dict:
        """Run the requested stages in order. Defaults to every implemented one.

        Local (non-HPC, non-container) mode forks a detached background process
        and returns immediately -- stage verdicts are written to the per-stage
        marker files (``.stage_status/<stage>/``) and read back by refresh()/
        retry(). Running the dem..los pipeline synchronously here would block
        the caller (notably the web GUI's submit request) for the entire run,
        leaving the UI stuck on "Submitting…".
        """
        tag = type(self).name
        want = list(steps) if steps else list(self._IMPLEMENTED)
        unknown = [s for s in want if s not in self.STAGES]
        if unknown:
            raise ValueError(f"{tag}: unknown stage(s) {unknown}; "
                             f"valid: {list(self.STAGES)}")
        todo = [s for s in want if s in self._IMPLEMENTED]
        if skipped := [s for s in want if s not in self._IMPLEMENTED]:
            print(f"[{tag}] not yet implemented, skipping: {skipped}")

        # `not INSARHUB_CONTAINER_CHILD`: when this same submit runs *inside* the
        # container (re-invoked by _reinvoke_via_container, which sets that env
        # var), config.container is still set, so without this guard it would
        # call `docker run` AGAIN inside the image -- which ships no docker CLI,
        # giving "/bin/sh: docker: not found". The child must run the stages
        # locally instead. ISCE2 (_reinvoke_via_container caller) and GMTSAR both
        # guard the same way; ISCE3 was missing it, which surfaced only from the
        # GUI: the app route re-persists `container` into insarhub_config.json
        # after submit(), so the container-side re-reads it as set (the CLI writes
        # that config before submit, so the container-stripped reinvoke write wins).
        if getattr(self.config, "container", None) and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
            self._record_stages(todo)
            self._reinvoke_via_container(steps=todo or None)
            return self.jobs

        if bool(getattr(self.config, "hpc_mode", False)):
            return self._submit_hpc(todo, force=force)

        self._record_stages(todo)
        self._start_local_background(todo, force=force)
        return self.jobs

    def _record_stages(self, todo: list[str]) -> None:
        """Record every requested stage as RUNNING and persist the job file.

        Done up front (before forking / re-invoking the container) so refresh()/
        retry(), which reload from the saved job file, see a full stage set and
        submit() returns a meaningful dict immediately. The detached child (or
        container-side process) overwrites these with real SUCCEEDED/FAILED
        verdicts via _run_stages()/save().
        """
        for stage in todo:
            self.jobs[stage] = {"stage": stage, "status": _RUNNING}
        self.save()

    def _run_stages(self, todo: list[str], force: bool = False) -> None:
        """Run ``todo`` stages in order, updating markers and self.jobs.

        Executed in the detached child (local mode) or the container-side
        process -- never in the process that called submit().
        """
        tag = type(self).name
        for stage in todo:
            runner = getattr(self, f"run_{stage}", None)
            if runner is None:
                raise ValueError(f"{tag}: no run_{stage}() method for stage {stage}")
            print(f"\n===== {stage} =====")
            self._set_status(stage, _RUNNING)
            try:
                good = runner(force=force)
            except Exception as exc:                             # noqa: BLE001
                logger.error("%s: stage %s raised: %s", tag, stage, exc)
                good = False
            self._set_status(stage, _SUCCEEDED if good else _FAILED)
            self.jobs[stage] = {"stage": stage,
                                "status": _SUCCEEDED if good else _FAILED}
            if not good:
                print(f"[{tag}] stage {stage} FAILED -- stopping")
                break
        self.save()

    def _start_local_background(self, stages: list[str], force: bool = False) -> None:
        """Fork a detached process to run stages; parent returns immediately.

        Skipped when re-invoked inside a container by _reinvoke_via_container:
        that host-side call already forks and blocks on `docker run`/apptainer,
        which is what provides the "return control to the user, keep running"
        behavior. Forking again here would let the container's foreground
        process (this one) exit right after the fork, and the container runtime
        would tear the whole container down -- including this freshly-forked,
        barely-started child -- as soon as its main process exits. Running
        _run_stages directly keeps the container alive until the stages finish.
        """
        pid_file = self._paths.executor_pid
        log_file = self._paths.executor_log
        if os.environ.get("INSARHUB_CONTAINER_CHILD"):
            self._run_stages(stages, force=force)
            return
        if os.name == "posix":
            pid = os.fork()
            if pid == 0:  # child — detach and run
                try:
                    os.setsid()
                    # Redirect stdout/stderr FIRST, before any work, so a
                    # failure lands in executor.log (os._exit below never
                    # flushes Python's buffered stderr).
                    with open(log_file, "w") as _lf:
                        os.dup2(_lf.fileno(), sys.stdout.fileno())
                        os.dup2(_lf.fileno(), sys.stderr.fileno())
                    self._run_stages(stages, force=force)
                except BaseException as exc:
                    import traceback
                    try:
                        with open(log_file, "a") as _lf:
                            _lf.write(f"\n[executor] local run failed: {exc}\n")
                            traceback.print_exc(file=_lf)
                            _lf.flush()
                    except Exception:
                        pass
                finally:
                    os._exit(0)
            # parent
            pid_file.write_text(str(pid))
            print(f"{Fore.GREEN}Local executor running in background (PID {pid}).{Style.RESET_ALL}")
            print(f"  log : {log_file}")
            print(f"  Use 'refresh' to check status, 'cancel' to stop.")
        else:
            # Windows: no fork — run blocking
            self._run_stages(stages, force=force)

    def _reinvoke_via_container(self, steps: list[str] | None = None) -> None:
        """Re-run this same `insarhub processor ... submit` CLI call inside
        self.config.container instead of on the host, in a detached background
        process so the calling submit() returns immediately.

        The container image is expected to have `insarhub` (plus ISCE3/COMPASS)
        installed — the container-side process runs the identical InSARHub code,
        so refresh()/retry()/cancel() need no container-awareness of their own:
        they just read the same status files this container-side process writes
        to the shared (bind-mounted) workdir.
        """
        import dataclasses

        from insarhub.utils.config_io import write_insarhub_config
        from insarhub.utils.container import wrap_container_cmd

        cfg_dict = {
            f.name: getattr(self.config, f.name)
            for f in dataclasses.fields(self.config)
            if f.name not in ("container", "workdir", "saved_job_path")
        }
        write_insarhub_config(self.workdir, {
            "processor": {"type": type(self).name, "config": cfg_dict}
        })

        step_args = f" --step {' '.join(steps)}" if steps else ""

        def _build_cli_cmd(host_pid: int | None = None) -> str:
            # INSARHUB_CONTAINER_CHILD tells _start_local_background() (running
            # inside the container) not to fork+detach again: this call already
            # forks and blocks on `docker run`/apptainer below, so backgrounding
            # is already handled at the host level -- see that method's docstring.
            # INSARHUB_HOST_PID (only known once we're the forked child) is this
            # host-side process's own PID, a real host-checkable liveness marker.
            env_prefix = "INSARHUB_CONTAINER_CHILD=1"
            if host_pid is not None:
                env_prefix += f" INSARHUB_HOST_PID={host_pid}"
            return (f"{env_prefix} insarhub processor "
                    f"-N {type(self).name} -w {self.workdir} submit{step_args}")

        log_file = self._paths.executor_log
        pid_file = self._paths.executor_pid
        print(f"{Fore.CYAN}Re-invoking {type(self).name} inside container "
              f"{self.config.container}...{Fore.RESET}")
        if os.name == "posix":
            pid = os.fork()
            if pid == 0:  # child — detach and run
                try:
                    os.setsid()
                    own_pid = os.getpid()
                    # Redirect stdout/stderr FIRST, before any work, so even a
                    # failure in command construction lands in executor.log.
                    # os._exit() below never flushes Python's buffered stderr,
                    # so an uncaught exception here used to be silently lost,
                    # leaving an empty executor.log and a "success" submit.
                    with open(log_file, "w") as _lf:
                        os.dup2(_lf.fileno(), sys.stdout.fileno())
                        os.dup2(_lf.fileno(), sys.stderr.fileno())
                    cli_cmd = _build_cli_cmd(host_pid=own_pid)
                    wrapped = wrap_container_cmd(self.config.container, cli_cmd, self.workdir)
                    result = subprocess.run(wrapped, shell=True)
                    if result.returncode != 0:
                        # `docker run` itself failed, so the container-side
                        # insarhub never ran and never wrote any stage status.
                        # Mark the target steps FAILED here so refresh() surfaces
                        # the error instead of leaving them stuck PENDING forever.
                        # Exit 127 is "command not found" (docker/apptainer not on
                        # PATH), NOT a bad image -- a missing image is docker's
                        # exit 125.
                        if result.returncode == 127:
                            detail = ("docker (or apptainer) not found on PATH "
                                      "-- is the container runtime installed?")
                        elif result.returncode == 125:
                            detail = (f"image '{self.config.container}' missing "
                                      "or misnamed -- check the name/tag")
                        else:
                            detail = f"exit {result.returncode}"
                        print(f"\n[executor] container run failed: {detail}")
                        target = steps or list(self._IMPLEMENTED)
                        for st in target:
                            if self._get_status(st) != _SUCCEEDED:
                                self._set_status(st, _FAILED)
                except BaseException as exc:
                    import traceback
                    try:
                        with open(log_file, "a") as _lf:
                            _lf.write(f"\n[executor] container run failed: {exc}\n")
                            traceback.print_exc(file=_lf)
                            _lf.flush()
                    except Exception:
                        pass
                    # Same as above: don't leave the stages stuck PENDING when
                    # the container never got off the ground.
                    try:
                        for st in (steps or list(self._IMPLEMENTED)):
                            if self._get_status(st) != _SUCCEEDED:
                                self._set_status(st, _FAILED)
                    except Exception:
                        pass
                finally:
                    os._exit(0)
            # parent
            pid_file.write_text(str(pid))
            print(f"{Fore.GREEN}Container executor running in background (PID {pid}).{Style.RESET_ALL}")
            print(f"  log : {log_file}")
            print(f"  Use 'refresh' to check status, 'cancel' to stop.")
        else:
            # Windows: no fork — run blocking. No separate host PID to hand off
            # either: this call itself is already the thing the user is waiting on.
            cli_cmd = _build_cli_cmd()
            wrapped = wrap_container_cmd(self.config.container, cli_cmd, self.workdir)
            result = subprocess.run(wrapped, shell=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Container run failed (exit {result.returncode}): {wrapped}")

    def refresh(self, ls: str | bool | None = None) -> dict:
        """Coloured per-stage status table, matching ISCE2_S1's refresh.

        Marker files are the source of truth for a stage's verdict, but they
        only flip when a manager finishes -- so a stage with 100 of 192 children
        done still reads PENDING. In HPC mode the per-child ``.done``/``.fail``
        markers are counted as well and shown as progress, which is the number
        actually worth watching during a long run.

        ``ls`` shows per-unit (cmd_XXXX) detail: bare ``--ls`` for every stage,
        ``--ls cslc`` for one.
        """
        ls_stage = ls if isinstance(ls, str) else None
        show_all = ls is True
        if ls_stage and ls_stage not in self.STAGES:
            raise ValueError(f"Unknown stage for --ls: {ls_stage!r}. "
                             f"Valid: {list(self.STAGES)}")

        active: dict[str, str] = ({} if not self._hpc_dir().is_dir()
                                  else self._live_job_names())

        colour = {_SUCCEEDED: Fore.GREEN, _FAILED: Fore.RED,
                  _RUNNING: Fore.CYAN, _PENDING: Fore.YELLOW}

        print(f"\n{Style.BRIGHT}  {'STAGE':<12} {'STATUS':<10} "
              f"{'PROGRESS':<12} DETAIL{Style.RESET_ALL}")
        print("  " + "-" * 62)

        self.jobs = {}
        counts: dict[str, int] = {}
        for s in self.STAGES:
            status = self._get_status(s)
            phases = self._hpc_phase_dirs(s)
            # Progress describes the WORKDIR, not the last submission -- see
            # stage_progress(). Falls back to counting this submission's job
            # markers only for a processor that does not define it.
            done, total = self.stage_progress(s)
            fail = sum(len(list((d / "logs").glob("cmd_*.fail"))) for d in phases)
            if total == 0:
                for d in phases:
                    done += len(list((d / "logs").glob("cmd_*.done")))
                    total += max(0, len(list((d / "sbatch").glob("*.sbatch"))) - 1)
            # Live SLURM state OVERRIDES the marker. A marker is written once,
            # when a manager finishes, so it cannot say "in progress" -- and a
            # FAILED marker left by a manager that died is stale the moment a
            # new one is resubmitted for the same stage. Believing the marker
            # there reports FAILED over a run that is actively progressing.
            live = any(n.startswith(f"b_{s}") for n in active.values())
            if status == _SUCCEEDED:
                # Sticky. A stage that produced everything it owes stays
                # SUCCEEDED -- a later partial resubmission, or leftover .fail
                # markers from an attempt that was subsequently repaired, must
                # not demote a complete stage.
                pass
            elif live:
                status = _RUNNING
            elif total and done >= total:
                # Complete on disk but the marker never flipped (a manager was
                # killed, or the work was finished across several resumes).
                status = _SUCCEEDED
                self._set_status(s, _SUCCEEDED)
            elif fail:
                status = _FAILED
            elif done:
                status = _RUNNING

            prog = f"{done}/{total}" if total > 0 else ""
            detail = ""
            if fail:
                detail = f"{fail} unit(s) failed"
            elif s not in self._IMPLEMENTED:
                detail = "(not implemented)"

            counts[status] = counts.get(status, 0) + 1
            self.jobs[s] = {"stage": s, "status": status,
                            "done": done, "failed": fail, "total": total}
            c = colour.get(status, "")
            print(f"  {s:<12} {c}{status:<10}{Style.RESET_ALL} "
                  f"{prog:<12} {detail}")

            if not (show_all or s == ls_stage):
                continue
            # Real units for the whole workdir (see stage_units), not the last
            # submission's cmd_XXXX markers.
            units = self.stage_units(s)
            if units:
                width = max(len(u) for u, _ in units)
                for label, ok in units:
                    st, cc = ((_SUCCEEDED, Fore.GREEN) if ok else
                              (_RUNNING, Fore.CYAN) if status == _RUNNING else
                              (_PENDING, Fore.YELLOW))
                    print(f"      {label:<{width}}  {cc}{st}{Style.RESET_ALL}")
                continue
            for d in phases:
                n = max(0, len(list((d / "sbatch").glob("*.sbatch"))) - 1)
                if n:
                    print(f"    {Style.BRIGHT}{d.name}{Style.RESET_ALL}")
                for i in range(n):
                    if (d / "logs" / f"cmd_{i:04d}.done").exists():
                        st, cc = _SUCCEEDED, Fore.GREEN
                    elif (d / "logs" / f"cmd_{i:04d}.fail").exists():
                        st, cc = _FAILED, Fore.RED
                    elif status == _RUNNING:
                        st, cc = _RUNNING, Fore.CYAN
                    else:
                        st, cc = _PENDING, Fore.YELLOW
                    print(f"      cmd_{i:04d}  {cc}{st}{Style.RESET_ALL}")

        summary = "  ".join(f"{colour.get(k,'')}{k}={v}{Style.RESET_ALL}"
                            for k, v in sorted(counts.items()))
        print("  " + "-" * 62)
        print(f"  {summary}")
        if active:
            mine = [j for j, n in active.items() if n.startswith("b_")]
            if mine:
                print(f"  {len(mine)} job(s) live in SLURM")
        return self.jobs

    @staticmethod
    def _live_job_names() -> dict[str, str]:
        """``{job_id: job_name}`` for this user's queued/running jobs.

        NOT slurm_active_jobs(), which maps job id -> STATE. Both are dicts of
        strings, so mistaking one for the other fails silently: a name check
        against "PENDING"/"RUNNING" simply never matches, and every job looks
        absent from the queue.
        """
        import subprocess

        try:
            p = subprocess.run(["squeue", "-h", "-u", os.environ.get("USER", ""),
                                "-o", "%i %j"], text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               timeout=20)
            if p.returncode != 0:
                return {}
        except Exception:                                        # noqa: BLE001
            return {}
        out: dict[str, str] = {}
        for line in p.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                out[parts[0].strip()] = parts[1].strip()
        return out

    def _hpc_phase_dirs(self, stage: str) -> list[Path]:
        """HPC working dirs for a stage: ``hpc/<stage>`` plus ``hpc/<stage>__*``
        for stages that submit in ordered phases (e.g. unwrap's mask prologue)."""
        root = self._hpc_dir()
        if not root.is_dir():
            return []
        return sorted(d for d in root.iterdir()
                      if d.is_dir() and (d.name == stage
                                         or d.name.startswith(f"{stage}__")))

    def retry(self, steps: list[str] | None = None) -> dict:
        targets = steps or [s for s in self._IMPLEMENTED
                            if self._get_status(s) == _FAILED]
        if not targets:
            print(f"[{type(self).name}] nothing to retry")
            return self.jobs
        for s in targets:
            shutil.rmtree(self._status_dir(s), ignore_errors=True)
        return self.submit(steps=targets)

    def watch(self, interval: int = 30) -> dict:
        """Stages run in a detached background process, so this only reports
        state; call it repeatedly to poll."""
        return self.refresh()

    def save(self) -> Path:
        import json
        p = self.workdir / self.JOBS_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.jobs, indent=1))
        return p
