Every local processor can run its work as SLURM jobs instead of inside the
process that called `submit()`. Pass `--hpc-mode`; nothing else about the
workflow changes.

This matters for more than speed. A local run lives in a background thread of
whatever process invoked it, so it dies when that process does — a lost SSH
session takes hours of geocoding with it. In HPC mode the work belongs to the
scheduler.

## The manager model

Submitting does **not** queue every job at once. Each unit of work becomes one
child `sbatch` script, and a single lightweight **manager** job supervises them
in a sliding window:

```
manager (1 core, 1 GB, longest walltime its partition allows)
  ├── child 0   ┐
  ├── child 1   ├── at most --worker alive at a time
  ├── child 2   ┘
  └── …         refilled as slots free
```

The manager polls `squeue` and submits the next child whenever one finishes. It
writes the stage's status marker at the end, so `refresh` reports HPC and local
runs through exactly one mechanism.

Where a processor has stages that must run in order, each stage's manager
**chain-submits the next from inside its own success path** rather than all of
them being queued up front with `--dependency=afterok`. Pre-submitting leaves
every not-yet-running manager sitting in the queue as its own job, counting
against the account's submitted-jobs limit while doing nothing.

## `--worker`

One flag, both axes:

```bash
insarhub processor -N ISCE3_Burst -w . --hpc-mode submit --worker 8
```

- stages that fan out → **8 concurrent SLURM jobs**
- stages that cannot → **8 threads inside the single job**

The second case is real, not hypothetical: ISCE3_Burst's `ifg` under
`phase_link` is one process, because the estimator consumes the full N×N
covariance across every date and has no per-pair unit to split. Setting only the
job count would leave it running on its saved thread count while SLURM reserved
cores it never used.

Override either individually when they should differ:

```bash
--worker 8 --max_workers 2      # 8 concurrent jobs, 2 threads each
```

`submit` warns when a single-job stage's `cpus_per_task` disagrees with
`max_workers`, naming how many cores are idle or oversubscribed.

### The queue limit

Clusters cap how many jobs one user may have **submitted**, separately from how
many may run. `submit` reads that cap from the partition's QOS and clamps
`--worker` to `limit - 2`, printing a yellow warning:

```
--worker 40 exceeds what partition 'all' allows: its QOS 'jobmanagement' caps
you at 20 SUBMITTED jobs. Reducing to 18 (leaving one slot for the manager and
one for the chain handover).
```

Two slots are reserved rather than one: the manager holds one, and during a
chain handover the finishing manager and the one it submits both briefly exist.

The cap counts **every** job you have queued, not just this run — lower
`--worker` further if you have others pending. If a submit does hit the ceiling
anyway, the manager treats it as backpressure and retries every 30 s for up to
two hours rather than abandoning the stage.

## `sbatch_options.json`

Written to the workdir on first submit, which then **stops so you can review
it**. It documents itself: a `_options` block lists every settable key with what
it does and an example, and `_stages` (or `_steps`) names each unit and says how
many jobs it becomes.

```json
{
  "default": { "time": "02:00:00", "cpus_per_task": 4, "mem": "16G",
               "partition": "all" },
  "manager": { "partition": "all" },
  "cslc":    { "time": "04:00:00", "cpus_per_task": 4, "mem": "32G" }
}
```

`default` is a **base every stage inherits**, not a fallback used only when a
stage is missing. Cluster-wide settings — `partition`, `account`, `qos` — belong
there once; each stage overrides only the time/cpus/mem it actually needs.

`manager` is special: only `partition` is read. A manager's cores, memory and
walltime are fixed, because it is pure bookkeeping whose one requirement is
outliving every child it supervises. Give it the longest-walltime partition
available if your stages are long or numerous.

Keys beginning with `_` are documentation and are ignored. Any key that is not a
recognised SLURM field is silently dropped rather than reaching `sbatch`, so a
typo fails quietly — check `_options` for the accepted set.

## How each processor decomposes

Granularity follows where a unit is a **separate process**. Work that is already
thread-parallel inside one process gets one job sized to match.

=== "ISCE3_Burst"

    | stage | jobs | unit |
    |---|---|---|
    | `dem`, `tec` | 1 each | network-bound, minutes |
    | `cslc` | **one per burst-date** | each is its own COMPASS process — the long pole |
    | `static` | one per burst, + 1 reduce | `s1_static_layers.py`, then an LOS mosaic |
    | `crop` | one per burst | |
    | `ifg` | **1** | phase linking needs the whole covariance; no per-pair unit exists |
    | `stitch`, `filt` | **one per pair** | |
    | `unwrap` | 1 prep + **one per pair** | the prep builds the water mask once, before N readers race on it |

    A stage whose job list comes from `ifg_manifest.json` cannot be enumerated
    until `ifg` has run. Submitting the whole chain is fine — those stages are
    reported as deferred, with the exact command to run once their inputs exist.

=== "ISCE3_NISAR"

    NISAR GSLC is already geocoded, so the COMPASS stages (`dem`, `tec`, `cslc`,
    `static`) are dropped — only three stages run:

    | stage | jobs | unit |
    |---|---|---|
    | `crop` | **one per GSLC date** | cut each frame to the AOI (a cheap VRT `gdal_translate`) |
    | `ifg` | **1** | phase linking needs the whole covariance; no per-pair unit (a single `wrapped_phase.run` over the stack) |
    | `stitch` | **one per pair** | |
    | `unwrap` | 1 prep + **one per pair** | the prep builds the water mask once, before N readers race on it |

    Same deferral rule as `ISCE3_Burst`: per-pair stages are enumerated from
    `ifg_manifest.json` and reported as deferred until `ifg` has run.

=== "GMTSAR_S1"

    **p2p mode** (`stack_mode=False`, the default) is one job per pair. Pairs
    are fully independent — multi-subswath gives each its own case directory,
    and single-subswath output is namespaced `intf/<julian_pair>/` — so a single
    manager fans every pair out with no chaining at all.

    Each job runs that pair's whole chain: align → interferogram → filter →
    unwrap → geocode.

    **stack_mode** runs ordered stages instead: `align → topo → intf →
    mergeprep → merge → cohmask → unwrap`, each one manager, chain-submitted.

=== "ISCE2_S1"

    `stackSentinel.py` writes `run_NN_*` files containing one shell command per
    line; each line becomes a child job. Consecutive steps with equal command
    counts are fused into a group manager.

## Monitoring

```bash
insarhub processor -N <name> -w . refresh          # coloured status table
insarhub processor -N <name> -w . refresh --ls     # per-unit detail
insarhub processor -N <name> -w . cancel           # scancel manager + children
```

`refresh` reports **the workdir**, not the last submission. Progress counts
products on disk, so a stage completed across five resumes reads `192/192`
rather than the 19 that the most recent run happened to fix. Live SLURM state
overrides the status marker: a marker is written once at the end and cannot say
"in progress", and one left by a cancelled run is stale the moment you
resubmit.

`--ls` lists real identifiers — `20221202_t056_118971_iw2`, or a pair's dates —
so a missing unit can be acted on directly.

!!! note "`--config` and action names"
    `--config` takes an optional value, so never put an action immediately after
    it: `--config cancel` consumes `cancel` as the config path. Write
    `-w . cancel`, or `--config --hpc-mode submit` where a flag follows.

## Dry runs

`--dry-run` prints the plan and changes nothing — no jobs, no scripts, no
config:

```
[ISCE3_Burst] HPC plan (8 concurrent job(s) max):
    cslc                      192 job(s)
[ISCE3_Burst] dry run -- nothing submitted, nothing written.
```

That guarantee is load-bearing. Generated manager scripts encode which stage
they chain to next, so a dry run over a *subset* of stages that rewrote them
would silently break a running pipeline's chain. And a dry run that persisted
config would leave whatever flag you were trialling in place for every later
run.

## Resuming

Stages already `SUCCEEDED` are skipped. Stages that support partial resume — 
`cslc`, and anything else with expensive units — filter finished work out of the
**command list** itself, so a resubmission queues only what is missing:

```
[ISCE3_Burst] cslc: 173 already geocoded, 19 to submit
```

Completion markers are cleared on every submission of a stage, because reaching
that point already means the stage is going to run. They are keyed by position
(`cmd_0007.done`) while the command list is re-derived each time, so a marker
kept across a changed list would refer to entirely different work — that is how
a resumed stage can skip real jobs and still report success.
