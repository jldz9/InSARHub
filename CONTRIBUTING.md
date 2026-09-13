# Contributing to InSARHub

Thank you for your interest in contributing to InSARHub.

The full guides live in the documentation — this file is the short version:

- [Contributing overview](docs/contributing/contributing.md)
- [Backend guide](docs/contributing/backend.md) — architecture, registry, path conventions
- [Frontend guide](docs/contributing/frontend.md) — the React GUI

They are also published at
<https://jldz9.github.io/InSARHub/latest/contributing/backend/> — note the
`latest/` segment, which the docs site needs because it is versioned with mike.

## How to contribute

### Bug reports and feature requests

Open an issue on [GitHub](https://github.com/jldz9/InSARHub/issues). For bugs,
include the output of `insarhub --version`, your Python version and operating
system, how InSARHub was installed (conda-forge, pip, container, source), and a
minimal reproducible example.

## Development setup

### In a container (least setup)

Open the repo in VS Code and choose **Reopen in Container**. `.devcontainer/`
builds the conda environment from `environment.yml`, installs Node, and runs the
editable install and frontend build for you.

### Locally

```bash
conda env create -f environment.yml
conda activate insarhub
pip install -e '.[test]'
```

`environment.yml` is the single definition of the development environment, and
the pins in it are load-bearing — read the comments before changing any of them.
GDAL, rasterio and burst2safe must come from conda, not pip: a pip wheel bundles
its own libproj against conda's older `proj.db` and fails at runtime with
`PROJ: ... DATABASE.LAYOUT.VERSION.MINOR` mismatches.

The GUI is a React app that is built into `src/insarhub/app/frontend/dist` and
served from there by `insarhub-app`. It is not built by the Python install:

```bash
cd src/insarhub/app/frontend
npm ci          # not `npm install` -- package-lock.json is committed
npm run build   # or `npm run dev` for hot reload on :5173
```

To add a SAR backend (ISCE2, ISCE3/dolphin, GMTSAR) to the environment, follow
[docs/quickstart/install.md](docs/quickstart/install.md); each backend is
installed into the same env, and GMTSAR builds its own.

## Tests

Four tiers, documented in [test/README.md](test/README.md).

```bash
pytest                            # tiers 1, 2 and 4 -- the everyday loop
pytest -m "basic or regression"   # what gates every PR, ~20s
pytest -m e2e                     # tier 3: real data, real backends, opt-in
```

Tier 3 is excluded from a bare `pytest` on purpose — it downloads real
Sentinel-1 data and runs real processing.

### Pull requests

1. Fork the repository and create a branch from `main`.
2. Make your changes and add tests under `test/`: a behaviour change belongs in
   `test/tier2_basic/`, a bug fix in `test/tier4_regression/` as one case named
   for the bug.
3. Run `pytest -m "basic or regression"`.
4. Open a pull request against `main` with a clear description of the change and
   any related issue numbers.

## Adding a new processor, analyzer or downloader

The long version, with the class hierarchy and the shared infrastructure each
base class already gives you, is in
[docs/contributing/backend.md](docs/contributing/backend.md). The mechanics:

1. Subclass the right base from `insarhub.core.base` — `BaseDownloader`,
   `LocalProcessor` (runs a local toolchain), `CloudProcessor` (submits to a
   service) or `BaseAnalyzer` — and implement its abstract methods.
2. Set a `name` class attribute. **Registration is automatic**: each base class's
   `__init_subclass__` registers any subclass that has a `name`, so there is no
   `register()` call to write. Add an `aliases` tuple if the class was renamed,
   so saved `insarhub_config.json` files keep resolving.
3. Add a config dataclass to `src/insarhub/config/defaultconfig.py`, export it
   from `src/insarhub/config/__init__.py`, and point the class's
   `default_config` at it.
4. Import your module in the subpackage's `__init__.py`
   (`insarhub/downloader/__init__.py`, `processor/`, `analyzer/`) — a class that
   is never imported is never registered.
5. Build paths from the dataclasses in `src/insarhub/config/paths.py`. Never
   hardcode `workdir / "hyp3"`.
6. Write tests that mock external APIs and heavy dependencies; tier 2 stubs the
   geo stack, so nothing there may need it at import time.

## Code style

- Match the surrounding code. No formatter is enforced — there is no Black, Ruff
  or pre-commit configuration in the repo and no lint job in CI, and running a
  formatter across the tree would bury your change in reflowed lines.
- Comments explain *why*, not *what*. Where a pin, an ordering or a workaround is
  load-bearing, say what breaks without it — that convention is why
  `environment.yml`, `docker/README.md` and the CI workflow read the way they do.
- Type hints are encouraged but not required.
- Frontend: `npm run lint` (ESLint is configured; `eslint.config.js`).

## Licensing

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
