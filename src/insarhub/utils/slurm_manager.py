# -*- coding: utf-8 -*-
"""
Shared SLURM sliding-window job-manager script generation.

Used by any LocalProcessor's HPC mode: one lightweight "manager" sbatch job
keeps at most ``max_concurrent`` child jobs active at a time, submitting new
ones immediately as slots open. Optionally, a manager chain-submits the next
manager in a sequence itself right after it succeeds, instead of every
manager being pre-submitted up front with a SLURM ``--dependency`` chain --
that leaves every not-yet-running manager sitting in the queue as its own
submitted job, which counts against the account's QOS submitted-jobs-per-user
limit even while just waiting on a dependency. Chaining from inside the
script means only one manager (the currently running one) is ever actually
submitted at a time. See ISCE2_Base._step_executor_hpc for the original design
this was extracted from.

Per-command ``.done``/``.fail`` markers are always named ``cmd_<idx>.done`` /
``cmd_<idx>.fail`` in *log_dir* -- callers give each unit of work (step,
stage, ...) its own log_dir, so there's never a naming collision.

Processor-specific status representations (ISCE2_S1's single text file vs
GMTSAR_S1's marker directory) are supplied by the caller as a raw bash
``write_status()`` function body -- the sliding-window loop only ever calls
``write_status "<value>"`` generically, so it doesn't need to know which
convention is in use.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from insarhub.utils.tool import Slurmjob_Config

SLURM_DEAD_STATES = frozenset({
    "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "BOOT_FAIL", "OUT_OF_MEMORY",
})

# Every job manager -- of every processor, for every stage/step -- is sized
# identically, because they all do the identical job: sit on a node running a
# bash poll loop that calls sbatch/squeue. None of the real work happens here
# (that's the child jobs, sized per stage from sbatch_options.json), so a
# manager never needs more than one core and a token amount of memory.
MANAGER_CPUS_PER_TASK = 1
MANAGER_MEM = "1G"

# What a manager's walltime falls back to when the partition's own limit
# can't be determined (sinfo missing/failing, or the partition reports an
# unlimited MaxTime so there's no maximum to adopt).
MANAGER_FALLBACK_TIME = "1-00:00:00"

# How a manager waits out a QOS/association submit ceiling before giving up.
SUBMIT_RETRY_SECS = 30
SUBMIT_MAX_RETRIES = 240          # 30s x 240 = 2 hours


def qos_max_submit_jobs(partition: str | None = None) -> tuple[int | None, str | None]:
    """``(max submitted jobs per user, qos name)`` for a partition, or (None, None).

    Clusters cap how many jobs one user may have QUEUED at once, separately
    from how many may RUN. Exceeding it makes sbatch reject the submission with
    ``QOSMaxSubmitJobPerUserLimit`` -- which, before this was checked up front,
    killed a manager mid-stage after its first full window.

    The limit is not on the association (``sacctmgr show assoc``) but on the
    QOS the partition imposes, so this resolves partition -> QoS -> limit.
    Returns (None, None) whenever anything is unavailable -- no SLURM, no
    permission to query, or an unlimited QOS -- and callers then skip the check
    rather than blocking on a number they could not read.
    """
    import subprocess

    def _run(cmd: list[str]) -> str:
        try:
            p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=15)
            return p.stdout if p.returncode == 0 else ""
        except Exception:                                        # noqa: BLE001
            return ""

    qos = None
    if partition:
        for tok in _run(["scontrol", "show", "partition", partition]).split():
            if tok.startswith("QoS=") and tok[4:] not in ("N/A", "(null)"):
                qos = tok[4:]
                break
    if not qos:
        return (None, None)

    out = _run(["sacctmgr", "-nP", "show", "qos", qos,
                "format=Name,MaxSubmitJobsPerUser"])
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and parts[0] == qos and parts[1].strip().isdigit():
            return (int(parts[1]), qos)
    return (None, qos)


# Every key a step entry in sbatch_options.json may set, with what it does and
# an example value. Written into the file itself as "_options" so the file
# documents itself -- editing sbatch resources should not require reading
# SLURM's manual or this source. Anything NOT listed here is ignored: the
# resolvers keep only Slurmjob_Config fields, so an unrecognised key is
# silently dropped rather than reaching sbatch and having it reject the job.
SBATCH_OPTIONS_HELP: dict = {
    "time": "Walltime limit, HH:MM:SS or D-HH:MM:SS. The job is killed at this "
            "limit, so overestimate. e.g. '04:00:00'",
    "partition": "Queue to run in. `sinfo` lists them with their walltime caps. "
                 "e.g. 'all'",
    "cpus_per_task": "Cores for the job. Stages that thread internally "
                     "(max_workers) should match this. e.g. 4",
    "mem": "Memory for the whole job. Exceeding it is an immediate kill, so "
           "check a finished job with `sacct -j <id> --format=MaxRSS`. e.g. '32G'",
    "nodes": "Nodes to allocate. Leave at 1 -- nothing here spans nodes. e.g. 1",
    "ntasks": "MPI tasks. Leave at 1 unless a stage genuinely uses MPI. e.g. 1",
    "account": "Charge account, if the cluster requires one. An account that "
               "does not exist makes sbatch reject the job. e.g. 'engineering'",
    "qos": "Quality-of-service level, if the cluster defines any. e.g. 'normal'",
    "nodelist": "Pin to specific nodes. Usually a debugging tool -- it can make "
                "a job wait indefinitely. e.g. 'node[01-04]'",
    "gpus": "GPUs per job. None of these stages use one. e.g. 1",
    "mail_user": "Address for job notifications. e.g. 'you@example.edu'",
    "mail_type": "When to email: NONE / BEGIN / END / FAIL / ALL. e.g. 'FAIL'",
}

SBATCH_OPTIONS_COMMENT = (
    "Per-step sbatch resources. 'default' is the BASE every step inherits; a "
    "step's own entry overrides individual keys on top of it, so cluster-wide "
    "settings (partition, account, qos) belong in 'default' once and each step "
    "only overrides the time/cpus/mem it actually needs. "
    "'manager' configures the lightweight job managers: only 'partition' is "
    "read there -- their cores, memory and walltime are fixed (1 idle core, "
    "and the partition's maximum walltime, since a manager must outlive every "
    "child it supervises). "
    "See '_options' below for every key you can set. Keys beginning with '_' "
    "are documentation and are ignored."
)


def sbatch_template_header() -> dict:
    """The self-documenting ``_comment``/``_options`` block for a fresh
    sbatch_options.json. Shared by every processor so the file reads the same
    whichever one wrote it."""
    return {"_comment": SBATCH_OPTIONS_COMMENT,
            "_options": dict(SBATCH_OPTIONS_HELP)}


def _slurm_walltime_seconds(text: str) -> int | None:
    """Seconds for a SLURM walltime string, or None if it isn't a finite time.

    SLURM accepts minutes, minutes:seconds, hours:minutes:seconds, days-hours,
    days-hours:minutes and days-hours:minutes:seconds -- note a bare number
    means *minutes* on its own but *hours* once a ``days-`` prefix is present,
    which is the one genuinely surprising case here.
    """
    t = text.strip()
    if not t or t.lower() in {"infinite", "unlimited", "n/a", "none"}:
        return None
    days = 0
    if "-" in t:
        d, _, t = t.partition("-")
        try:
            days = int(d)
        except ValueError:
            return None
    try:
        parts = [int(p) for p in t.split(":")]
    except ValueError:
        return None
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = (parts[0], parts[1], 0) if days else (0, parts[0], parts[1])
    elif len(parts) == 1:
        h, m, s = (parts[0], 0, 0) if days else (0, parts[0], 0)
    else:
        return None
    return days * 86400 + h * 3600 + m * 60 + s


@lru_cache(maxsize=None)
def partition_max_walltime(partition: str | None = None) -> str | None:
    """The maximum walltime *partition* allows, as a SLURM time string.

    Returns None if it can't be determined or the partition has no limit.
    *partition*=None asks about the cluster's default partition (the one
    ``sinfo`` marks with a trailing ``*``). Cached: this shells out, and every
    stage's manager in a run asks the same question.
    """
    try:
        if partition:
            r = subprocess.run(["sinfo", "-h", "-p", partition, "-o", "%l"],
                               capture_output=True, text=True, timeout=10)
            values = r.stdout.split()
        else:
            r = subprocess.run(["sinfo", "-h", "-o", "%P|%l"],
                               capture_output=True, text=True, timeout=10)
            values = [ln.split("|", 1)[1] for ln in r.stdout.splitlines()
                      if "|" in ln and ln.split("|", 1)[0].strip().endswith("*")]
    except Exception:
        return None

    # sinfo prints one line per node grouping, so the same partition can come
    # back repeated (and, on heterogeneous clusters, occasionally differing) --
    # take the longest finite limit reported.
    best, best_secs = None, -1
    for v in values:
        secs = _slurm_walltime_seconds(v)
        if secs is not None and secs > best_secs:
            best, best_secs = v.strip(), secs
    return best


def manager_walltime(partition: str | None = None) -> str:
    """The walltime every job manager gets: the most its partition allows.

    A manager must outlive every child job it supervises -- it submits them in
    a sliding window and only exits once the last one finishes -- so anything
    short enough to expire mid-run silently strands the remaining work (and,
    with chained managers, every later stage too). There's no cost to asking
    for the maximum: the manager holds a single idle core, and SLURM rejects
    outright any --time above the partition's own MaxTime, so the ceiling is
    the only value that's both always-legal and never too short.
    """
    return partition_max_walltime(partition) or MANAGER_FALLBACK_TIME


def slurm_active_jobs() -> dict[str, str]:
    """Return {job_id: squeue_state} for all of the current user's active jobs
    (R, PD, CG, etc.)."""
    try:
        r = subprocess.run(
            ["squeue", "--noheader", "--format=%i %T", "--me"],
            capture_output=True, text=True, timeout=10,
        )
        result: dict[str, str] = {}
        for line in r.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                result[parts[0]] = parts[1]
        return result
    except Exception:
        return {}


def slurm_job_states(job_ids: list[str]) -> dict[str, str]:
    """Query sacct for the terminal state of specific job IDs.

    Returns {job_id: state} only for jobs that have ended. Job IDs still
    running or not yet started are absent from the result.
    """
    if not job_ids:
        return {}
    try:
        r = subprocess.run(
            ["sacct", "--noheader", "--parsable2", "--format=JobID,State",
             "--jobs=" + ",".join(job_ids)],
            capture_output=True, text=True, timeout=15,
        )
        result: dict[str, str] = {}
        for line in r.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 2:
                continue
            jid = parts[0].strip().split(".")[0]  # strip .batch / array suffixes
            state = parts[1].strip().split(" ")[0]
            # Keep the "worst" state if a job appears multiple times (array steps)
            if jid not in result or state in SLURM_DEAD_STATES:
                result[jid] = state
        return result
    except Exception:
        return {}


def build_cmd_sbatch_script(
    job_name: str, cmd: str, cmd_idx: int, log_dir: Path, slurm_kwargs: dict,
    env_lines: list[str], sbatch_dir: Path | None = None,
    file_prefix: str = "cmd", label: str | None = None,
) -> Path:
    """Generate a single-command sbatch script.

    Logs START/DONE/FAIL with elapsed seconds and writes cmd_<idx>.done /
    cmd_<idx>.fail markers to log_dir on completion.

    job_name: short SLURM --job-name for this child job.
    file_prefix: on-disk filename prefix (e.g. the step/stage name) --
        distinct from job_name, which stays short for `squeue` readability.
    label: text used in the START/DONE/FAIL log lines (defaults to file_prefix).
    """
    label = label or file_prefix
    done_file = log_dir / f"cmd_{cmd_idx:04d}.done"
    fail_file = log_dir / f"cmd_{cmd_idx:04d}.fail"
    log_file  = log_dir / f"cmd_{cmd_idx:04d}.log"

    slurm_cfg = Slurmjob_Config(
        job_name=job_name,
        output_file=str(log_dir / f"{file_prefix}_{cmd_idx:04d}_slurm_%j.out"),
        error_file=str(log_dir / f"{file_prefix}_{cmd_idx:04d}_slurm_%j.err"),
        **slurm_kwargs,
    )

    lines = ["#!/bin/bash"]
    lines += slurm_cfg.to_header_lines()
    lines += ["", *env_lines, ""]
    lines += [
        f'if [[ -f {done_file} ]]; then echo "cmd_{cmd_idx:04d} already done, skipping."; exit 0; fi',
        "",
        f'_t0=$(date +%s)',
        f'echo "[$(date)] START {label} cmd_{cmd_idx:04d}"',
        f'{cmd} > {log_file} 2>&1',
        f'_rc=$?',
        f'_elapsed=$(( $(date +%s) - _t0 ))',
        f'if [[ $_rc -eq 0 ]]; then',
        f'  echo "[$(date)] DONE  {label} cmd_{cmd_idx:04d} elapsed=${{_elapsed}}s"',
        f'  touch {done_file}',
        f'  rm -f {fail_file}',
        f'else',
        f'  echo "[$(date)] FAIL  {label} cmd_{cmd_idx:04d} elapsed=${{_elapsed}}s rc=$_rc"',
        f'  echo $_rc > {fail_file}',
        f'  exit $_rc',
        f'fi',
    ]

    out_dir = sbatch_dir if sbatch_dir is not None else log_dir
    sbatch_script = out_dir / f"{file_prefix}_{cmd_idx:04d}.sbatch"
    sbatch_script.write_text("\n".join(lines) + "\n")
    sbatch_script.chmod(0o755)
    return sbatch_script


def chain_submit_lines(
    next_manager_script: Path | None, next_job_id_file: Path | None,
) -> list[str]:
    """Bash lines appended to a manager script's success path: submit the next
    manager in a chain and record its job id (for refresh() to pick up later).
    Only reached on success, so a failed/cancelled manager halts the chain on
    its own -- no separate cleanup needed for the not-yet-submitted rest."""
    if next_manager_script is None:
        return []
    return [
        "",
        f'_next=$(sbatch "{next_manager_script}" 2>&1)',
        "if [[ $? -eq 0 ]]; then",
        "    _next_jid=$(echo \"$_next\" | grep -oE '[0-9]+' | tail -1)",
        f'    echo "$_next_jid" > "{next_job_id_file}"',
        '    echo "[$(date)] chained -> job $_next_jid"',
        "else",
        f'    echo "[$(date)] chain sbatch FAILED: $_next"',
        "    exit 1",
        "fi",
    ]


_MANAGER_SIZE_FIELDS = ("time", "cpus_per_task", "mem", "ntasks", "nodes")


def build_sliding_window_manager(
    job_name_base: str, commands: list[str], log_dir: Path, sbatch_dir: Path,
    max_concurrent: int, slurm_kwargs: dict, env_lines: list[str],
    write_status_fn: str, manager_time: str | None = None,
    next_manager_script: Path | None = None, next_job_id_file: Path | None = None,
    file_prefix: str = "cmd", label: str | None = None,
    extra_var_lines: list[str] | None = None,
    manager_slurm_kwargs: dict | None = None,
    manager_partition: str | None = None,
) -> Path:
    """Generate N child sbatch scripts + one manager that submits them in a
    sliding window of at most max_concurrent at a time, polling squeue and
    refilling slots as they free up.

    job_name_base: short SLURM job-name stem -- the manager becomes
        f"{job_name_base}_mgr", each child f"{job_name_base}_<idx>".
    slurm_kwargs: resource kwargs for the CHILD jobs (the actual unit of
        work) -- e.g. the per-stage time/cpus_per_task/mem a caller loaded
        from sbatch_options.json. Used as-is for every child script.
    manager_slurm_kwargs: resource kwargs for the lightweight manager job
        itself. The manager always gets its own fixed sizing
        (MANAGER_CPUS_PER_TASK core, MANAGER_MEM) regardless -- if omitted,
        it's derived from slurm_kwargs by dropping the sizing fields
        (time/cpus_per_task/mem/ntasks/nodes), so non-sizing fields shared
        by children (partition, account, qos, ...) still apply to the
        manager too. Passing slurm_kwargs straight through as-is would
        raise "multiple values for keyword argument" against the manager's
        own sizing -- this split is what avoids that while still letting
        children receive their real resource sizing (previously, callers
        stripped sizing out of a single shared dict before calling, which
        fixed the manager but silently starved every child of its intended
        time/cpus/mem too).
    manager_time: manager walltime. Defaults (None) to the maximum its
        partition allows -- see manager_walltime(). sbatch_options.json
        deliberately has no say over a manager's *sizing*: that file
        configures the real processing work (the child jobs), and a manager
        is pure bookkeeping whose only requirement is outliving its children.
    manager_partition: run managers on this partition instead of the one the
        children use (sbatch_options.json's "manager" entry). Worth setting
        when a long-walltime partition exists: managers are idle
        single-core babysitters that must outlive every child, so they suit
        a long/low-priority queue even when the real work belongs on a
        faster one. Also drives the auto-detected walltime above, since the
        ceiling adopted is whichever partition the manager itself lands on.
    write_status_fn: complete bash ``write_status() { ... }`` function
        definition (as one or more lines, may itself declare variables the
        function body needs) -- the manager loop calls
        ``write_status "<value>"`` generically (values used: "RUNNING",
        "SUCCEEDED", "FAILED:<reason>", "FAILED:manager killed"). Each
        processor supplies its own status representation: ISCE2_S1 writes a
        single status text file, GMTSAR_S1 touches/removes marker files in a
        status directory.
    extra_var_lines: additional bash variable-assignment lines inserted right
        after LOG_DIR (before SUBMITTED_FILE) -- e.g. ISCE2_S1's STATUS_FILE.
    """
    label = label or file_prefix
    n_cmds = len(commands)
    extra_var_lines = extra_var_lines or []

    for i, cmd in enumerate(commands):
        build_cmd_sbatch_script(f"{job_name_base}_{i:04d}", cmd, i, log_dir,
                                 slurm_kwargs, env_lines, sbatch_dir, file_prefix, label)

    if manager_slurm_kwargs is None:
        manager_slurm_kwargs = {k: v for k, v in slurm_kwargs.items()
                                 if k not in _MANAGER_SIZE_FIELDS}
    if manager_partition:
        manager_slurm_kwargs = {**manager_slurm_kwargs, "partition": manager_partition}
    if manager_time is None:
        manager_time = manager_walltime(manager_slurm_kwargs.get("partition"))

    mgr_cfg = Slurmjob_Config(
        job_name=f"{job_name_base}_mgr",
        output_file=str(log_dir / "manager_%j.out"),
        error_file=str(log_dir / "manager_%j.err"),
        time=manager_time, ntasks=1, nodes=1,
        cpus_per_task=MANAGER_CPUS_PER_TASK, mem=MANAGER_MEM,
        **manager_slurm_kwargs,
    )

    submitted_file = log_dir / "submitted_child_jobs.txt"
    child_script_entries = [
        f'  "{sbatch_dir}/{file_prefix}_{i:04d}.sbatch"' for i in range(n_cmds)
    ]

    lines = ["#!/bin/bash"]
    lines += mgr_cfg.to_header_lines()
    lines += [
        "",
        "set -o pipefail",
        "",
        f'LOG_DIR="{log_dir}"',
        *extra_var_lines,
        f'SUBMITTED_FILE="{submitted_file}"',
        f'N_CMDS={n_cmds}',
        f'MAX_CONCURRENT={max_concurrent}',
        # Backpressure handling for a QOS/association submit ceiling; see
        # submit_one(). 30s x 240 = up to 2h of waiting for a slot before the
        # stage is declared failed -- long enough to ride out a full queue,
        # short enough that a genuinely stuck limit still surfaces.
        f'SUBMIT_RETRY_SECS={SUBMIT_RETRY_SECS}',
        f'SUBMIT_MAX_RETRIES={SUBMIT_MAX_RETRIES}',
        "",
        "CHILD_SCRIPTS=(",
    ] + child_script_entries + [
        ")",
        "",
        write_status_fn,
        "",
        "trap 'write_status \"FAILED:manager killed\"; exit 1' TERM INT",
        "",
        f'echo "[$(date)] Manager: {n_cmds} commands, max_concurrent={max_concurrent}"',
        'write_status "RUNNING"',
        '> "$SUBMITTED_FILE"',
        "",
        "# sliding-window: build list of pending indices (skip already-done)",
        "PENDING_IDXS=()",
        f"for (( i=0; i<{n_cmds}; i++ )); do",
        "    IDX=$(printf '%04d' $i)",
        '    if [[ -f "$LOG_DIR/cmd_${IDX}.done" ]]; then',
        '        echo "  cmd_${IDX} SKIPPED (already done)"',
        "    else",
        "        PENDING_IDXS+=($i)",
        "    fi",
        "done",
        "",
        "PENDING_PTR=0",
        "FAIL_COUNT=0",
        "declare -A JID_TO_IDX",
        "",
        "submit_one() {",
        "    local raw_idx=${PENDING_IDXS[$PENDING_PTR]}",
        "    local IDX; IDX=$(printf '%04d' $raw_idx)",
        "    local result rc attempt=0",
        "    while : ; do",
        '        result=$(sbatch "${CHILD_SCRIPTS[$raw_idx]}" 2>&1)',
        "        rc=$?",
        "        [[ $rc -eq 0 ]] && break",
        # A QOS/association submit ceiling is BACKPRESSURE, not a failure: the
        # scheduler is saying "you already have as many jobs queued as you are
        # allowed", which resolves by itself as earlier children finish. Dying
        # here abandoned an entire stage mid-run (the whole point of a sliding
        # window is to stay under exactly this kind of cap). Anything else --
        # a bad partition, a nonexistent account, a malformed script -- is a
        # real error and still aborts immediately.
        '        if [[ "$result" == *QOSMaxSubmitJobPerUserLimit* ',
        '           || "$result" == *AssocMaxSubmitJobLimit* ',
        '           || "$result" == *"job submit limit"* ]]; then',
        "            (( attempt++ ))",
        '            if (( attempt == 1 )); then',
        '                echo "  [$(date)] cmd_${IDX}: at the queue submit limit; '
        'waiting for slots (retrying every ${SUBMIT_RETRY_SECS}s)"',
        "            fi",
        '            if (( attempt > SUBMIT_MAX_RETRIES )); then',
        '                echo "  sbatch gave up on cmd_${IDX} after $attempt '
        'attempts: $result"',
        '                write_status "FAILED:submit limit persisted for cmd_${IDX}"',
        "                exit 1",
        "            fi",
        '            sleep "$SUBMIT_RETRY_SECS"',
        "            continue",
        "        fi",
        '        echo "  sbatch FAILED cmd_${IDX}: $result"',
        '        write_status "FAILED:sbatch failed for cmd_${IDX}"',
        "        exit 1",
        "    done",
        "    local JID; JID=$(echo \"$result\" | grep -oE '[0-9]+' | tail -1)",
        '    JID_TO_IDX[$JID]=$raw_idx',
        '    echo "$JID" >> "$SUBMITTED_FILE"',
        '    echo "  [$(date)] cmd_${IDX} -> job $JID"',
        "    (( PENDING_PTR++ ))",
        "}",
        "",
        "# fill initial window",
        "while [[ ${#JID_TO_IDX[@]} -lt MAX_CONCURRENT && $PENDING_PTR -lt ${#PENDING_IDXS[@]} ]]; do",
        "    submit_one",
        "done",
        "",
        "# poll: retire finished jobs, refill slot immediately",
        "while [[ ${#JID_TO_IDX[@]} -gt 0 ]]; do",
        "    sleep 30",
        "    for JID in \"${!JID_TO_IDX[@]}\"; do",
        "        squeue --noheader --jobs=\"$JID\" 2>/dev/null | grep -q . && continue",
        "        raw_idx=${JID_TO_IDX[$JID]}",
        "        IDX=$(printf '%04d' $raw_idx)",
        '        if [[ -f "$LOG_DIR/cmd_${IDX}.done" ]]; then',
        '            echo "  [$(date)] cmd_${IDX} SUCCEEDED"',
        '        elif [[ -f "$LOG_DIR/cmd_${IDX}.fail" ]]; then',
        '            echo "  [$(date)] cmd_${IDX} FAILED"',
        "            FAIL_COUNT=$(( FAIL_COUNT + 1 ))",
        "        else",
        "            # No marker yet -- do NOT conclude failure here. squeue stops",
        "            # reporting a job the instant it leaves the queue, but the",
        "            # child's marker write may not be visible to this manager yet",
        "            # on a shared filesystem (metadata/attribute-cache lag).",
        "            # Observed for real: g_merge_0003 finished COMPLETED (exit 0:0)",
        "            # and left cmd_0003.done, yet the manager declared 'no marker'",
        "            # and failed the whole merge stage, because one false failure",
        "            # stops the FAIL_COUNT-gated refill below.",
        "            # Give the marker a grace period, then fall back to sacct,",
        "            # which is authoritative about how the job actually ended.",
        "            for _try in 1 2 3 4 5 6; do",
        "                sleep 5",
        '                if [[ -f "$LOG_DIR/cmd_${IDX}.done" || -f "$LOG_DIR/cmd_${IDX}.fail" ]]; then break; fi',
        "            done",
        '            if [[ -f "$LOG_DIR/cmd_${IDX}.done" ]]; then',
        '                echo "  [$(date)] cmd_${IDX} SUCCEEDED (marker appeared late)"',
        '            elif [[ -f "$LOG_DIR/cmd_${IDX}.fail" ]]; then',
        '                echo "  [$(date)] cmd_${IDX} FAILED"',
        "                FAIL_COUNT=$(( FAIL_COUNT + 1 ))",
        "            else",
        "                _ST=$(sacct -j \"$JID\" --noheader --format=State%20 2>/dev/null | head -1 | awk '{print $1}')",
        '                if [[ "$_ST" == "COMPLETED" ]]; then',
        '                    echo "  [$(date)] cmd_${IDX} SUCCEEDED (no marker; sacct=COMPLETED)"',
        '                    : > "$LOG_DIR/cmd_${IDX}.done"',
        "                else",
        '                    echo "WARNING: cmd_${IDX} no marker, sacct=${_ST:-unknown} — marking failed"',
        '                    echo "${_ST:-unknown}" > "$LOG_DIR/cmd_${IDX}.fail"',
        "                    FAIL_COUNT=$(( FAIL_COUNT + 1 ))",
        "                fi",
        "            fi",
        "        fi",
        "        unset 'JID_TO_IDX[$JID]'",
        "        if [[ $FAIL_COUNT -eq 0 && $PENDING_PTR -lt ${#PENDING_IDXS[@]} ]]; then",
        "            submit_one",
        "        fi",
        "    done",
        "    echo \"  [$(date)] running=${#JID_TO_IDX[@]} pending=$(( ${#PENDING_IDXS[@]} - PENDING_PTR ))\"",
        "done",
        "",
        "if [[ $FAIL_COUNT -gt 0 ]]; then",
        '    write_status "FAILED:${FAIL_COUNT} command(s) failed"',
        "    exit 1",
        "fi",
        'write_status "SUCCEEDED"',
        f'echo "[$(date)] {label} completed."',
    ]
    lines += chain_submit_lines(next_manager_script, next_job_id_file)
    lines += ["exit 0"]

    manager_script = sbatch_dir / "manager.sbatch"
    manager_script.write_text("\n".join(lines) + "\n")
    manager_script.chmod(0o755)
    return manager_script
