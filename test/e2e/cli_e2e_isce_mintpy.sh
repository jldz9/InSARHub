#!/usr/bin/env bash
# Real end-to-end pipeline: ISCE2/topsStack -> MintPy, against real infrastructure.
#
# Runs the actual insarhub CLI with no mocking of any kind:
#   - downloads real Sentinel-1 SLC scenes (~4GB each)
#   - runs real ISCE2 stackSentinel processing for every real selected pair
#   - runs ISCE_SBAS.prep_data() for real (real path discovery)
#   - runs the real MintPy time-series workflow
#
# By default this operates on p100_f466_isce/ (repo root) — a real S1 SLC
# stack (path 100 / frame 466), kept separate from cli_e2e_hyp3_mintpy.sh's
# default p100_f466/ workdir: Hyp3_SBAS and ISCE_SBAS derive their MintPy
# output directory purely from workdir (workdir/mintpy/, with no
# analyzer-type awareness — see MintPyPaths in config/paths.py), so running
# both e2e tests against the same workdir would silently overwrite one
# pipeline's mintpy/ outputs (ifgramStack.h5, smallbaselineApp.cfg,
# velocity.h5, etc.) with the other's.
#
# Prerequisites
# -------------
#   - ~/.netrc (or ~/.credit_pool) with Earthdata credentials.
#   - A real ISCE2 + topsStack install. Build the base env from this repo's
#     environment.yml, then add ISCE2 into the same env:
#       mamba env create -f environment.yml -n insarhub_isce
#       conda activate insarhub_isce
#       mamba install -c conda-forge "numpy<2.0" isce2
#       pip install -e .
#   - Disk space: ~50GB+ for the 13 real SLCs, plus processing intermediates
#     (unpacked bursts, coregistered stacks, interferograms) — check with
#     `df -h` before starting; this can require several hundred GB for a
#     27-pair stack.
#   - Time: real ISCE2 stackSentinel processing of 27 pairs on a single
#     (non-HPC) machine can take many hours to multiple days. Pass
#     --hpc-mode (after configuring sbatch_options.json — see
#     `insarhub processor -N ISCE2_S1 --hpc-mode -w <workdir> submit` docs)
#     if a SLURM cluster is available instead.
#   - (optional) ~/.cdsapirc — see cli_e2e_hyp3_mintpy.sh for why.
#
# Usage (run from the repo root, so the [workdir] default resolves correctly)
# -----
#   conda activate insarhub_isce
#   bash test/e2e/cli_e2e_isce_mintpy.sh [workdir]
#
# Safe to re-run: SLC download skips files that already exist and match the
# expected size; ISCE2_S1.submit() skips any run-file step already marked
# SUCCEEDED (skip_existing, on by default) — so re-running this script after
# a partial/interrupted run resumes rather than restarting from scratch.

set -euo pipefail

# Anchor the default workdir to the real repo-root p100_f466_isce/ regardless of
# CWD or how this script was invoked (an explicit $1 still overrides it and
# is resolved relative to the caller's CWD, as usual). git rev-parse is used
# rather than ${BASH_SOURCE[0]}, which is empty (and silently resolves
# relative to CWD instead) when this file is piped into bash or run via
# `bash -c` rather than executed directly.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [[ -z "$REPO_ROOT" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi
WORKDIR="${1:-$REPO_ROOT/p100_f466_isce}"

if [[ ! -f "$WORKDIR/insarhub_config.json" ]]; then
  echo "[ERROR] $WORKDIR/insarhub_config.json not found." >&2
  echo "        Run search + --select-pairs first to produce a stack_*.json here." >&2
  exit 1
fi

if ! python -c "import isce" >/dev/null 2>&1; then
  echo "[ERROR] Real ISCE2 is not importable in the active Python environment." >&2
  echo "        conda activate insarhub_isce" >&2
  echo "        (build it first: mamba env create -f environment.yml -n insarhub_isce && mamba install -n insarhub_isce -c conda-forge \"numpy<2.0\" isce2)" >&2
  exit 1
fi

echo "== Stage 1/4: Download real Sentinel-1 SLC scenes ============================"
insarhub downloader -N S1_SLC -w "$WORKDIR" --config -d -O --worker 8

echo "== Stage 2/4: Submit real ISCE2_S1 processing (runs detached in background) ===="
insarhub processor -N ISCE2_S1 -w "$WORKDIR" submit

echo "== Stage 3/4: Watch real ISCE2 processing to completion ======================"
# Polls run-file .status files until every step is SUCCEEDED/FAILED. This can
# run for hours; safe to Ctrl+C and re-run this script later — submit()
# resumes from the last completed step, watch() just re-attaches to the
# on-disk state.
insarhub processor -N ISCE2_S1 -w "$WORKDIR" watch

echo "== Stage 4/4: ISCE_SBAS prep_data + real MintPy run ==========================="
insarhub analyzer -N ISCE_SBAS -w "$WORKDIR" run --step prep_data
insarhub analyzer -N ISCE_SBAS -w "$WORKDIR" run

echo "Done. MintPy outputs are under $WORKDIR/mintpy/"
