# Contributing to InSARHub

Thank you for your interest in contributing to InSARHub.

## How to contribute

### Bug reports and feature requests

Open an issue on [GitHub](https://github.com/jldz9/InSARHub/issues). For bugs,
include the InSARHub version, Python version, operating system, and a minimal
reproducible example.

### Pull requests

1. Fork the repository and create a branch from `main`.
2. Install the development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
3. Make your changes and add tests under `test/`.
4. Run the test suite:
   ```bash
   pytest test/ -v
   ```
5. Open a pull request against `main` with a clear description of the change
   and any related issue numbers.

### Adding a new processor or downloader backend

InSARHub uses a plugin registry pattern. To add a new backend:

1. Subclass `Downloader`, `Processor`, or `Analyzer` from `insarhub.base`.
2. Implement all abstract methods defined in the base class.
3. Register the class by calling `registry.register("YourClass", YourClass)` at
   module level so it is picked up on import.
4. Add a corresponding config dataclass to `insarhub/config.py`.
5. Write unit tests that mock any external APIs or heavy dependencies.

## Code style

- Format with [Black](https://black.readthedocs.io/) (`black .`).
- Type hints encouraged but not required.
- Keep docstrings short; prefer self-documenting names.

## Licensing

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
