"""Tier 2 -- the CLI does what it promises, without touching real data.

cli/main.py is ~1300 statements of argument parsing and dispatch and was at 0%
coverage under the old suite, despite being one of the two shipped entry points
and the place the 0.4.0 `--stacks PATH:FRAME` bug lived.

Scope here is the *contract*: every subcommand exists, parses its documented
flags, dispatches to the right place, and fails loudly rather than silently.
Anything that needs a real backend belongs in tier 3.
"""

from __future__ import annotations

import argparse

import pytest

from insarhub.cli.main import create_parser


# ── The command tree ─────────────────────────────────────────────────────────

def _walk(parser, prefix=()):
    """Yield (path, parser) for every leaf subcommand."""
    subparsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subparsers:
        yield prefix, parser
        return
    for action in subparsers:
        for name, sub in action.choices.items():
            yield from _walk(sub, prefix + (name,))


LEAVES = list(_walk(create_parser()))
LEAF_IDS = [" ".join(p) for p, _ in LEAVES]

# The commands the docs and README promise. Pinned explicitly so that deleting
# or renaming one is a test failure rather than a silent removal.
EXPECTED_COMMANDS = {
    "downloader",
    "processor submit", "processor refresh", "processor download",
    "processor retry", "processor watch", "processor credits",
    "processor cancel", "processor run-stage-unit",
    "analyzer run", "analyzer cleanup",
    "utils clip", "utils h5-to-raster", "utils save-footprint",
    "utils slurm", "utils plot-network", "utils era5-download",
}


def test_command_tree_matches_the_documented_surface():
    assert set(LEAF_IDS) == EXPECTED_COMMANDS, (
        "CLI surface changed. Added: "
        f"{sorted(set(LEAF_IDS) - EXPECTED_COMMANDS)}; "
        f"removed: {sorted(EXPECTED_COMMANDS - set(LEAF_IDS))}. "
        "Update EXPECTED_COMMANDS and the docs together."
    )


@pytest.mark.parametrize("path,parser", LEAVES, ids=LEAF_IDS)
def test_every_subcommand_has_help(path, parser):
    """`--help` must work on every leaf without importing a backend.

    This is the one command a user runs when nothing else works, so it must not
    depend on ISCE2/GMTSAR being installed.
    """
    text = parser.format_help()
    assert text.strip()
    assert "usage:" in text.lower()


@pytest.mark.parametrize("path,parser", LEAVES, ids=LEAF_IDS)
def test_no_duplicate_flags(path, parser):
    """A flag defined twice silently shadows the first definition."""
    seen, dupes = set(), []
    for action in parser._actions:
        for opt in action.option_strings:
            if opt in seen:
                dupes.append(opt)
            seen.add(opt)
    assert not dupes, f"`insarhub {' '.join(path)}` defines {dupes} more than once"


# ── Top-level behaviour ──────────────────────────────────────────────────────

def test_version_flag(cli):
    import insarhub

    code, out, _ = cli("--version")
    assert code == 0
    assert insarhub.__version__ in out


def test_help_exits_zero(cli):
    code, out, _ = cli("--help")
    assert code == 0
    assert "downloader" in out


def test_no_arguments_does_not_crash(cli):
    """Bare `insarhub` should print usage, not raise."""
    code, out, err = cli()
    assert "usage" in (out + err).lower()


def test_unknown_command_exits_nonzero(cli):
    code, _, _ = cli("definitely-not-a-command")
    assert code != 0


@pytest.mark.parametrize("path", sorted(EXPECTED_COMMANDS))
def test_subcommand_help_exits_zero(cli, path):
    code, out, _ = cli(*path.split(), "--help")
    assert code == 0, f"`insarhub {path} --help` exited {code}"
    assert "usage:" in out.lower()


# ── Dispatch ─────────────────────────────────────────────────────────────────

def test_verbose_counts_after_the_subcommand():
    """`insarhub downloader --verbose --verbose` raises the level to DEBUG."""
    parser = create_parser()
    args, _ = parser.parse_known_args(["downloader", "--verbose", "--verbose"])
    assert args.verbose == 2


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: `insarhub --verbose downloader` silently yields verbose=0. "
        "Every subparser re-declares --verbose with action='count', default=0 "
        "(cli/main.py:79 and :100 and siblings), and the subparser's default "
        "overwrites the value the top-level parser already counted. So the "
        "top-level --verbose that `insarhub --help` advertises does nothing "
        "unless it is typed after the subcommand. Fix: drop the per-subparser "
        "--verbose, or give them default=argparse.SUPPRESS."
    ),
)
def test_top_level_verbose_is_not_discarded_by_the_subcommand():
    parser = create_parser()
    args, _ = parser.parse_known_args(["--verbose", "--verbose", "downloader"])
    assert args.verbose == 2


@pytest.mark.parametrize(
    "command,dest,default",
    [
        ("downloader", "downloader_name", "S1_SLC"),
        ("processor", "processor_name", "Hyp3_S1"),
        ("analyzer", "analyzer_name", "Hyp3_Mintpy_SBAS"),
    ],
)
def test_component_name_flag(command, dest, default):
    """-N/--name selects the component, and defaults when omitted.

    Asserted with a value that is NOT the default: every one of these dests has
    a default, so checking `-N Hyp3_S1` gives processor_name == "Hyp3_S1" passes
    whether or not the flag is wired up at all.

    Note -N belongs to the `processor`/`analyzer` parser itself, not to their
    `submit`/`run` subcommands, so it must precede the subcommand.
    """
    parser = create_parser()

    args, _ = parser.parse_known_args([command])
    assert getattr(args, dest) == default, f"{command} -N default changed"

    args, extra = parser.parse_known_args([command, "-N", "SENTINEL_MARKER"])
    assert getattr(args, dest) == "SENTINEL_MARKER", (
        f"`insarhub {command} -N SENTINEL_MARKER` did not reach {dest}"
    )
    assert "-N" not in extra, "-N was not recognised by the parser"


def test_processor_name_must_precede_the_subcommand():
    """Documents the CLI's actual shape so the e2e harness builds valid argv.

    `insarhub processor submit -N X` leaves -N unparsed (it lands in extra and
    processor_name keeps its default), which silently submits the wrong
    processor. The correct form is `insarhub processor -N X submit`.
    """
    parser = create_parser()

    args, extra = parser.parse_known_args(["processor", "submit", "-N", "SENTINEL_MARKER"])
    assert args.processor_name == "Hyp3_S1", "unexpectedly parsed a post-subcommand -N"
    assert "-N" in extra

    args, extra = parser.parse_known_args(["processor", "-N", "SENTINEL_MARKER", "submit"])
    assert args.processor_name == "SENTINEL_MARKER"
    assert extra == []


def test_unknown_flags_are_tolerated_for_config_passthrough():
    """Config overrides arrive as arbitrary --key value pairs, so the parser
    must use parse_known_args and hand the remainder on rather than erroring."""
    parser = create_parser()
    args, extra = parser.parse_known_args(
        ["processor", "submit", "-P", "Hyp3_S1", "--some-config-key", "7"]
    )
    assert "--some-config-key" in extra
