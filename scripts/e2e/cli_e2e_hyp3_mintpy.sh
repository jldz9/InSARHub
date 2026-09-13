#!/usr/bin/env bash
# Real end-to-end pipeline: HyP3 -> MintPy, against real infrastructure.
#
# Runs the actual insarhub CLI with no mocking of any kind:
#   - searches ASF and selects real interferogram pairs for path 100 / frame
#     466 over the real p100_f466 AOI/date range
#   - submits real InSAR_GAMMA jobs to HyP3 (consumes real processing
#     credits on your Earthdata account)
#   - polls HyP3 for real job status and downloads the real result ZIPs
#   - runs Hyp3_Mintpy_SBAS.prep_data() for real (real rasterio clip/overlap)
#   - runs the real MintPy time-series workflow
#
# By default this operates on p100_f466/ (repo root). If a stack_*.json is
# already there (e.g. from a prior run of this script), the search/select-pairs
# stage is skipped so the existing real pair selection is reused as-is.
#
# Prerequisites
# -------------
#   - ~/.netrc with a 'machine urs.earthdata.nasa.gov' entry (or
#     ~/.credit_pool for multi-account credit rotation) —
#     see docs/quickstart/install.md
#   - insarhub installed in the active environment (MintPy is a base
#     dependency): pip install insarhub   (or: conda activate dev, already
#     set up in this repo's dev environment)
#   - (optional) ~/.cdsapirc with a CDS API token — the default MintPy config
#     uses troposphericDelay_method=pyaps, which authorizes against CDS during
#     the correct_troposphere step; without ~/.cdsapirc you'll be prompted
#     interactively the first time.
#
# Usage (run from the repo root, so the [workdir] default resolves correctly)
# -----
#   bash test/e2e/cli_e2e_hyp3_mintpy.sh [workdir]
#
# Safe to re-run: search/select-pairs is skipped if a stack_*.json already
# exists; job submission is skipped if hyp3_jobs.json already exists (so
# re-running this script never duplicates real jobs/credits); HyP3 download
# skips files that already exist and are valid ZIPs.

set -euo pipefail

# Anchor the default workdir to the real repo-root p100_f466/ regardless of
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
WORKDIR="${1:-$REPO_ROOT/p100_f466}"

# Real search AOI / date range / track for p100_f466 (path 100, frame 466).
AOI="POLYGON ((-113.21199 37.66462, -112.47240 37.66462, -112.47240 38.15669, -113.21199 38.15669, -113.21199 37.66462))"
START="2021-01-08"
END="2021-06-01"

mkdir -p "$WORKDIR"

echo "== Stage 1/4: Search ASF + select real pairs =================================="
if compgen -G "$WORKDIR/stack_*.json" > /dev/null; then
  echo "  $WORKDIR already has a stack_*.json — skipping search/select-pairs."
else
  insarhub downloader -N S1_SLC -w "$WORKDIR" \
    --AOI "$AOI" \
    --start "$START" --end "$END" \
    --stacks 100:466 \
    --select-pairs
fi

echo "== Stage 2/4: Submit real HyP3 jobs ==========================================="
if [[ -f "$WORKDIR/hyp3_jobs.json" ]]; then
  echo "  $WORKDIR/hyp3_jobs.json already exists — skipping submit"
  echo "  (re-submitting would create duplicate real jobs and spend more credits)."
else
  insarhub processor -N Hyp3_S1 -w "$WORKDIR" submit
fi

echo "== Stage 3/4: Watch real jobs to completion + download real results =========="
# Polls every 5 min (default) until every job is SUCCEEDED/FAILED, downloading
# each job's real output ZIP as it finishes. This runs for as long as ASF
# takes to process all submitted jobs (commonly tens of minutes to a few
# hours) — safe to Ctrl+C and re-run this script later; already-downloaded
# ZIPs are skipped and remaining jobs are refreshed from where they left off.
insarhub processor -N Hyp3_S1 -w "$WORKDIR" watch

echo "== Stage 4/4: Hyp3_Mintpy_SBAS prep_data + real MintPy run =========================="
insarhub analyzer -N Hyp3_Mintpy_SBAS -w "$WORKDIR" run --step prep_data
insarhub analyzer -N Hyp3_Mintpy_SBAS -w "$WORKDIR" run

echo "Done. MintPy outputs are under $WORKDIR/mintpy/"
