"""One place that decides how much InSARHub logs.

Release behaviour: InSARHub's own INFO and DEBUG records are suppressed, while
WARNING and above still reach the terminal. Ordinary ``print()`` output is
untouched -- that is the program's actual output, not logging. Set
``INSARHUB_DEBUG=1`` to turn every InSARHub log record back on.

Why an environment variable rather than a CLI flag: the CLI is only one of three
entry points. ``insarhub-app`` (the GUI) and plain ``import insarhub`` have no
argv to read, and those are precisely the cases where a user currently cannot
get a log line out of the program at all. One switch covers all three.

Two things this deliberately does NOT do:

* **No ``logging.disable()``.** See the note in ``insarhub/__init__.py``: it is
  process-global and cannot be undone by a caller, so importing insarhub would
  silence a host application's own loggers too. Whole commands looked like
  no-ops because of it.
* **The root logger stays at WARNING even in debug mode.** Raising the root is
  what turns a debug session into a wall of matplotlib, botocore, rasterio and
  asyncio chatter. Only the ``insarhub`` logger is lowered, so DEBUG shows
  *our* records. A record that passes its own logger's level is emitted by the
  root's handlers regardless of the root logger's level, so this works.
"""

from __future__ import annotations

import logging
import os

ENV_VAR = "INSARHUB_DEBUG"
LOGGER_NAME = "insarhub"

_RELEASE_FORMAT = "[%(levelname)s] %(message)s"
_DEBUG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Everything except these counts as "on", so INSARHUB_DEBUG=1, =true and =yes
# all work, and =0 / =false / empty mean off.
_FALSEY = {"", "0", "false", "no", "off"}


def debug_enabled() -> bool:
    """True when INSARHUB_DEBUG asks for full logging."""
    return os.environ.get(ENV_VAR, "").strip().lower() not in _FALSEY


def level() -> int:
    """The level the ``insarhub`` logger should run at."""
    return logging.DEBUG if debug_enabled() else logging.WARNING


def configure(install_handler: bool | None = None) -> int:
    """Apply the logging policy; return the level given to ``insarhub``.

    ``install_handler`` decides whether to attach a handler to the root logger:

    * ``True``  -- for the CLI and the GUI, which own the process and are
      expected to print. Without it, WARNING and above would fall through to
      Python's last-resort handler with no formatting.
    * ``False`` -- never touch the root logger.
    * ``None`` (default) -- install one only in debug mode. This is what
      ``import insarhub`` uses: a library must not hijack the host
      application's logging, but with no handler at all a debug session would
      produce nothing, since the last-resort handler drops anything below
      WARNING.

    ``basicConfig`` is a no-op once the root has a handler, so an embedding
    application's own configuration still wins.
    """
    if install_handler is None:
        install_handler = debug_enabled()

    lvl = level()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(lvl)

    # Deliberately NO NullHandler. The usual library idiom attaches one so that
    # unconfigured hosts see nothing -- but that also satisfies logging's
    # handler lookup, which stops `logging.lastResort` from firing, and
    # lastResort is exactly what carries WARNING and ERROR to stderr when no
    # handler is installed. Adding it silently swallowed every insarhub warning
    # and error in plain library use.

    if install_handler:
        logging.basicConfig(
            level=logging.WARNING,
            format=_DEBUG_FORMAT if debug_enabled() else _RELEASE_FORMAT,
        )
    return lvl
