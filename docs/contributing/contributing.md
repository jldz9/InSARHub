# Contributing

InSARHub welcomes contributions of all kinds — bug fixes, new processors/analyzers, documentation improvements, and frontend enhancements.


## Quick Setup

```bash
git clone https://github.com/your-username/InSARHub.git
cd InSARHub
conda env create -f environment.yml
conda activate insarhub
pip install -e ".[dev]"
```

## Project Layout

```
InSARHub/
├── src/insarhub/
│   ├── analyzer/        # Analyzers source code
│   ├── app/
│   │   ├── frontend/    # React + Vite web UI (TypeScript)
│   │   └── routes/      # FastAPI route handlers
│   ├── cli/             # Command-line interface
│   ├── commands/        # Command objects shared by CLI and GUI
│   ├── config/
│   │   ├── defaultconfig.py  # Dataclass configs for every module
│   │   └── paths.py          # Centralized workdir path layout
│   ├── core/            # Base classes, registry, engine
│   ├── downloader/      # Downloader source code
│   ├── processor/       # Processors source code
│   └── utils/           # Shared utilities
├── docs/                # MkDocs documentation source
└── mkdocs.yml
```

## Submitting Changes

1. Create a feature branch: `git checkout -b feat/my-feature`
2. Keep commits focused — one logical change per commit.
3. Update `CHANGELOG.md` under `[Unreleased]`.
4. Open a pull request against `main` describing what changed and why.

## Reporting Bugs

Open an issue at <https://github.com/jldz9/InSARHub/issues> with:

- InSARHub version (`insarhub --version`)
- OS and Python version
- Minimal reproduction steps
- Relevant log output or error traceback

---

See [Backend](backend.md) for Python/API contribution details and [Frontend](frontend.md) for the React UI.
