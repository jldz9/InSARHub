#!/usr/bin/env bash
# Runs once, after the container is created, from the workspace root.
#
# The image already has the conda env (environment.yml) and the test tooling.
# What is left is everything that needs the MOUNTED WORKSPACE, which does not
# exist at image-build time: the editable install and the frontend.
set -euo pipefail

ENV_PY="/opt/conda/envs/${CONDA_ENV:-insarhub}/bin/python"
FRONTEND="src/insarhub/app/frontend"

echo "==> Installing InSARHub (editable)"
# --no-deps on purpose: environment.yml already resolved the full dependency
# tree through conda, and letting pip re-resolve pyproject's `dependencies`
# would pull PyPI wheels of rasterio/burst2safe over the conda builds. Those
# wheels bundle their own libproj against conda's older proj.db and break at
# runtime with "PROJ: ... DATABASE.LAYOUT.VERSION.MINOR" mismatches -- the trap
# environment.yml's own comments document. Same reasoning as
# docker/dev/Dockerfile.base.
"$ENV_PY" -m pip install --no-cache-dir --no-deps -e .

echo "==> Installing frontend dependencies"
# npm ci, not npm install: package-lock.json is committed and CI uses ci too, so
# this reproduces the exact tree .github/workflows/test.yml builds.
( cd "$FRONTEND" && npm ci )

echo "==> Building frontend"
# insarhub-app serves src/insarhub/app/frontend/dist; without this the GUI comes
# up with no page to serve. Re-run it after frontend edits, or use `npm run dev`
# on port 5173 for hot reload.
( cd "$FRONTEND" && npm run build )

echo
echo "Ready. Environment: ${CONDA_ENV:-insarhub}"
"$ENV_PY" -c "import insarhub; print('insarhub', insarhub.__version__, '->', insarhub.__file__)"
echo
echo "  pytest -m 'basic or regression'   # fast, hermetic -- what gates every PR"
echo "  insarhub --help"
echo "  insarhub-app                      # GUI on :8080"
echo "  (cd $FRONTEND && npm run dev)     # frontend hot reload on :5173"
