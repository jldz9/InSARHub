# -*- coding: utf-8 -*-
"""
HPC-compatible CLI for InSARHub.

Every command handler builds an instance from CLI args, then delegates all
logic to the shared command layer (insarhub.commands). The same command
classes are used by the Panel frontend, so there is no duplicated business logic.

Pipeline subcommands
--------------------
insarhub downloader -N S1_SLC --AOI -113.05 37.74 -112.68 38.00 --start 2021-01-01 --end 2022-01-01
insarhub downloader ... --select-pairs --download --orbit-files --worker 8
insarhub processor -N Hyp3_S1 -w /data/bryce submit --worker 4
insarhub processor -N ISCE2_S1 -w /data/bryce --hpc-mode submit --worker 7
insarhub processor -N Hyp3_S1 -w /data/bryce refresh [-r]
insarhub processor -N Hyp3_S1 -w /data/bryce download [-r]
insarhub processor -N Hyp3_S1 -w /data/bryce retry [-r]
insarhub processor -N Hyp3_S1 -w /data/bryce watch --interval 300 [-r]
insarhub processor -N Hyp3_S1 -w /data/bryce credits
insarhub processor -N ISCE2      -w /data/bryce run    (local — not yet implemented)
insarhub analyzer   -N Hyp3_Mintpy_SBAS -w /data/bryce run
insarhub analyzer   -N Hyp3_Mintpy_SBAS -w /data/bryce cleanup

Utilities
---------
insarhub utils clip           --workdir /data/bryce --aoi -113.05 37.74 -112.68 38.00
insarhub utils h5-to-raster   --input velocity.h5
insarhub utils save-footprint --input velocity.h5
insarhub utils slurm          --job-name insar_run --cpus 8 --mem 32G --command "insarhub analyzer -N Hyp3_Mintpy_SBAS -w /data/bryce run"
insarhub utils era5-download  -w /data/bryce -o /data/era5
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from insarhub._version import __version__
from insarhub.config.paths import Hyp3Paths, StackPaths
from insarhub.utils.defaults import SELECT_PAIRS_DEFAULTS as _SP, DOWNLOAD_DEFAULTS as _DL
from insarhub.utils.local_processor_reload import (
    _parse_group_key, _read_config_json, _ROLE_CONFIG_STRIP_FIELDS,
    _read_proc_config_from_folder, _find_subfolder_config, _find_jobs_file,
    _jobs_glob, _load_local_processor, _call_if_supported, _SAVED_CFG_SKIP,
)


# ---------------------------------------------------------------------------
# Shared argument helpers
# ---------------------------------------------------------------------------


def _add_job_file(p: argparse.ArgumentParser):
    p.add_argument("--job-file", metavar="PATH",
                   help="Path to a saved HyP3 job JSON file (overrides default hyp3_jobs.json)")


def _add_credential_pool(p: argparse.ArgumentParser):
    p.add_argument("--credential-pool", metavar="PATH",
                   help='JSON file mapping {username: password} for multi-account HyP3 submission '
                        '(default: ~/.credit_pool)')


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insarhub",
        description="InSAR processing pipeline CLI",
        epilog="Use 'insarhub <command> --help' for details on each command.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"insarhub {__version__}")
    _VERBOSE_HELP = ("Show per-step progress (INFO). Repeat (--verbose --verbose) "
                     "for DEBUG. Warnings and errors are always shown.")
    parser.add_argument("--verbose", action="count", default=0, help=_VERBOSE_HELP)

    sub = parser.add_subparsers(dest="command", required=False, metavar="COMMAND")

    # ------------------------------------------------------------------ #
    # downloader — search + optionally download satellite scenes
    #
    # Config fields for the chosen downloader are passed as extra --KEY VALUE
    # flags and resolved dynamically at runtime.
    # Use --list-options to see all fields for the selected downloader.
    # ------------------------------------------------------------------ #
    p_search = sub.add_parser(
        "downloader",
        help="Search (and optionally download) satellite scenes",
        description=(
            "Search for scenes using any registered downloader.\n"
            "Downloader config fields are passed as extra --KEY VALUE flags.\n"
            "Run with --list-options to see all available fields for the selected downloader."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_search.add_argument("--verbose", action="count", default=0, help=_VERBOSE_HELP)

    g_dl = p_search.add_argument_group("downloader")
    g_dl.add_argument(
        "--list-downloaders", action="store_true",
        help="Print all registered downloaders and exit",
    )
    g_dl.add_argument(
        "-N", "--name", metavar="STR", default="S1_SLC", dest="downloader_name",
        help="Downloader name (default: S1_SLC; see --list-downloaders)",
    )
    g_dl.add_argument(
        "--pipeline", action="store_true",
        help="Show the full compatible processor → analyzer tree for the selected downloader and exit",
    )
    g_dl.add_argument(
        "--list-options", action="store_true",
        help="Print all additional config fields for the selected downloader",
    )
    g_dl.add_argument("-w", "--workdir", metavar="PATH",
                      help="Working directory (default: current directory)")
    g_dl.add_argument("--no-verify-ssl", action="store_true", dest="no_verify_ssl",
                      help="Disable SSL certificate verification (use if ASF cert is expired)")
    g_dl.add_argument("--config", metavar="PATH", nargs="?", const="__default__", default=None,
                      help="Path to a saved downloader config JSON; "
                           "omit the value to use <workdir>/insarhub_config.json")
    g_dl.add_argument(
        "--AOI", nargs="+", metavar="AOI",
        help="Area of interest: shapefile/GeoJSON path, WKT string, or 4 floats W S E N. "
             "Sets intersectsWith automatically.",
    )
    g_dl.add_argument(
        "--stacks", nargs="+", metavar="PATH:FRAME",
        help="Select specific track/frame stacks as PATH:FRAME tokens, "
             "e.g. --stacks 100:466 20:118 20:123. "
             "Sets relativeOrbit and frame; takes precedence over --relativeOrbit/--frame. "
             "For S1_Burst (no frame number exists on SLC-BURST) the second half is a "
             "burst ID instead: 124:124_264305_IW2, 124:264305_IW2, or 124:264305 "
             "(that index in every subswath). Exits non-zero if nothing matches.",
    )

    g_pairs = p_search.add_argument_group("pair selection  (requires --select-pairs)")
    g_pairs.add_argument(
        "--select-pairs", action="store_true", dest="select_pairs",
        help="Select interferogram pairs after search and save to pairs.json",
    )
    g_pairs.add_argument(
        "--dt-targets", nargs="+", type=int, default=list(_SP["dt_targets"]),
        metavar="DAYS", help=f"Target temporal spacings in days (default: {list(_SP['dt_targets'])})",
    )
    g_pairs.add_argument("--dt-tol",  type=int,   default=_SP["dt_tol"],  metavar="DAYS", help=f"Temporal tolerance in days (default: {_SP['dt_tol']})")
    g_pairs.add_argument("--dt-max",  type=int,   default=_SP["dt_max"],  metavar="DAYS", help=f"Max temporal baseline in days (default: {_SP['dt_max']})")
    g_pairs.add_argument("--pb-max",  type=float, default=_SP["pb_max"],  metavar="M",    help=f"Max perpendicular baseline in metres (default: {_SP['pb_max']})")
    g_pairs.add_argument("--min-degree", type=int, default=_SP["min_degree"], metavar="INT", help=f"Min connections per scene (default: {_SP['min_degree']})")
    g_pairs.add_argument("--max-degree", type=int, default=_SP["max_degree"], metavar="INT", help=f"Max connections per scene (default: {_SP['max_degree']})")
    g_pairs.add_argument("--force-connect", action=argparse.BooleanOptionalAction, default=_SP["force_connect"],
                         help=f"Force connectivity for isolated scenes (default: {_SP['force_connect']})")
    g_pairs.add_argument("--sp-workers", type=int, default=_SP["max_workers"], metavar="INT",
                         help=f"Threads for baseline API fallback (default: {_SP['max_workers']})")
    g_pairs.add_argument("--no-avoid-low-quality-days", action="store_false", dest="avoid_low_quality_days",
                         help="Disable weather/snow pre-filter (enabled by default)")
    g_pairs.set_defaults(avoid_low_quality_days=_SP["avoid_low_quality_days"])
    g_pairs.add_argument("--snow-threshold", type=float, default=_SP["snow_threshold"], metavar="FRAC",
                         help=f"Snow cover fraction [0-1] above which a scene is dropped (default: {_SP['snow_threshold']})")
    g_pairs.add_argument("--precip-mm-threshold", type=float, default=_SP["precip_mm_threshold"], metavar="MM",
                         help=f"3-day precipitation (mm) above which a scene is dropped (default: {_SP['precip_mm_threshold']})")
    g_pairs.add_argument("--pairs-output", metavar="PATH", default=None,
                         help="Output file for pairs (default: <workdir>/pairs.json)")

    g_down = p_search.add_argument_group("download")
    g_down.add_argument("-d", "--download",    action="store_true", help="Download scenes after search")
    g_down.add_argument("-O", "--orbit-files", nargs="?", const=True, default=False, metavar="PATH",
                        help="Download orbit files. Optionally specify a save directory, e.g. -O orbits/ (default: workdir)")
    g_down.add_argument("--merge", action="store_true",
                        help="Combine all frames sharing one relative orbit (path) into a single "
                             "stack instead of per-frame subdirs — e.g. workdir/p100_merged_f465_f466/. "
                             "Requires all results to share one path (raises otherwise).")
    g_down.add_argument("--worker", metavar="INT", type=int, default=_DL["max_workers"],
                        help=f"Parallel download workers (default: {_DL['max_workers']})")
    g_down.add_argument("--footprint", metavar="PATH",
                        help="Save footprint map image to this path")

    # ------------------------------------------------------------------ #
    # processor — submit + manage InSAR processing jobs
    #
    # Usage: insarhub processor -N <ProcessorName> <action> [options]
    #
    # HyP3 processors (online): submit | refresh | download | retry | watch | credits
    # Local processors (ISCE2_S1): submit | refresh | retry | watch
    # ------------------------------------------------------------------ #
    p_proc = sub.add_parser(
        "processor",
        help="Submit and manage InSAR processing jobs",
        description=(
            "Select a processor with -N and run an action.\n"
            "\nHyP3 (online) processor actions:\n"
            "  submit           Submit pairs to HyP3\n"
            "  refresh          Pull latest job statuses  [-r to search recursively incl. retry files]\n"
            "  download         Download completed outputs [-r to search recursively incl. retry files]\n"
            "  retry            Resubmit failed jobs       [-r to search recursively incl. retry files]\n"
            "  watch            Poll until all jobs complete [-r to search recursively incl. retry files]\n"
            "  credits          Show remaining HyP3 credits\n"
            "\nLocal processor actions (ISCE2_S1):\n"
            "  submit           Submit pairs to local ISCE2 processor (runs in background)\n"
            "  refresh          Show current job statuses\n"
            "  retry            Resubmit failed pairs\n"
            "  cancel           Kill background executor or scancel HPC jobs\n"
            "  watch            Poll until all pairs complete\n"
            "\nRun 'insarhub processor -N <name> <action> --help' for action details."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_proc.add_argument("--verbose", action="count", default=0, help=_VERBOSE_HELP)
    p_proc.add_argument(
        "-N", "--name", metavar="STR", default="Hyp3_S1", dest="processor_name",
        help="Processor name (default: Hyp3_S1; see --list-processors)",
    )
    p_proc.add_argument(
        "--list-processors", action="store_true",
        help="Print all registered processors and exit",
    )
    p_proc.add_argument("-w", "--workdir", metavar="PATH", default=None,
                        help="Working directory (default: current directory)")
    p_proc.add_argument(
        "--list-options", action="store_true",
        help="Print all config fields for the selected processor and exit",
    )
    p_proc.add_argument("--config", metavar="PATH", nargs="?", const="__default__", default=None,
                        help="Path to insarhub_config.json or a saved processor config JSON; "
                             "omit the value to use <workdir>/insarhub_config.json")
    proc_sub = p_proc.add_subparsers(dest="proc_action", required=False, metavar="ACTION")

    # --- submit  (HyP3) ----------------------------------------------- #
    p_proc_submit = proc_sub.add_parser(
        "submit",
        help="Submit interferogram pairs to a HyP3 processor",
        description=(
            "Submit pairs to the selected HyP3 processor.\n"
            "Processor config fields are passed as extra --KEY VALUE flags.\n"
            "Run 'insarhub processor -N <name> --list-options' to see all fields.\n"
            "When pairs.json has multiple groups (from 'downloader --select-pairs'),\n"
            "a separate job folder is created under workdir for each group."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g_sub = p_proc_submit.add_argument_group("submit options")
    g_sub.add_argument("--config", metavar="PATH", nargs="?", const="__default__", default=None,
                       help="Path to insarhub_config.json or a saved processor config JSON; "
                            "omit the value to use <workdir>/insarhub_config.json")
    g_sub.add_argument("--credential-pool", metavar="PATH",
                       help="JSON {username: password} for multi-account HyP3 submission")
    g_sub.add_argument("--name-prefix", metavar="STR", default="ifg",
                       help="Job name prefix (default: ifg)")
    g_sub.add_argument("--worker", metavar="INT", type=int, default=None,
                       help="Parallelism. Sets both axes so you need not know "
                            "which one a stage uses: concurrent SLURM jobs for "
                            "a stage that fans out, and threads inside the job "
                            "for one that cannot (e.g. ISCE3_Burst's ifg is a "
                            "single process). Override either individually with "
                            "--max_workers / --max_concurrent_hpc.")
    g_sub.add_argument("--step", metavar="STEP", nargs="+", default=None,
                       help="Local/ISCE processors only: force (re)run only these step(s), "
                            "regardless of saved status. Give the full name "
                            "(run_03_average_baseline), just the number (03 or 3), or a "
                            "run_03 prefix. Every other step is left untouched — unlike "
                            "'retry', this does NOT cascade into re-running downstream "
                            "steps. Omit to run normally (every step not already SUCCEEDED).")
    g_sub.add_argument("--dry-run", action="store_true",
                       help="Print what would be submitted without sending any jobs to HyP3")
    g_sub.add_argument("--container", metavar="PATH", nargs="?", const=_CONTAINER_DEFAULT_SENTINEL,
                       default=None,
                       help="Local/ISCE processors only: path to a .sif/Apptainer image or a "
                            "Docker image reference with insarhub installed — re-runs the "
                            "pipeline inside the container instead of on the host. Bare "
                            "--container uses the processor's default image.")
    g_sub_pairs = p_proc_submit.add_argument_group(
        "pairs input",
        "Provide pairs explicitly, or omit to auto-load pairs.json from workdir",
    )
    g_sub_pairs.add_argument(
        "--pairs-file", metavar="PATH",
        help="JSON file from 'downloader --select-pairs' (flat list or grouped dict)",
    )
    g_sub_pairs.add_argument(
        "--pairs", metavar='"ref,sec"', nargs="+",
        help='Inline pairs as "reference,secondary" strings (single group)',
    )

    # --- refresh  (HyP3) ---------------------------------------------- #
    p_proc_refresh = proc_sub.add_parser("refresh", help="Refresh HyP3 job statuses")
    _add_job_file(p_proc_refresh)
    p_proc_refresh.add_argument("-r", "--recursive", action="store_true",
                                help="Recursively search workdir for hyp3*.json job files")
    p_proc_refresh.add_argument(
        "--ls", metavar="STEP", nargs="?", const=True, default=None,
        help="Local/ISCE processors only: also show per-command (cmd_XXXX) detail. "
             "Bare --ls shows it for every step; --ls 03 (also accepts '3', "
             "'run_03') shows it for just that one step. Omit for step-level "
             "summary only (default).")
    p_proc_refresh.add_argument(
        "--container", metavar="PATH", nargs="?", const=_CONTAINER_DEFAULT_SENTINEL, default=None,
        help="Local/ISCE processors only: path to a .sif/Apptainer image or a "
             "Docker image reference with insarhub installed — needed on a host "
             "with no local ISCE2 install, since the processor is constructed "
             "(and ISCE2 discovery attempted) before refresh's own logic runs. "
             "Bare --container uses the processor's default image.")

    # --- download  (HyP3) --------------------------------------------- #
    p_proc_dl = proc_sub.add_parser("download", help="Download completed HyP3 job outputs")
    _add_job_file(p_proc_dl)
    p_proc_dl.add_argument("--worker", metavar="INT", type=int, default=None,
                           help="Parallel download workers (overrides saved config)")
    p_proc_dl.add_argument("-r", "--recursive", action="store_true",
                           help="Recursively search workdir for hyp3*.json job files")

    # --- retry  (HyP3) ------------------------------------------------- #
    p_proc_retry = proc_sub.add_parser("retry", help="Resubmit failed HyP3 jobs")
    _add_job_file(p_proc_retry)
    p_proc_retry.add_argument("-r", "--recursive", action="store_true",
                              help="Recursively search workdir for hyp3*.json job files")
    p_proc_retry.add_argument(
        "--container", metavar="PATH", nargs="?", const=_CONTAINER_DEFAULT_SENTINEL, default=None,
        help="Local/ISCE processors only: path to a .sif/Apptainer image or a "
             "Docker image reference with insarhub installed — retries inside "
             "the container instead of on the host. Bare --container uses the "
             "processor's default image.")

    # --- watch  (HyP3) ------------------------------------------------- #
    p_proc_watch = proc_sub.add_parser(
        "watch", help="Poll HyP3 until all jobs complete, downloading results as they succeed")
    _add_job_file(p_proc_watch)
    p_proc_watch.add_argument("--interval", metavar="SEC", type=int, default=300,
                              help="Seconds between refreshes (default: 300)")
    p_proc_watch.add_argument("--worker", metavar="INT", type=int, default=None,
                              help="Parallel download workers (overrides saved config)")
    p_proc_watch.add_argument("-r", "--recursive", action="store_true",
                              help="Recursively search workdir for hyp3*.json job files")
    p_proc_watch.add_argument(
        "--container", metavar="PATH", nargs="?", const=_CONTAINER_DEFAULT_SENTINEL, default=None,
        help="Local/ISCE processors only: path to a .sif/Apptainer image or a "
             "Docker image reference with insarhub installed — needed on a host "
             "with no local ISCE2 install, since the processor is constructed "
             "(and ISCE2 discovery attempted) before watch's own logic runs. "
             "Bare --container uses the processor's default image.")

    # --- credits  (HyP3) ----------------------------------------------- #
    p_proc_credits = proc_sub.add_parser("credits", help="Show remaining HyP3 processing credits")
    _add_credential_pool(p_proc_credits)

    # --- cancel  (local / HPC) ----------------------------------------- #
    p_proc_cancel = proc_sub.add_parser(
        "cancel",
        help="Cancel all running/pending jobs (local: SIGTERM; HPC: scancel)"
    )
    p_proc_cancel.add_argument(
        "--container", metavar="PATH", nargs="?", const=_CONTAINER_DEFAULT_SENTINEL, default=None,
        help="Local/ISCE processors only: path to a .sif/Apptainer image or a "
             "Docker image reference with insarhub installed — needed on a host "
             "with no local ISCE2 install, since the processor is constructed "
             "(and ISCE2 discovery attempted) before cancel's own logic runs. "
             "Bare --container uses the processor's default image.")

    # --- run-stage-unit  (internal: one HPC child job's unit of work) --- #
    p_proc_run_stage_unit = proc_sub.add_parser(
        "run-stage-unit",
        help="Internal: run one HPC child job's unit of work (GMTSAR_S1 stack_mode)",
        description=(
            "Not meant to be run by hand -- this is what GMTSAR_S1's HPC-mode "
            "child sbatch jobs invoke to re-enter insarhub and run one stack "
            "stage's unit of work (align/intf/merge), since GMTSAR_S1 has no "
            "flat shell-command-list generator the way ISCE2_S1's stackSentinel.py "
            "run_NN_* files do. See gmtsar_s1.py's run_stage_unit() docstring."
        ),
    )
    p_proc_run_stage_unit.add_argument(
        "--config", metavar="PATH", nargs="?", const="__default__", default="__default__",
        help="Path to insarhub_config.json; omit the value to use "
             "<workdir>/insarhub_config.json (the default)")
    p_proc_run_stage_unit.add_argument(
        "--stage", metavar="STAGE", required=True,
        help="Stack stage name: align / topo / intf / merge")
    p_proc_run_stage_unit.add_argument(
        "--subswath", metavar="INT", type=int, default=None,
        help="Which subswath's align/topo/intf unit to run (multi-subswath "
             "stacks only; omit for single-subswath)")
    p_proc_run_stage_unit.add_argument(
        "--index", metavar="INT", type=int, default=None,
        help="Pair index within the subswath (intf stage only)")

    # ------------------------------------------------------------------ #
    # analyzer — prepare + run MintPy SBAS time-series analysis
    #
    # Actions: prep_data | run | cleanup
    # Config fields for the chosen analyzer are passed as extra --KEY VALUE
    # flags (run only) and resolved dynamically at runtime.
    # ------------------------------------------------------------------ #
    _step_table = (
        "Available steps for --step:\n"
        "\n"
        "  Keyword       Description\n"
        "  ----------    ----------------------------------------\n"
        "  prep_data     Prepare data (unzip, clip, write .mintpy.cfg)  [alias: prep]\n"
        "  all           prep_data + all MintPy steps below (default if --step omitted)\n"
        "  plot          (Re)generate figures under mintpy/pic/ from already-computed\n"
        "                results. Not a real MintPy step -- runs automatically after\n"
        "                any --step selection of more than one MintPy step, or on its\n"
        "                own via --step plot.\n"
        "\n"
        "  MintPy step             \n"
        "  --------------------\n"
        + "".join(f"  {s}\n" for s in _MINTPY_ALL_STEPS)
        + "\n"
        "Examples:\n"
        "  insarhub analyzer -N Hyp3_Mintpy_SBAS run\n"
        "  insarhub analyzer -N Hyp3_Mintpy_SBAS --compute-maxMemory 30 run --step velocity\n"
        "  insarhub analyzer -N Hyp3_Mintpy_SBAS --list-options\n"
        "  insarhub analyzer -N Hyp3_Mintpy_SBAS cleanup\n"
    )
    p_analyzer = sub.add_parser(
        "analyzer",
        help="Prepare data and run MintPy SBAS time-series analysis",
        description=(
            "Prepare HyP3 data and run MintPy SBAS time-series analysis.\n"
            "Select the analyzer and set config options here; then choose an action.\n"
            "Config fields are passed as extra --KEY VALUE flags (see --list-options).\n"
            "\nActions:\n"
            "  run              Run analysis workflow (see --step below)\n"
            "  cleanup          Remove temporary files\n"
            "\n"
            + _step_table
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_analyzer.add_argument("--verbose", action="count", default=0, help=_VERBOSE_HELP)
    p_analyzer.add_argument(
        "-N", "--name", metavar="STR", default="Hyp3_Mintpy_SBAS", dest="analyzer_name",
        help="Analyzer name (default: Hyp3_Mintpy_SBAS; see --list-analyzers)",
    )
    p_analyzer.add_argument("-w", "--workdir", metavar="PATH", default=None,
                            help="Working directory containing HyP3 results (default: current directory)")
    p_analyzer.add_argument(
        "--list-analyzers", action="store_true",
        help="Print all registered analyzers and exit",
    )
    p_analyzer.add_argument(
        "--list-options", action="store_true",
        help="Print all config fields for the selected analyzer and exit",
    )
    # Pre-register analyzer config fields so argparse knows they consume a value
    # and won't treat their argument as the ACTION subcommand.
    # Use SUPPRESS default so only explicitly set fields appear in args namespace.
    try:
        from insarhub.config.defaultconfig import Hyp3_Mintpy_SBAS_Config
        import dataclasses as _dc, typing as _typing
        _hints = _typing.get_type_hints(Hyp3_Mintpy_SBAS_Config)
        for _f in _dc.fields(Hyp3_Mintpy_SBAS_Config):
            if _f.name in _ANALYZER_SKIP_FIELDS:
                continue
            if _f.name == "container":
                # --container is registered on the `run` subparser instead
                # (canonical position: AFTER the action, matching the processor
                # and every doc example). Registering it on the parent too let
                # its nargs="?" greedily swallow the action -- `--container run
                # --step X` parsed `run` as --container's value. Skip it here.
                continue
            else:
                _kwargs = _field_argparse_kwargs(_hints.get(_f.name, str), None)
                _kwargs["default"] = argparse.SUPPRESS
                _kwargs["help"] = argparse.SUPPRESS
            try:
                _flag_h = "--" + _f.name.replace("_", "-")
                _flag_u = "--" + _f.name
                if _flag_h != _flag_u:
                    p_analyzer.add_argument(_flag_h, _flag_u, dest=_f.name, **_kwargs)
                else:
                    p_analyzer.add_argument(_flag_h, dest=_f.name, **_kwargs)
            except argparse.ArgumentError:
                pass
    except Exception:
        pass

    az_sub = p_analyzer.add_subparsers(dest="az_action", required=False, metavar="ACTION")

    # --- run ----------------------------------------------------------- #
    p_az_run = az_sub.add_parser(
        "run",
        help="Run analysis workflow (step(s) defined by --step)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_az_run.add_argument(
        "--step", metavar="STEP", nargs="+",
        help="Step(s) to run — see parent 'analyzer --help' for the full table",
    )
    # --container is also pre-registered on the parent (from the config fields),
    # which only matches when placed BEFORE the action (`analyzer --container run`).
    # Register it on the run subparser too so it also works in the natural
    # position AFTER the action (`analyzer ... run --container`), mirroring the
    # processor subcommands. nargs="?" + const keeps a bare `--container`
    # resolving to the analyzer's default image; default=SUPPRESS so it doesn't
    # clobber a value given before the action.
    p_az_run.add_argument(
        "--container", metavar="PATH", nargs="?", const=_CONTAINER_DEFAULT_SENTINEL,
        default=argparse.SUPPRESS,
        help="Run inside a .sif/Docker image with insarhub+MintPy installed. "
             "Bare --container uses the analyzer's default image.")
    p_az_run.add_argument("--debug", action="store_true",
                          help="Enable MintPy debug mode")

    # --- cleanup ------------------------------------------------------- #
    p_az_cleanup = az_sub.add_parser(
        "cleanup",
        help="Remove temporary files after analysis",
    )
    p_az_cleanup.add_argument("--debug", action="store_true",
                              help="Debug mode — preserve temporary files (dry run)")

    # ================================================================== #
    #  utils                                                              #
    # ================================================================== #
    p_utils = sub.add_parser(
        "utils",
        help="Standalone utility tools",
        description=(
            "Standalone utility tools.\n"
            "\nUtilities:\n"
            "  clip           Clip HyP3 zip contents to an AOI\n"
            "  h5-to-raster   Convert MintPy HDF5 output to GeoTIFF\n"
            "  save-footprint Extract footprint polygon from a raster\n"
            "  plot-network   Plot interferogram network from a pairs JSON file\n"
            "  slurm          Generate a SLURM batch script\n"
            "  era5-download  Download ERA5 weather data for MintPy tropospheric correction\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_utils.add_argument("--verbose", action="count", default=0, help=_VERBOSE_HELP)
    ut_sub = p_utils.add_subparsers(dest="ut_action", required=False, metavar="TOOL")

    # --- clip ---------------------------------------------------------- #
    p_clip = ut_sub.add_parser(
        "clip",
        help="Clip HyP3 zip contents to an AOI for MintPy",
    )
    p_clip.add_argument("-w", "--workdir", metavar="PATH", default=None,
                        help="Directory containing HyP3 .zip files (default: cwd)")
    p_clip.add_argument("--aoi", metavar="VALUE", nargs="+", required=True,
                        help="AOI as 'minlon minlat maxlon maxlat' or path to GeoJSON/SHP file")

    # --- h5-to-raster -------------------------------------------------- #
    p_h5 = ut_sub.add_parser(
        "h5-to-raster",
        help="Convert MintPy HDF5 output to GeoTIFF",
    )
    p_h5.add_argument("-i", "--input", metavar="PATH", required=True,
                      help="Input HDF5 file (e.g. velocity.h5)")
    p_h5.add_argument("-o", "--output", metavar="PATH", default=None,
                      help="Output GeoTIFF path (default: same name as input with .tif)")

    # --- save-footprint ------------------------------------------------ #
    p_fp = ut_sub.add_parser(
        "save-footprint",
        help="Extract footprint polygon from a raster",
    )
    p_fp.add_argument("-i", "--input", metavar="PATH", required=True,
                      help="Input raster file")
    p_fp.add_argument("-o", "--output", metavar="PATH", default=None,
                      help="Output footprint file (default: auto-named beside input)")

    # --- slurm --------------------------------------------------------- #
    p_slurm = ut_sub.add_parser(
        "slurm",
        help="Generate a SLURM batch job script",
        description=(
            "Generate a SLURM batch script from the given resource and environment\n"
            "parameters. The --command argument is the shell command(s) to execute\n"
            "inside the job (e.g. an insarhub analyzer run invocation).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_slurm.add_argument("--job-name", metavar="STR", default="insarhub_job",
                         help="SLURM job name (default: insarhub_job)")
    p_slurm.add_argument("--time", metavar="HH:MM:SS", default="04:00:00",
                         help="Wall-time limit (default: 04:00:00)")
    p_slurm.add_argument("--partition", metavar="STR", default="all",
                         help="SLURM partition (default: all)")
    p_slurm.add_argument("--nodes", metavar="N", type=int, default=1,
                         help="Number of nodes (default: 1)")
    p_slurm.add_argument("--ntasks", metavar="N", type=int, default=1,
                         help="Number of tasks (default: 1)")
    p_slurm.add_argument("--cpus", metavar="N", type=int, default=8,
                         help="CPUs per task (default: 8)")
    p_slurm.add_argument("--mem", metavar="STR", default="32G",
                         help="Memory per node (default: 32G)")
    p_slurm.add_argument("--gpus", metavar="STR", default=None,
                         help="GPU allocation e.g. '1' or '2' (optional)")
    p_slurm.add_argument("--conda-env", metavar="STR", default=None,
                         help="Conda environment to activate (optional)")
    p_slurm.add_argument("--modules", metavar="MOD", nargs="+", default=[],
                         help="Environment modules to load (optional)")
    p_slurm.add_argument("--mail-user", metavar="EMAIL", default=None,
                         help="Email address for job notifications (optional)")
    p_slurm.add_argument("--mail-type", metavar="STR", default="ALL",
                         help="When to send email: BEGIN, END, FAIL, ALL (default: ALL)")
    p_slurm.add_argument("--account", metavar="STR", default=None,
                         help="Account to charge resources to (optional)")
    p_slurm.add_argument("--qos", metavar="STR", default=None,
                         help="Quality of Service specification (optional)")
    p_slurm.add_argument("--command", metavar="CMD", required=True, dest="job_command",
                         help="Command(s) to execute inside the job")
    p_slurm.add_argument("-o", "--output", metavar="PATH", default="job.slurm",
                         help="Output script path (default: job.slurm)")

    # --- plot-network -------------------------------------------------- #
    p_pn = ut_sub.add_parser(
        "plot-network",
        help="Plot interferogram network from a pairs JSON file",
    )
    p_pn.add_argument("-i", "--input", metavar="PATH", required=True,
                      help="Pairs JSON file (plain list produced by downloader --select-pairs)")
    p_pn.add_argument("--baselines", metavar="PATH", default=None,
                      help="Baselines JSON file (default: auto-detect beside the pairs file)")
    p_pn.add_argument("-o", "--output", metavar="PATH", default=None,
                      help="Output PNG path (default: network.png beside the pairs file)")
    p_pn.add_argument("--title", metavar="STR", default="Interferogram Network",
                      help="Plot title (default: 'Interferogram Network')")

    # --- era5-download ------------------------------------------------- #
    p_era5 = ut_sub.add_parser(
        "era5-download",
        help="Download ERA5 weather data for MintPy tropospheric correction",
        description=(
            "Scan a workdir of HyP3 zip files, determine required dates and spatial\n"
            "extents, and download ERA5 pressure-level data in MintPy-compatible\n"
            "filename format (ERA5_S*_N*_W*_E*_YYYYMMDD_HH.grb) via the CDS API.\n"
            "\nRequires a ~/.cdsapirc file with your CDS API credentials.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_era5.add_argument("-w", "--workdir", metavar="PATH", default=".",
                        help="Directory containing HyP3 zip files (default: current directory)")
    p_era5.add_argument("-o", "--output", metavar="PATH", default=".",
                        help="Output directory for ERA5 .grb files (default: current directory)")
    p_era5.add_argument("--num-processes", metavar="N", type=int, default=_DL["max_workers"],
                        help=f"Parallel download workers (default: {_DL['max_workers']})")
    p_era5.add_argument("--max-retries", metavar="N", type=int, default=3,
                        help="Retry attempts per file on download failure (default: 3)")

    return parser


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def _resolve_workdir(raw: str | None) -> Path:
    """Resolve -w, failing loudly when it does not exist.

    A relative -w is resolved against the CURRENT directory, so running
    `-w p56` from inside p56 silently yields p56/p56. Without this check the
    first thing to notice was whatever the command needed next -- typically
    "No pairs file found under current workdir .../p56/p56", which blames a
    missing pairs file for what is really a mistyped path.
    """
    if not raw:
        return Path.cwd()
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        print(f"[ERROR] workdir does not exist: {p}", file=sys.stderr)
        if Path(raw).name == Path.cwd().name:
            print(f"        You may already be inside it -- use '-w .' "
                  f"or run from {Path.cwd().parent}", file=sys.stderr)
        sys.exit(1)
    return p


def _find_job_files(job_dir: Path, override: str | None = None,
                    include_retry: bool = False) -> list[Path]:
    """Return all hyp3*.json job files in job_dir, or just the override file if given."""
    if override:
        return [Path(override).expanduser().resolve()]
    return sorted(p for p in job_dir.glob("hyp3*.json")
                  if include_retry or not p.name.startswith("hyp3_retry_"))


def _load_credential_pool(path: str | None) -> dict | None:
    from insarhub.utils import earth_credit_pool
    resolved = Path(path).expanduser().resolve() if path else Path("~/.credit_pool").expanduser()
    if not resolved.exists():
        if path:
            print(f"[ERROR] Credential pool file not found: {resolved}", file=sys.stderr)
            sys.exit(1)
        return None
    return earth_credit_pool(resolved)



def _fail(result, label: str):
    """Print error and exit if a CommandResult indicates failure."""
    if not result.success:
        print(f"[ERROR] {label}: {result.errors[0] if result.errors else result.message}",
              file=sys.stderr)
        sys.exit(1)


def _iter_job_dirs(workdir: Path, job_file_override: str | None,
                   recursive: bool = False) -> list[Path]:
    """
    Return the list of directories to operate on for lifecycle commands.

    Resolution order:
      1. --job-file given  → parent directory of that file only
      2. recursive=True    → all dirs under workdir that contain hyp3*.json
      3. workdir has insarhub_config.json  → workdir itself (already a stack dir)
      4. p*_f* subdirs that contain any hyp3*.json  → each subdir
      5. workdir itself  (flat / single-group case)
    """
    if job_file_override:
        return [Path(job_file_override).expanduser().resolve().parent]
    if recursive:
        dirs = sorted({p.parent for p in workdir.rglob("hyp3*.json")})
        return dirs if dirs else [workdir]
    if (workdir / "insarhub_config.json").exists():
        return [workdir]
    subdirs = sorted(
        d for d in workdir.iterdir()
        if d.is_dir() and _parse_group_key(d.name) and any(d.glob("hyp3*.json"))
    )
    return subdirs if subdirs else [workdir]


def _iter_job_entries(workdir: Path, args) -> "Iterator[tuple[Path, Path, str]]":
    """Yield (job_dir, job_file, tag) for every HyP3 job file across all matched
    job directories — shared by refresh/download/retry/watch's identical scan."""
    recursive = getattr(args, "recursive", False)
    for job_dir in _iter_job_dirs(workdir, args.job_file, recursive=recursive):
        for jf in _find_job_files(job_dir, args.job_file, include_retry=recursive):
            tag = f"[{job_dir.name}/{jf.name}] " if job_dir != workdir else f"[{jf.name}] "
            yield job_dir, jf, tag


def _iter_analysis_dirs(workdir: Path) -> list[Path]:
    """
    Return the list of directories to run analysis on.

    Resolution order:
      1. workdir has insarhub_config.json  → workdir itself (already a stack dir)
      2. p*_f* subdirs that contain any *.zip files  → each subdir
      3. workdir itself  (flat / single-group case)
    """
    if (workdir / "insarhub_config.json").exists():
        return [workdir]
    def _has_zips(d: Path) -> bool:
        hyp3_sub = Hyp3Paths(d).output_dir
        return any(hyp3_sub.glob("*.zip")) if hyp3_sub.is_dir() else any(d.glob("*.zip"))

    subdirs = sorted(
        d for d in workdir.iterdir()
        if d.is_dir() and _parse_group_key(d.name) and _has_zips(d)
    )
    return subdirs if subdirs else [workdir]


def _load_hyp3_processor(workdir: Path, job_file: Path | None = None,
                          credential_pool_path: str | None = None,
                          processor_name: str = "Hyp3_S1",
                          **extra_overrides):
    """Build a HyP3 processor, loading saved jobs from job_file when provided."""
    from insarhub import Processor

    overrides: dict = {"workdir": workdir}
    if job_file:
        overrides["saved_job_path"] = job_file

    pool = _load_credential_pool(credential_pool_path)
    if pool:
        overrides["earthdata_credentials_pool"] = pool

    overrides.update({k: v for k, v in extra_overrides.items() if v is not None})
    return Processor.create(processor_name, **overrides)


# ---------------------------------------------------------------------------
# Dynamic config introspection helpers (used by cmd_search)
# ---------------------------------------------------------------------------


def _unwrap_optional(annotation):
    """Extract the non-None type from 'X | None' or 'Optional[X]'.

    When the union contains both a scalar and list type (e.g. int | list[int] | None),
    prefer the list type so CLI flags accept multiple values with nargs="+".
    """
    import types as _types
    import typing
    origin = typing.get_origin(annotation)
    # covers both Union[X, None] and X | None (Python 3.10+)
    if origin is typing.Union or isinstance(annotation, getattr(_types, "UnionType", type(None))):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        # Prefer list[X] over scalar X so multi-value fields get nargs="+"
        list_types = [a for a in args if typing.get_origin(a) is list]
        return list_types[0] if list_types else (args[0] if args else str)
    return annotation




def _field_argparse_kwargs(annotation, default) -> dict:
    """Return kwargs for ArgumentParser.add_argument() inferred from a type annotation."""
    import typing
    import dataclasses

    base = _unwrap_optional(annotation)
    origin = typing.get_origin(base)

    if base is bool:
        # Pure boolean trigger: the flag never consumes a following token, so
        # `--hpc-mode run` (flag before the ACTION subcommand) sets True and
        # leaves `run` as the action. The old nargs="?" form greedily ate
        # `run` and died with "expected a boolean, got 'run'". Config files
        # and the frontend pass booleans as JSON, and _serialize_config_overrides
        # only re-emits bare flags for True, so the explicit `--flag false`
        # value form has no caller here.
        return {"action": "store_true",
                "default": bool(default) if default is not None else False}

    if origin is list:
        inner_args = typing.get_args(base)
        inner = inner_args[0] if inner_args else str
        return {"nargs": "+", "type": inner, "default": default}

    if base in (int, float, str, Path):
        return {"type": str if base is Path else base, "default": default, "metavar": base.__name__.upper()}

    # Fallback for complex types (e.g. tuple, custom classes) — accept as string
    return {"type": str, "default": default, "metavar": "VALUE"}


_MINTPY_ALL_STEPS = [
    'load_data', 'modify_network', 'reference_point', 'quick_overview',
    'correct_unwrap_error', 'invert_network',
    'correct_LOD', 'correct_SET', 'correct_ionosphere', 'correct_troposphere',
    'deramp', 'correct_topography', 'residual_RMS', 'reference_date',
    'velocity', 'geocode', 'google_earth', 'hdfeos5',
]

_SEARCH_SKIP_FIELDS = {"name"}  # handled via CLI flags or internal

# Fields handled by static flags or internal state in cmd_processor
_SUBMIT_SKIP_FIELDS = {
    "name", "workdir", "pairs", "saved_job_path",
    "earthdata_credentials_pool",
    "name_prefix",
    "sbatch_options_per_step",
}
# Runtime-only fields: still valid as CLI flags (unlike _SUBMIT_SKIP_FIELDS,
# which also controls flag generation), but never persisted to or reloaded
# from insarhub_config.json — pass again each invocation, like --dry-run.
#
# ``container`` is deliberately NOT here: it must be persisted so a GUI retry
# (which has no --container flag to re-pass) re-runs inside the same image
# instead of silently falling back to the host. A later submit/retry with an
# explicit --container still overrides the persisted value.
_RUNTIME_ONLY_FIELDS: set[str] = set()
# Sentinel for a bare `--container` (no value): resolve to the processor/
# analyzer's own `container_default` config value.
_CONTAINER_DEFAULT_SENTINEL = "__default__"


def _resolve_container_arg(name: str, container_arg: str | None) -> str | None:
    """Resolve a `--container` flag value against a processor/analyzer name.

    ``None``       → no container / keep the persisted value (host fallback).
    ``__default__``→ the processor/analyzer's ``container_default`` image.
    anything else  → that explicit image.
    """
    if container_arg is None or container_arg != _CONTAINER_DEFAULT_SENTINEL:
        return container_arg
    from insarhub import Analyzer, Processor
    for registry in (Processor._registry, Analyzer._registry):
        cfg_cls = getattr(registry.get(name), "default_config", None)
        if cfg_cls is not None:
            return getattr(cfg_cls, "container_default", None)
    return None
# _SAVED_CFG_SKIP / _ROLE_CONFIG_STRIP_FIELDS / _read_config_json now live in
# utils/local_processor_reload.py (shared with app/routes/processor.py) --
# imported below.

# Fields handled by static flags or internal state in cmd_analyzer
_ANALYZER_SKIP_FIELDS = {"name", "workdir", "debug"}

# str fields whose value is really "LAT LON" (two numbers) rather than one
# opaque token -- accept two space-separated CLI args instead of requiring
# the caller to quote them into one shell argument (e.g. --reference_lalo
# 37.84 -112.82, not just --reference_lalo "37.84 -112.82" -- both work).
# Re-joined with a space in _apply_config_overrides() before being stored,
# since the rest of the codebase (MintPy config generation, GUI text field)
# still expects the field as a single "LAT LON" string, unchanged.
_LATLON_FIELDS = {"reference_lalo"}

# str/int|str fields holding an ISCE-style space-separated list of a
# *variable* number of values (unlike _LATLON_FIELDS' fixed count of 2) --
# same footgun, same fix: accept space-separated CLI tokens unquoted and
# rejoin them, instead of silently dropping everything after the first
# token (default "1 2 3" means e.g. --subswath 1 2 3 unquoted).
_SPACE_JOINED_FIELDS = {"subswath", "swath_num"}

_NEG_NUMBER_RE = re.compile(r'^-\d[\d.eE+\-]*$')


def _filter_unknown_flags(unknown: list[str]) -> list[str]:
    """Drop standalone negative-number tokens from argparse unknown list.

    When a parser has type=int/float options, argparse sets
    _has_negative_number_optionals=True and then mis-classifies tokens like
    '-105.51' as potential flags instead of values. Filter them out so we
    only error on genuinely unknown flags (e.g. '--typo-flag').
    """
    return [u for u in unknown if not _NEG_NUMBER_RE.match(u)]


def _build_config_parser(config_cls, skip_fields: set | None = None) -> argparse.ArgumentParser:
    """Build an ArgumentParser populated with flags from a config dataclass."""
    import dataclasses
    import typing

    if skip_fields is None:
        skip_fields = _SEARCH_SKIP_FIELDS

    p = argparse.ArgumentParser(add_help=False)
    try:
        hints = typing.get_type_hints(config_cls)
    except Exception:
        hints = {}

    _UNSET = object.__new__(object)  # sentinel: field not provided by user

    for field in dataclasses.fields(config_cls):
        if field.name in skip_fields:
            continue
        annotation = hints.get(field.name, str)

        flag_hyphen = "--" + field.name.replace("_", "-")
        flag_under  = "--" + field.name
        kwargs = _field_argparse_kwargs(annotation, None)
        if field.name in _LATLON_FIELDS:
            # nargs=2 would reject a quoted single "LAT LON" token (argparse
            # applies type= per-token, and float("37.8 -112.8") raises) --
            # accept 1+ raw string tokens instead and sort out unquoted vs.
            # quoted (and validate the count/parseability) in
            # _apply_config_overrides, so both forms work:
            #   --reference_lalo 37.84 -112.82
            #   --reference_lalo "37.84 -112.82"
            kwargs["nargs"] = "+"
            kwargs["type"] = str
            kwargs["metavar"] = "LAT_LON"
        elif field.name in _SPACE_JOINED_FIELDS:
            kwargs["nargs"] = "+"
            kwargs["type"] = str
            kwargs["metavar"] = "VALUE"
        kwargs["default"] = _UNSET  # distinguish "not provided" from any real value
        kwargs["help"] = argparse.SUPPRESS  # hidden; shown only via --list-options
        try:
            if flag_hyphen != flag_under:
                p.add_argument(flag_hyphen, flag_under, dest=field.name, **kwargs)
            else:
                p.add_argument(flag_hyphen, dest=field.name, **kwargs)
        except argparse.ArgumentError:
            pass  # skip duplicate flags (e.g. --workdir already added)

    p._unset_sentinel = _UNSET  # type: ignore[attr-defined]

    return p


def _apply_config_overrides(overrides: dict, config_cls, extra_args: list[str],
                            skip_fields: set, label: str) -> None:
    """Parse extra_args as --flag overrides for config_cls's dataclass fields,
    merging any explicitly-provided values into `overrides` (mutated in place).

    Prints an [ERROR] and exits if any flags are unrecognized; prints a
    [WARNING] and does nothing if config_cls has no dataclass fields at all.
    """
    import dataclasses

    if config_cls is None or not dataclasses.is_dataclass(config_cls):
        if extra_args:
            print(f"[WARNING] Extra args ignored (no config dataclass): {extra_args}", file=sys.stderr)
        return

    config_parser = _build_config_parser(config_cls, skip_fields=skip_fields)
    config_ns, unknown = config_parser.parse_known_args(extra_args)
    if _filter_unknown_flags(unknown):
        print(f"[ERROR] Unknown flags for '{label}': {_filter_unknown_flags(unknown)}", file=sys.stderr)
        sys.exit(1)

    unset = config_parser._unset_sentinel  # type: ignore[attr-defined]
    for f in dataclasses.fields(config_cls):
        val = getattr(config_ns, f.name, unset)
        if val is not unset and val is not None:
            if f.name in _LATLON_FIELDS and isinstance(val, list):
                # 1 token: already one quoted "LAT LON" string, used as-is.
                # 2+ tokens: unquoted "LAT LON" split by the shell, rejoin.
                joined = val[0] if len(val) == 1 else " ".join(val)
                parts = joined.split()
                flag = "--" + f.name.replace("_", "-")
                if len(parts) != 2:
                    print(f"[ERROR] {flag} expects LAT LON (two numbers), got: {joined!r}",
                          file=sys.stderr)
                    sys.exit(1)
                try:
                    float(parts[0]), float(parts[1])
                except ValueError:
                    print(f"[ERROR] {flag} expects two numbers (LAT LON), got: {joined!r}",
                          file=sys.stderr)
                    sys.exit(1)
                val = joined
            elif f.name in _SPACE_JOINED_FIELDS and isinstance(val, list):
                # Any count is valid here (unlike _LATLON_FIELDS' exactly 2)
                # -- e.g. --subswath 1, --subswath 1 2, --subswath 1 2 3.
                val = val[0] if len(val) == 1 else " ".join(val)
            overrides[f.name] = val


def _print_config_options(config_cls_or_instance, display_label: str | None = None,
                          skip_fields: set | None = None,
                          value_overrides: dict | None = None):
    """Pretty-print all config fields for --list-options.

    Accepts either a dataclass *class* (shows defaults) or a dataclass *instance*.
    value_overrides: field_name → str value read from .mintpy.cfg, shown instead of defaults.
    """
    import dataclasses
    import typing

    if skip_fields is None:
        skip_fields = _SEARCH_SKIP_FIELDS

    instance = None
    if dataclasses.is_dataclass(config_cls_or_instance) and not isinstance(config_cls_or_instance, type):
        instance = config_cls_or_instance
        config_cls = type(instance)
    else:
        config_cls = config_cls_or_instance

    try:
        hints = typing.get_type_hints(config_cls)
    except Exception:
        hints = {}

    label = display_label or config_cls.__name__
    if value_overrides is not None:
        value_col = "CURRENT VALUE"
    elif instance is not None:
        value_col = "CURRENT VALUE"
    else:
        value_col = "DEFAULT"
    print(f"\nConfig fields for {label}:\n")
    print(f"  {'FLAG':<35}  {'TYPE':<25}  {value_col}")
    print(f"  {'-'*35}  {'-'*25}  {'-'*20}")
    for field in dataclasses.fields(config_cls):
        if field.name in skip_fields:
            continue
        flag = "--" + field.name
        ann = hints.get(field.name, "?")
        if value_overrides is not None and field.name in value_overrides:
            value = value_overrides[field.name]
        elif instance is not None:
            value = repr(getattr(instance, field.name))
        elif field.default is not dataclasses.MISSING:
            value = repr(field.default)
        elif field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            value = repr(field.default_factory())
        else:
            value = "(required)"
        print(f"  {flag:<35}  {str(ann):<25}  {value}")
    print()


def _read_mintpy_cfg(cfg_path: Path) -> dict[str, str]:
    """Read a .mintpy.cfg file and return {dataclass_field: value} by reverse-mapping keys.

    mintpy.compute.maxMemory → compute_maxMemory
    """
    result = {}
    for line in cfg_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if key.startswith('mintpy.'):
            field_name = key[len('mintpy.'):].replace('.', '_')
            result[field_name] = value
    return result


def _field_to_mintpy_key(field_name: str) -> str:
    """Convert dataclass field name to mintpy config key.
    compute_maxMemory → mintpy.compute.maxMemory
    """
    parts = field_name.split('_')
    if len(parts) > 1:
        return f"mintpy.{parts[0]}.{'.'.join(parts[1:])}"
    return f"mintpy.{parts[0]}"


def _update_mintpy_cfg(cfg_path: Path, overrides: dict) -> None:
    """Apply field_name → value overrides in-place to an existing .mintpy.cfg."""
    lines = cfg_path.read_text().splitlines()
    updated = {_field_to_mintpy_key(k): str(v) for k, v in overrides.items()}
    new_lines = []
    for line in lines:
        if '=' in line and not line.strip().startswith('#'):
            key = line.partition('=')[0].strip()
            if key in updated:
                new_lines.append(f"{key:<40} = {updated.pop(key)}")
                continue
        new_lines.append(line)
    cfg_path.write_text('\n'.join(new_lines) + '\n')


def _read_dl_config_from_folder(folder: Path) -> dict:
    """Read downloader config from insarhub_config.json (or legacy formats), else fallback downloader_config.json."""
    from insarhub.utils.config_io import read_insarhub_config
    data = read_insarhub_config(folder)
    cfg = data.get("downloader", {}).get("config", {})
    if cfg:
        return {k: v for k, v in cfg.items() if k not in _ROLE_CONFIG_STRIP_FIELDS}
    raw = _read_config_json(folder / "downloader_config.json")
    return {k: v for k, v in raw.items() if k not in _ROLE_CONFIG_STRIP_FIELDS}


def _load_pairs(args, workdir: Path) -> dict | list:
    """
    Return pairs as either:
      dict  – {"p100_f466": [["ref", "sec"], ...], ...}  (multi-group from select_pairs)
      list  – [["ref", "sec"], ...]                       (flat / inline)

    Resolution order:
      1. --pairs-file  (explicit file)
      2. --pairs       (inline on CLI)
      3. p*_f* subdirs containing stack_p*_f*.json  (auto, multi-group)
      4. workdir/pairs.json  (auto, single group)
    """
    if getattr(args, "pairs_file", None):
        return json.loads(Path(args.pairs_file).expanduser().resolve().read_text())
    if getattr(args, "pairs", None):
        result = []
        for item in args.pairs:
            parts = [x.strip() for x in item.split(",")]
            if len(parts) != 2:
                print(f"[ERROR] Invalid pair '{item}' — expected 'reference,secondary'",
                      file=sys.stderr)
                sys.exit(1)
            result.append(parts)
        return result
    # Auto-detect per-group subdirs created by `downloader --select-pairs`
    subdir_pairs: dict[str, list] = {}
    if workdir.is_dir():
        for subdir in sorted(workdir.iterdir()):
            if not subdir.is_dir():
                continue
            pf = _parse_group_key(subdir.name)
            if pf is None:
                continue
            # New format: stack_p{path}_f{frame}.json inside subdir
            stack = subdir / f"stack_{subdir.name}.json"
            if stack.is_file():
                data = json.loads(stack.read_text())
                subdir_pairs[subdir.name] = data.get("pairs", [])
                continue
            # Legacy format: pairs_{subdir.name}.json inside subdir
            pjson = subdir / f"pairs_{subdir.name}.json"
            if pjson.is_file():
                subdir_pairs[subdir.name] = json.loads(pjson.read_text())

        # Legacy: pairs_p*_f*.json flat in workdir
        for f in sorted(workdir.glob("pairs_p*_f*.json")):
            potential_key = f.stem[6:]
            if _parse_group_key(potential_key) and potential_key not in subdir_pairs:
                subdir_pairs[potential_key] = json.loads(f.read_text())

    if subdir_pairs:
        print(f"[pairs] Auto-loading {len(subdir_pairs)} group(s) from subdirs")
        return subdir_pairs

    # Fall back to any flat stack_p*_f*.json in workdir root (e.g. a merged
    # download's stack_p{path}_merged.json), then legacy pairs.json
    flat_stacks = sorted(workdir.glob("stack_p*_f*.json"))
    if len(flat_stacks) > 1:
        print(f"[pairs] Auto-loading {len(flat_stacks)} flat stack file(s) from {workdir}")
        return {
            f.stem.removeprefix("stack_"): json.loads(f.read_text()).get("pairs", [])
            for f in flat_stacks
        }
    for auto in (*flat_stacks, workdir / "pairs.json"):
        if auto.is_file():
            print(f"[pairs] Auto-loading {auto}")
            data = json.loads(auto.read_text())
            return data.get("pairs", []) if isinstance(data, dict) else data
    print(f"[ERROR] No pairs file found under current workdir {workdir}. Use --pairs-file, --pairs, or run "
          "'insarhub downloader --select-pairs' first.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Command handlers  (each one: build instance → call Command → check result)
# ---------------------------------------------------------------------------

def cmd_downloader(args, extra_args: list[str]):
    import dataclasses
    from insarhub import Downloader
    from insarhub.commands import SearchCommand, SummaryCommand, FootprintCommand, DownloadScenesCommand

    # --list-downloaders
    if args.list_downloaders:
        print("Available downloaders:")
        for name in Downloader.available():
            print(f"  {name}")
        return

    # Resolve downloader class
    if args.downloader_name not in Downloader._registry:
        print(f"[ERROR] Unknown downloader '{args.downloader_name}'. Use --list-downloaders.",
              file=sys.stderr)
        sys.exit(1)
    downloader_cls = Downloader._registry[args.downloader_name]

    # --pipeline  (no instantiation needed — reads registry directly)
    if args.pipeline:
        from insarhub.core.registry import Processor, Analyzer
        dl_name = args.downloader_name
        procs = [
            (n, c) for n, c in Processor._registry.items()
            if getattr(c, 'compatible_downloader', None) in (None, 'all', dl_name)
        ]
        lines = [dl_name]
        for pi, (pname, _) in enumerate(procs):
            last_proc = pi == len(procs) - 1
            proc_prefix = '└─' if last_proc else '├─'
            proc_indent = '   ' if last_proc else '│  '
            lines.append(f"{proc_prefix} {pname}")
            from insarhub.core.base import _compatible_processor
            anals = [
                n for n, c in Analyzer._registry.items()
                if _compatible_processor(getattr(c, 'compatible_processor', None), pname)
            ]
            for ai, aname in enumerate(anals):
                anal_prefix = '└─' if ai == len(anals) - 1 else '├─'
                lines.append(f"{proc_indent}{anal_prefix} {aname}")
        if len(lines) == 1:
            lines.append('└─ (no compatible processors registered)')
        print('\n'.join(lines))
        return

    # --list-options
    if args.list_options:
        config_cls = getattr(downloader_cls, "default_config", None)
        if config_cls is None:
            print(f"Downloader '{args.downloader_name}' has no config class.")
        else:
            workdir = _resolve_workdir(args.workdir)
            _cfg = args.config if (args.config and args.config != "__default__") else None
            if _cfg:
                cfg_path = Path(_cfg).expanduser().resolve()
            elif args.config == "__default__":
                _direct = workdir / "downloader_config.json"
                cfg_path = _direct if _direct.exists() else _find_subfolder_config(workdir, "downloader_config.json")
            else:
                cfg_path = None
            values = (_read_dl_config_from_folder(cfg_path.parent) or _read_config_json(cfg_path)) if cfg_path else {}
            if not values:
                print(f"[INFO] No saved config found. Showing defaults.")
            _print_config_options(config_cls,
                                  display_label=f"{args.downloader_name} downloader",
                                  skip_fields=_SEARCH_SKIP_FIELDS,
                                  value_overrides=values if values else None)
        return

    # Resolve workdir early so saved config can serve as base defaults
    workdir = _resolve_workdir(args.workdir)
    _cfg = args.config if (args.config and args.config != "__default__") else None
    _default_cfg_requested = (args.config == "__default__")
    if _cfg:
        cfg_path = Path(_cfg).expanduser().resolve()
        saved_cfg = _read_dl_config_from_folder(cfg_path.parent) or _read_config_json(cfg_path)
    elif _default_cfg_requested:
        # --config with no value: prefer insarhub_config.json (new format), fall back to
        # downloader_config.json (legacy), check workdir then p*_f* subdirs
        saved_cfg = _read_dl_config_from_folder(workdir)
        if not saved_cfg:
            subdir_cfg = _find_subfolder_config(workdir, "insarhub_config.json") or \
                         _find_subfolder_config(workdir, "downloader_config.json")
            if subdir_cfg:
                saved_cfg = _read_dl_config_from_folder(subdir_cfg.parent) or _read_config_json(subdir_cfg)
        if not saved_cfg:
            print(
                f"[ERROR] --config specified but no insarhub_config.json found in {workdir}",
                file=sys.stderr,
            )
            sys.exit(1)
        cfg_path = workdir / "insarhub_config.json"
    else:
        cfg_path = None
        saved_cfg = {}
    if saved_cfg:
        print(f"[INFO] Loaded saved config from {cfg_path or workdir}")

    # Parse extra_args as downloader config overrides; explicit CLI args override saved config
    overrides: dict = dict(saved_cfg)
    config_cls = getattr(downloader_cls, "default_config", None)
    _apply_config_overrides(overrides, config_cls, extra_args,
                            skip_fields=_SEARCH_SKIP_FIELDS, label=args.downloader_name)

    if args.AOI:
        from insarhub.utils.tool import _to_wkt
        aoi_input = args.AOI
        if len(aoi_input) == 4:
            try:
                aoi_input = [float(x) for x in aoi_input]
            except ValueError:
                pass  # not floats — treat as single-string WKT or path
        if isinstance(aoi_input, list) and len(aoi_input) == 1:
            aoi_input = aoi_input[0]
        overrides["intersectsWith"] = _to_wkt(aoi_input)

    # The second half of a --stacks token is a FRAME NUMBER only for frame-based
    # datasets. SLC-BURST products carry no frameNumber at all, so burst stacks key
    # on fullBurstID ("124_264305_IW2") and the selector is a string. Keep
    # non-numeric selectors as strings and let the downloader say what they mean
    # (_stack_key_matches); coercing both halves to int here silently matched
    # nothing for every burst search.
    _non_search = getattr(downloader_cls, "_NON_SEARCH_FIELDS", frozenset())
    _sel_label  = getattr(downloader_cls, "stack_key_label", "frame").upper()

    stacks_filter: list[tuple] | None = None
    if args.stacks:
        parsed: list[tuple] = []
        for token in args.stacks:
            parts = [p.strip() for p in token.split(":")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                print(f"[ERROR] Invalid --stacks token '{token}' — expected PATH:{_sel_label}",
                      file=sys.stderr)
                sys.exit(1)
            path_tok, sel_tok = parts
            if not path_tok.isdigit():
                print(f"[ERROR] --stacks PATH must be an integer, got '{token}'", file=sys.stderr)
                sys.exit(1)
            parsed.append((int(path_tok), int(sel_tok) if sel_tok.isdigit() else sel_tok))
        # Broad search to reduce API response; exact-pair filter applied after search
        overrides["relativeOrbit"] = list(dict.fromkeys(p for p, _ in parsed))
        # Narrow the query by frame only when every selector really is a frame number
        # AND this downloader queries on frame at all -- a burst ID reaching
        # asf.search(frame=...) dies in its int-range validator.
        _frames = [f for _, f in parsed if isinstance(f, int)]
        if "frame" not in _non_search and len(_frames) == len(parsed):
            overrides["frame"] = list(dict.fromkeys(_frames))
        stacks_filter = parsed
    else:
        # Reconstruct stacks_filter from saved config lists so the exact-pair filter
        # is re-applied on reload (prevents ASF returning all cross-combinations)
        _ro = overrides.get("relativeOrbit")
        _fr = overrides.get("frame")
        if (isinstance(_ro, list) and isinstance(_fr, list)
                and len(_ro) == len(_fr) and len(_ro) > 0):
            def _as_selector(x):
                """Saved frames are ints for frame datasets, burst-ID strings otherwise."""
                try:
                    return int(x)
                except (TypeError, ValueError):
                    return str(x)
            try:
                stacks_filter = list(zip([int(x) for x in _ro],
                                         [_as_selector(x) for x in _fr]))
            except (TypeError, ValueError):
                # A config we cannot read as path/selector pairs is not worth
                # crashing over -- fall through to an unfiltered search.
                stacks_filter = None

    overrides["workdir"] = workdir
    if getattr(args, "no_verify_ssl", False):
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        overrides["ssl_verify"] = False

    downloader = Downloader.create(args.downloader_name,
                                   **{k: v for k, v in overrides.items()
                                      if k not in ("name", "config")})

    result = SearchCommand(downloader).run()
    _fail(result, "search")

    if stacks_filter:
        from insarhub.commands import FilterCommand
        available = list((result.data or {}).keys())
        _fail(FilterCommand(downloader, {"path_frame": stacks_filter}).run(), "filter")
        if not downloader.active_results:
            # An explicit stack selection that matches nothing is a typo or a stale
            # config, not a reason to carry on with every stack the search returned.
            print("[ERROR] --stacks matched no stacks in the search results.", file=sys.stderr)
            print("        requested: "
                  + " ".join(f"{p}:{s}" for p, s in stacks_filter), file=sys.stderr)
            print("        available: "
                  + (" ".join(f"{k[0]}:{k[1]}" for k in available) or "(none)"), file=sys.stderr)
            sys.exit(1)

    SummaryCommand(downloader).run()

    if args.footprint:
        FootprintCommand(downloader, save_path=args.footprint).run()

    if args.select_pairs:
        merge_flag = getattr(args, "merge", False)
        pairs, _baselines, _scene_bperp, _prefetch_cache, quality_scores, _quality_factors = downloader.select_pairs(
            dt_targets=tuple(args.dt_targets),
            dt_tol=args.dt_tol,
            dt_max=args.dt_max,
            pb_max=args.pb_max,
            min_degree=args.min_degree,
            max_degree=args.max_degree,
            force_connect=args.force_connect,
            max_workers=args.sp_workers,
            avoid_low_quality_days=args.avoid_low_quality_days,
            snow_threshold=args.snow_threshold,
            precip_mm_threshold=args.precip_mm_threshold,
            merge=merge_flag,
        )

        # Summary only — select_pairs() already wrote stack files, scored the
        # pairs, and saved the network plot(s).
        dl_workdir = downloader.config.workdir
        _sp = StackPaths(dl_workdir)
        _dl_is_stack = (dl_workdir / "insarhub_config.json").exists()
        if isinstance(pairs, dict):
            for (path, frame), group_pairs in pairs.items():
                tag = _sp.dir_for(path, frame).name
                subdir = dl_workdir if _dl_is_stack else dl_workdir / tag
                stack_path = subdir / _sp.stack_file_for(path, frame).name
                n_scored = len((quality_scores or {}).get((path, frame), {}))
                print(f"[quality] {n_scored} selected pairs scored")
                print(f"[pairs] {tag}: {len(group_pairs)} pairs → {stack_path}")
        else:
            stack_path = dl_workdir / _sp.stack_file(0, 0).name
            print(f"[quality] {len(quality_scores or {})} selected pairs scored")
            print(f"[pairs] Saved {len(pairs)} pairs → {stack_path}")

    if args.download:
        orbit_dir = args.orbit_files if isinstance(args.orbit_files, str) else None
        merge = getattr(args, "merge", False)

        if merge:
            # Must match the merge directory asf_base.download(merge=True) actually
            # writes SLCs to (StackPaths.merge_dir) — a bare "merged" dir here would
            # save orbit files to a different, empty folder than the SLCs landed in.
            _paths = {p for (p, _f) in downloader.active_results.keys()}
            _frames = [f for (_p, f) in downloader.active_results.keys()]
            merged_dir = StackPaths(workdir).merge_dir(next(iter(_paths)), _frames)
            print(f"[merge] Downloading all stacks → {merged_dir}/slc/")
            dl_kwargs: dict = {"max_workers": args.worker, "merge": True}
            result = DownloadScenesCommand(downloader, **dl_kwargs).run()
            _fail(result, "download")
            if args.orbit_files and hasattr(downloader, "download_orbit"):
                print(f"[merge] Downloading orbit files → {merged_dir.name}/slc/")
                downloader.download_orbit(save_dir=str(merged_dir))
        else:
            dl_kwargs = {"max_workers": args.worker}
            orbit_handled = False
            if hasattr(downloader, "download") and "download_orbit" in downloader.download.__code__.co_varnames:
                dl_kwargs["download_orbit"] = bool(args.orbit_files)
                orbit_handled = bool(args.orbit_files)
            result = DownloadScenesCommand(downloader, **dl_kwargs).run()
            _fail(result, "download")
            if args.orbit_files and not orbit_handled and hasattr(downloader, "download_orbit"):
                downloader.download_orbit(save_dir=orbit_dir)
    elif args.orbit_files:
        orbit_dir = args.orbit_files if isinstance(args.orbit_files, str) else None
        if hasattr(downloader, "download_orbit"):
            downloader.download_orbit(save_dir=orbit_dir)
        else:
            print("[WARNING] This downloader does not support orbit file download.", file=sys.stderr)


def cmd_processor(args, extra_args: list[str]):
    from insarhub import Processor
    from insarhub.core.base import CloudProcessor, LocalProcessor

    # --list-processors (works without a sub-action)
    if getattr(args, "list_processors", False):
        print("Available processors:")
        for name in Processor.available():
            print(f"  {name}")
        return

    processor_name = getattr(args, "processor_name", "Hyp3_S1")

    if processor_name not in Processor._registry:
        print(f"[ERROR] Unknown processor '{processor_name}'. Use --list-processors.",
              file=sys.stderr)
        sys.exit(1)
    processor_cls = Processor._registry[processor_name]
    is_hyp3  = issubclass(processor_cls, CloudProcessor)
    is_local = issubclass(processor_cls, LocalProcessor)

    # --list-options (no action required)
    if getattr(args, "list_options", False):
        config_cls = getattr(processor_cls, "default_config", None)
        if config_cls is None:
            print(f"Processor '{processor_name}' has no config class.")
        else:
            workdir = _resolve_workdir(getattr(args, "workdir", None))
            _cfg = args.config if (args.config and args.config != "__default__") else None
            if _cfg:
                cfg_path = Path(_cfg).expanduser().resolve()
            elif args.config == "__default__":
                _direct = workdir / "processor_config.json"
                cfg_path = _direct if _direct.exists() else _find_subfolder_config(workdir, "processor_config.json")
            else:
                cfg_path = None
            values = (_read_proc_config_from_folder(cfg_path.parent) or _read_config_json(cfg_path)) if cfg_path else {}
            if not values:
                print(f"[INFO] No saved config found. Showing defaults.")
            _print_config_options(config_cls,
                                  display_label=f"{processor_name} processor",
                                  skip_fields=_SUBMIT_SKIP_FIELDS,
                                  value_overrides=values if values else None)
        return

    action = getattr(args, "proc_action", None)

    if is_hyp3:
        _HYPO_ACTIONS = {"submit", "refresh", "download", "retry", "watch", "credits"}
        if action == "submit":
            _proc_submit(args, extra_args)
        elif action == "refresh":
            _proc_refresh(args)
        elif action == "download":
            _proc_download_results(args)
        elif action == "retry":
            _proc_retry(args)
        elif action == "watch":
            _proc_watch(args)
        elif action == "credits":
            _proc_credits(args)
        # else: no action → help shown by main()

    elif is_local:
        _LOCAL_ACTIONS = {"submit", "refresh", "retry", "watch", "cancel", "run-stage-unit"}
        if action == "submit":
            _proc_local_submit(args, extra_args)
        elif action == "refresh":
            _proc_local_refresh(args)
        elif action == "retry":
            _proc_local_retry(args)
        elif action == "cancel":
            _proc_local_cancel(args)
        elif action == "watch":
            _proc_local_watch(args)
        elif action == "run-stage-unit":
            _proc_run_stage_unit(args)
        # else: no action → help shown by main()

    else:
        print(f"[ERROR] Processor '{processor_name}' has unknown type "
              f"(not HyP3 or local). Cannot determine available actions.",
              file=sys.stderr)
        sys.exit(1)


def _proc_submit(args, extra_args: list[str]):
    import dataclasses
    from insarhub import Processor
    from insarhub.commands import SubmitCommand, SaveJobsCommand

    processor_name = getattr(args, "processor_name", "Hyp3_S1")
    processor_cls = Processor._registry[processor_name]

    # Resolve workdir early so saved config can serve as base defaults
    workdir = _resolve_workdir(args.workdir)
    _cfg = args.config if (args.config and args.config != "__default__") else None
    _default_cfg_requested = (args.config == "__default__")
    if _cfg:
        cfg_path = Path(_cfg).expanduser().resolve()
        saved_cfg = _read_proc_config_from_folder(cfg_path.parent) or _read_config_json(cfg_path)
    elif _default_cfg_requested:
        # --config with no value: prefer insarhub_config.json (new format), fall back to
        # processor_config.json (legacy), check workdir then p*_f* subdirs
        saved_cfg = _read_proc_config_from_folder(workdir)
        if not saved_cfg:
            subdir_cfg = _find_subfolder_config(workdir, "insarhub_config.json") or \
                         _find_subfolder_config(workdir, "processor_config.json")
            if subdir_cfg:
                saved_cfg = _read_proc_config_from_folder(subdir_cfg.parent) or _read_config_json(subdir_cfg)
        if not saved_cfg:
            print(
                f"[ERROR] --config specified but no insarhub_config.json found in {workdir}",
                file=sys.stderr,
            )
            sys.exit(1)
        cfg_path = workdir / "insarhub_config.json"
    else:
        cfg_path = None
        saved_cfg = {}
    if saved_cfg:
        print(f"[INFO] Loaded saved config from {cfg_path or workdir}")

    # Parse extra_args as processor config overrides; explicit CLI args override saved config
    # Strip metadata keys and runtime-only flags that must not carry over from saved state
    overrides: dict = {k: v for k, v in saved_cfg.items()
                       if k not in _SAVED_CFG_SKIP and k not in ("processor_type",)}
    config_cls = getattr(processor_cls, "default_config", None)
    _apply_config_overrides(overrides, config_cls, extra_args,
                            skip_fields=_SUBMIT_SKIP_FIELDS, label=processor_name)

    overrides["name_prefix"] = args.name_prefix
    if getattr(args, "worker", None) is not None:
        overrides["max_workers"] = args.worker

    pool = _load_credential_pool(getattr(args, "credential_pool", None))
    if pool:
        overrides["earthdata_credentials_pool"] = pool

    dry_run = getattr(args, "dry_run", False)

    # On dry-run: preview submission — find every process directory at workdir level
    # and one level of subdirectories, write processor config into insarhub_config.json.
    if dry_run:
        from insarhub.utils.config_io import write_insarhub_config
        _skip_write = _SUBMIT_SKIP_FIELDS | {"earthdata_credentials_pool", "workdir", "pairs"}
        _preview_overrides = {k: v for k, v in overrides.items()
                              if k not in _SUBMIT_SKIP_FIELDS | {"name", "config"}}
        # Carry dry_run into the preview processor so its __init__ knows not to
        # stamp the folder (Hyp3Base writes a workflow marker on construction).
        _preview_overrides["dry_run"] = True

        def _is_process_dir(d: Path) -> bool:
            return (d / "insarhub_config.json").exists() or (d / "downloader_config.json").exists()

        def _stamp_process_dir(d: Path) -> None:
            try:
                _preview_proc = Processor.create(processor_name, workdir=d,
                                                 pairs=[], **_preview_overrides)
                proc_cfg = {f.name: getattr(_preview_proc.config, f.name)
                            for f in dataclasses.fields(_preview_proc.config)
                            if f.name not in _skip_write}
                # Report only. This used to WRITE the resolved config into every
                # process dir, which made --dry-run permanently reconfigure a
                # stack: any flag being trialled was persisted, and every later
                # run inherited it. Resolving and showing the config is the
                # useful half; committing it is what submit() is for.
                print(f"[dry-run] {d}")
                print(f"[dry-run]   would write insarhub_config.json  "
                      f"processor={processor_name}  ({len(proc_cfg)} field(s))")
            except Exception as e:
                print(f"[dry-run] Could not update {d}: {e}", file=sys.stderr)

        targets = []
        if _is_process_dir(workdir):
            targets.append(workdir)
        if workdir.is_dir():
            for sub in sorted(workdir.iterdir()):
                if sub.is_dir() and _is_process_dir(sub):
                    targets.append(sub)

        if targets:
            for t in targets:
                _stamp_process_dir(t)
        else:
            _checked = [workdir] + ([sub for sub in sorted(workdir.iterdir()) if sub.is_dir()] if workdir.is_dir() else [])
            for _d in _checked:
                if not (_d / "insarhub_config.json").exists() and not (_d / "downloader_config.json").exists():
                    print(f"[dry-run] insarhub_config.json is missing for {_d}", file=sys.stderr)
                elif not (_d / "insarhub_config.json").exists():
                    print(f"[dry-run] insarhub_config.json is missing for {_d}", file=sys.stderr)
            if not _checked:
                print(f"[dry-run] No directories found under {workdir}", file=sys.stderr)

    pairs_data = _load_pairs(args, workdir)

    groups: dict[tuple[int, int] | None, list] = (
        {_parse_group_key(k): [tuple(p) for p in v] for k, v in pairs_data.items()}
        if isinstance(pairs_data, dict)
        else {None: [tuple(p) for p in pairs_data]}
    )
    if dry_run:
        print(f"[dry-run] Processor : {processor_name}")
        print(f"[dry-run] Workdir   : {workdir}")
        print(f"[dry-run] Groups    : {len(groups)}")

    for pf, group_pairs in groups.items():
        folder = f"p{pf[0]}_f{pf[1]}" if pf else None
        # Avoid nesting if workdir is already the target group folder
        if folder and workdir.name == folder:
            job_dir = workdir
        else:
            job_dir = workdir / folder if folder else workdir
        group_prefix = (f"{args.name_prefix}_p{pf[0]}_f{pf[1]}"
                        if pf else args.name_prefix)
        tag = f"[{folder}] " if folder else ""
        job_dir.mkdir(parents=True, exist_ok=True)
        group_overrides = {k: v for k, v in overrides.items()
                           if k not in ("name", "config")}
        group_overrides.update({"workdir": job_dir, "pairs": group_pairs,
                                 "name_prefix": group_prefix})
        # Hyp3Base stamps the folder from __init__, so the flag has to reach
        # the processor being constructed -- not just this function's local
        # `dry_run` -- or building it writes insarhub_config.json.
        group_overrides["dry_run"] = dry_run
        processor = Processor.create(processor_name, **group_overrides)
        # Write full resolved config into insarhub_config.json (consistent with
        # GUI) -- but NOT on a dry run, which previously fell through to here
        # and wrote one per job dir before deciding it had nothing to submit.
        if not dry_run:
            _skip_write = _SUBMIT_SKIP_FIELDS | {"earthdata_credentials_pool",
                                                 "workdir", "pairs"}
            from insarhub.utils.config_io import write_insarhub_config as _wic
            _wic(job_dir, {"processor": {"type": processor_name,
                                         "config": {f.name: getattr(processor.config, f.name)
                                                    for f in dataclasses.fields(processor.config)
                                                    if f.name not in _skip_write}}})
        if dry_run:
            print(f"\n{tag}Would submit {len(group_pairs)} pairs → {job_dir}")
            print(f"{tag}  name_prefix : {group_prefix}")
            for ref, sec in group_pairs:
                print(f"{tag}  {ref}  ↔  {sec}")
            continue
        print(f"{tag}Submitting {len(group_pairs)} pairs → {job_dir}")
        result = SubmitCommand(processor).run()
        _fail(result, f"submit {folder or ''}".strip())
        SaveJobsCommand(processor).run()


def _proc_refresh(args):
    from insarhub.commands import RefreshCommand
    processor_name = getattr(args, "processor_name", "Hyp3_S1")
    workdir = _resolve_workdir(args.workdir)
    for job_dir, jf, tag in _iter_job_entries(workdir, args):
        print(f"{tag}Refreshing…")
        processor = _load_hyp3_processor(job_dir, job_file=jf, processor_name=processor_name)
        _fail(RefreshCommand(processor).run(), f"refresh {tag}".strip())


def _proc_download_results(args):
    from insarhub.commands import RefreshCommand, DownloadResultsCommand
    processor_name = getattr(args, "processor_name", "Hyp3_S1")
    workdir = _resolve_workdir(args.workdir)
    for job_dir, jf, tag in _iter_job_entries(workdir, args):
        print(f"{tag}Downloading results…")
        processor = _load_hyp3_processor(job_dir, job_file=jf, processor_name=processor_name,
                                         max_workers=getattr(args, "worker", None))
        RefreshCommand(processor).run()
        _fail(DownloadResultsCommand(processor).run(), f"download {tag}".strip())


def _proc_retry(args):
    from insarhub.commands import RetryCommand
    processor_name = getattr(args, "processor_name", "Hyp3_S1")
    workdir = _resolve_workdir(args.workdir)
    for job_dir, jf, tag in _iter_job_entries(workdir, args):
        print(f"{tag}Retrying failed jobs…")
        processor = _load_hyp3_processor(job_dir, job_file=jf, processor_name=processor_name)
        _fail(RetryCommand(processor).run(), f"retry {tag}".strip())


def _proc_watch(args):
    import io
    import time
    from contextlib import redirect_stdout, redirect_stderr
    from tqdm import tqdm
    from hyp3_sdk import Batch as HyP3Batch

    workdir = _resolve_workdir(args.workdir)
    # Build (job_dir, job_file, processor) for every job file across all dirs
    from insarhub.processor.hyp3_base import Hyp3Base
    entries: list[tuple[Path, Path, Hyp3Base]] = []
    processor_name = getattr(args, "processor_name", "Hyp3_S1")
    for job_dir, jf, _tag in _iter_job_entries(workdir, args):
        entries.append((job_dir, jf, _load_hyp3_processor(job_dir, job_file=jf,
                                                           processor_name=processor_name,
                                                           max_workers=getattr(args, "worker", None))))

    downloaded: dict[Path, set] = {jf: set() for _, jf, _ in entries}

    def _bar_label(job_dir: Path, jf: Path) -> str:
        prefix = job_dir.name if job_dir != workdir else ""
        return f"[{prefix}/{jf.name}]" if prefix else f"[{jf.name}]"

    bars = [
        tqdm(
            total=0,
            desc=_bar_label(d, jf),
            position=i,
            leave=True,
            bar_format="{desc}: {postfix}",
            file=sys.stderr,
        )
        for i, (d, jf, _) in enumerate(entries)
    ]
    for bar in bars:
        bar.set_postfix_str("waiting for first refresh…")

    tqdm.write(f"Watching {len(entries)} job file(s), refreshing every {args.interval}s. Ctrl+C to stop.")

    try:
        while True:
            done_count = 0
            for i, (job_dir, jf, processor) in enumerate(entries):
                sink = io.StringIO()
                with redirect_stdout(sink), redirect_stderr(sink):
                    processor.refresh()
                # refresh() swallows per-user errors internally (prints and
                # continues) so a broken credential/auth pool entry silently
                # leaves processor.batchs empty forever, looking like a
                # frozen 0/0 progress bar with no explanation. Surface just
                # those failure lines — everything else refresh() prints
                # (the noisy per-job status table) stays suppressed.
                for line in sink.getvalue().splitlines():
                    if "Failed to refresh" in line:
                        with tqdm.external_write_mode(file=sys.stderr):
                            tqdm.write(f"{_bar_label(job_dir, jf)} {line.strip()}")

                total = active = failed = succeeded = 0
                new_succeeded: dict = {}
                for username, batch in processor.batchs.items():
                    total += len(batch)
                    active += len(batch.filter_jobs(
                        running=True, pending=True, succeeded=False, failed=False))
                    failed += len(batch.filter_jobs(
                        running=False, pending=False, succeeded=False, failed=True))
                    succ = batch.filter_jobs(
                        running=False, pending=False, succeeded=True, failed=False)
                    succeeded += len(succ)
                    new = [j for j in succ if j.job_id not in downloaded[jf]]
                    if new:
                        new_succeeded[username] = new

                ts = time.strftime("%H:%M:%S")
                bars[i].set_postfix_str(
                    f"[{ts}] {succeeded}/{total} Done | {active} Running | {failed} Failed"
                )

                if new_succeeded:
                    label = _bar_label(job_dir, jf)
                    n = sum(len(v) for v in new_succeeded.values())
                    old_batchs = processor.batchs
                    processor.batchs = {u: HyP3Batch(jobs) for u, jobs in new_succeeded.items()}
                    with tqdm.external_write_mode(file=sys.stderr):
                        print(f"{label} {n} job(s) succeeded — downloading…")
                    # download() spawns its own worker threads, each opening a
                    # per-file tqdm progress bar — those need tqdm's global
                    # write lock too. external_write_mode() holds that same
                    # lock for its whole `with` block, so calling download()
                    # from inside it deadlocks: the main thread holds the
                    # lock waiting for the workers to finish, while every
                    # worker blocks trying to acquire that same lock just to
                    # create/update its own bar. Call it after the lock is
                    # released instead.
                    _out_dir, dl_results = processor.download()
                    processor.batchs = old_batchs
                    # Only mark job IDs as handled once download() actually
                    # reported no failures for this round — marking them
                    # beforehand meant a transient download failure (network
                    # blip, disk error) permanently skipped that job on every
                    # later watch iteration, even though its ZIP never landed.
                    if dl_results.get("failed", 0) == 0:
                        for jobs in new_succeeded.values():
                            for j in jobs:
                                downloaded[jf].add(j.job_id)
                    else:
                        with tqdm.external_write_mode(file=sys.stderr):
                            tqdm.write(f"{label} {dl_results['failed']} download(s) failed "
                                       f"— will retry next refresh.")

                if total > 0 and active == 0:
                    done_count += 1

            if done_count == len(entries):
                tqdm.write("All groups complete.")
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        tqdm.write("\nStopped by user.")
    finally:
        for bar in bars:
            bar.close()


def _proc_credits(args):
    from insarhub.commands import CheckCreditsCommand
    processor_name = getattr(args, "processor_name", "Hyp3_S1")
    workdir = _resolve_workdir(args.workdir)
    # credits is per-credential-pool, not per job_dir — run once
    processor = _load_hyp3_processor(workdir, credential_pool_path=args.credential_pool,
                                      processor_name=processor_name)
    CheckCreditsCommand(processor).run()


def _proc_local_submit(args, extra_args: list[str]):
    import dataclasses
    from insarhub import Processor
    from insarhub.utils.config_io import write_insarhub_config as _wic

    processor_name = getattr(args, "processor_name", "ISCE2_S1")
    processor_cls  = Processor._registry[processor_name]
    workdir        = _resolve_workdir(args.workdir)

    # ── Load saved config (same resolution order as Hyp3) ─────────────────
    _cfg = args.config if (args.config and args.config != "__default__") else None
    _default_cfg_requested = (args.config == "__default__")
    if _cfg:
        _cfg_path = Path(_cfg).expanduser().resolve()
        saved_cfg = _read_proc_config_from_folder(_cfg_path.parent) or _read_config_json(_cfg_path)
        print(f"[INFO] Loaded config from {_cfg}")
    elif _default_cfg_requested:
        # --config with no value: prefer insarhub_config.json (new format), fall back to
        # processor_config.json (legacy), check workdir then p*_f* subdirs
        saved_cfg = _read_proc_config_from_folder(workdir)
        if not saved_cfg:
            subdir_cfg = _find_subfolder_config(workdir, "insarhub_config.json") or \
                         _find_subfolder_config(workdir, "processor_config.json")
            if subdir_cfg:
                saved_cfg = _read_proc_config_from_folder(subdir_cfg.parent) or _read_config_json(subdir_cfg)
        if not saved_cfg:
            print(f"[ERROR] --config specified but no insarhub_config.json found in {workdir}",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[INFO] Loaded saved config from {workdir / 'insarhub_config.json'}")
    else:
        saved_cfg = _read_proc_config_from_folder(workdir)  # silent fallback

    # ── Parse extra CLI flags as config overrides ──────────────────────────
    config_cls = getattr(processor_cls, "default_config", None)
    overrides: dict = {k: v for k, v in saved_cfg.items() if k not in _SAVED_CFG_SKIP}
    _apply_config_overrides(overrides, config_cls, extra_args,
                            skip_fields=_SUBMIT_SKIP_FIELDS, label=processor_name)

    # Dedicated --container flag (nargs="?") wins over any --container VALUE
    # that rode in via extra_args. Bare --container resolves to the processor's
    # default image; absent leaves the saved value untouched.
    if getattr(args, "container", None) is not None:
        overrides["container"] = _resolve_container_arg(processor_name, args.container)

    overrides["workdir"] = str(workdir)

    # --worker is the single parallelism knob and sets BOTH axes, because which
    # one governs a given stage is an implementation detail the user should not
    # have to track. A stage that fans out becomes N concurrent SLURM jobs; a
    # stage that cannot (ISCE3_Burst's ifg is one process -- phase linking needs
    # the whole covariance) instead runs N threads inside its single job.
    #
    # Previously --worker set only max_concurrent_hpc under --hpc-mode, so for a
    # single-job stage it did nothing at all: `--worker 16` left max_workers at
    # its saved value (3) while SLURM reserved whatever cpus_per_task said, and
    # the cores sat idle with no indication why. There was no CLI route to
    # max_workers in HPC mode at all -- it had to be hand-edited into the
    # config file.
    #
    # An explicit --max_workers / --max_concurrent_hpc still wins. It has to be
    # detected from the raw args, not from `overrides`: that dict is already
    # seeded from the saved config, so both keys are normally present and
    # nothing there distinguishes "the user typed it" from "it was loaded".
    if getattr(args, "worker", None) is not None:
        def _typed(field: str) -> bool:
            flags = (f"--{field}", "--" + field.replace("_", "-"))
            return any(a == f or a.startswith(f + "=")
                       for a in (extra_args or []) for f in flags)

        if overrides.get("hpc_mode", False):
            # HPC: --worker is CONCURRENT JOBS only. Threads inside each child
            # are derived per stage from that stage's cpus_per_task in
            # sbatch_options.json (see _submit_hpc), because a single global
            # max_workers cannot match stages that reserve different amounts --
            # 2 for stitch/filt, 4 for unwrap, 8 for ifg. Setting it here would
            # be wrong for all but one of them.
            if not _typed("max_concurrent_hpc"):
                overrides["max_concurrent_hpc"] = args.worker
        elif not _typed("max_workers"):
            # Local: there are no jobs, so --worker is the thread count.
            overrides["max_workers"] = args.worker

    # --dry-run is registered in the HyP3 submit subparser so argparse consumes
    # it into args.dry_run before extra_args is built — pull it back in here.
    if getattr(args, "dry_run", False):
        overrides["dry_run"] = True

    # ── Load pairs (same helpers as Hyp3) ──────────────────────────────────
    # Stack-download workflows (builds_own_network, e.g. ISCE3_Burst /
    # ISCE3_NISAR) derive their own interferogram network from the downloaded
    # products in slc/ and take no pairs. Mirrors the GUI route's
    # _needs_stack_file so CLI and processor agree.
    _builds_own = getattr(processor_cls, "builds_own_network", False)
    _needs_pairs = not _builds_own

    pairs: list[tuple] = []
    if _needs_pairs:
        pairs_data = _load_pairs(args, workdir)
        raw_pairs  = pairs_data if isinstance(pairs_data, list) else next(iter(pairs_data.values()), [])
        # Preserve full arity -- ISCE2_S1/Hyp3_S1 use 2-tuples (ref, sec), but
        # GMTSAR_S1 requires 4-tuples (ref_safe, ref_eof, sec_safe, sec_eof).
        # Used to hardcode (p[0], p[1]), silently truncating GMTSAR_S1 pairs.
        pairs      = [tuple(str(x) for x in p) for p in raw_pairs]
        if processor_name == "GMTSAR_S1" and pairs and len(pairs[0]) == 2:
            # Downloader output (--select-pairs) is bare ASF scene name
            # 2-tuples -- no .SAFE suffix, no .EOF orbit filename. Expand into
            # GMTSAR_S1's required 4-tuples instead of letting __init__ reject
            # them outright (it requires exactly 4-tuples).
            from insarhub.processor.gmtsar_s1 import pairs_from_downloader
            slc_dir_val   = overrides.get("slc_dir") or str(workdir)
            orbit_dir_val = overrides.get("orbit_dir") or slc_dir_val
            pairs = pairs_from_downloader(pairs, slc_dir=slc_dir_val, orbit_dir=orbit_dir_val)
        if not pairs:
            print("[ERROR] No pairs found. Use --pairs-file or place stack_*.json in workdir.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"  Loaded {len(pairs)} pair(s)")
    else:
        _glob = getattr(processor_cls, "input_glob", "*.SAFE")
        print(f"[INFO] {processor_name}: interferogram network derived from "
              f"slc/{_glob}; no pairs file needed")

    # ── Auto-load sbatch_options.json if hpc_mode ─────────────────────────────
    if overrides.get("hpc_mode"):
        from insarhub.processor.isce2_base import load_or_init_sbatch_options
        # Each processor's stage set is its own: ISCE2 numbers steps 01..17,
        # GMTSAR uses align/topo/intf/merge, ISCE3_Burst uses dem..unwrap.
        # Falling through to ISCE2's template for anything unrecognised wrote a
        # file full of numbered entries that never match a real stage, so every
        # stage silently fell back to "default" resources.
        tmpl = getattr(processor_cls, "SBATCH_DEFAULT_TEMPLATE", None)
        if not tmpl and processor_name == "GMTSAR_S1":
            from insarhub.processor.gmtsar_s1 import _GMTSAR_SBATCH_DEFAULT_TEMPLATE
            tmpl = _GMTSAR_SBATCH_DEFAULT_TEMPLATE
        per_step = (load_or_init_sbatch_options(workdir, default_template=tmpl)
                    if tmpl else load_or_init_sbatch_options(workdir))
        if per_step is None:
            print(
                f"  Then rerun:\n\n"
                f"    insarhub processor -N {processor_name} -w {workdir} --hpc-mode submit\n"
            )
            sys.exit(0)
        overrides["sbatch_options_per_step"] = per_step
        print(f"[INFO] Loaded sbatch options from {workdir / 'sbatch_options.json'}")

    # ── Build config + processor ───────────────────────────────────────────
    valid_keys  = {f.name for f in dataclasses.fields(config_cls)} if config_cls else set()
    init_kwargs = {k: v for k, v in overrides.items() if k in valid_keys}
    cfg         = config_cls(**init_kwargs)
    processor   = processor_cls(pairs=pairs, config=cfg)

    # ── Persist resolved config to insarhub_config.json (mirrors Hyp3) ────
    # sbatch_options_per_step is excluded: sbatch_options.json is the source of truth
    # container IS persisted now (not in _RUNTIME_ONLY_FIELDS) so retry/refresh
    # re-run inside the same image.
    #
    # NOT under --dry-run. This write happens before submit(), so a dry run was
    # permanently rewriting the folder's saved config even though it submitted
    # nothing -- every LATER run then inherited whatever the dry run was
    # exploring. That is exactly how a `--process-full-extent` flag test left
    # process_full_extent=true in a stack's config, silently switching the
    # processing extent from the user's AOI to the full burst footprint for
    # every subsequent run. A dry run must be inspectable without side effects.
    if getattr(cfg, "dry_run", False):
        print("[INFO] dry run: leaving insarhub_config.json unchanged")
    else:
        _skip_write = (_SUBMIT_SKIP_FIELDS | {"workdir", "sbatch_options_per_step"}
                       | _RUNTIME_ONLY_FIELDS)
        _wic(workdir, {"processor": {"type": processor_name,
                                     "config": {f.name: getattr(cfg, f.name)
                                                for f in dataclasses.fields(cfg)
                                                if f.name not in _skip_write}}})

    submit_kwargs = {}
    if getattr(args, "step", None):
        submit_kwargs["steps"] = args.step
    processor.submit(**submit_kwargs)


def _proc_local_refresh(args):
    processor_name = getattr(args, "processor_name", "ISCE2_S1")
    workdir        = _resolve_workdir(args.workdir)
    jobs_pattern, jobs_subdir = _jobs_glob(processor_name)
    jobs_path      = _find_jobs_file(workdir, pattern=jobs_pattern, subdir=jobs_subdir)
    if jobs_path is None:
        print(f"[ERROR] No {jobs_pattern} found in {workdir}. Run submit first.", file=sys.stderr)
        sys.exit(1)
    hpc_mode = getattr(args, "hpc_mode", False)
    container = _resolve_container_arg(processor_name, getattr(args, "container", None))
    processor = _load_local_processor(processor_name, workdir, jobs_path,
                                      hpc_mode=hpc_mode, container=container)
    _call_if_supported(processor.refresh, ls=getattr(args, "ls", None))


def _proc_local_retry(args):
    processor_name = getattr(args, "processor_name", "ISCE2_S1")
    workdir        = _resolve_workdir(args.workdir)
    jobs_pattern, jobs_subdir = _jobs_glob(processor_name)
    jobs_path      = _find_jobs_file(workdir, pattern=jobs_pattern, subdir=jobs_subdir)
    if jobs_path is None:
        print(f"[ERROR] No {jobs_pattern} found in {workdir}. Run submit first.", file=sys.stderr)
        sys.exit(1)
    hpc_mode = getattr(args, "hpc_mode", False)
    dry_run  = getattr(args, "dry_run", False)
    container = _resolve_container_arg(processor_name, getattr(args, "container", None))
    _load_local_processor(processor_name, workdir, jobs_path,
                          hpc_mode=hpc_mode, dry_run=dry_run,
                          container=container).retry()


def _proc_local_cancel(args):
    processor_name = getattr(args, "processor_name", "ISCE2_S1")
    workdir        = _resolve_workdir(args.workdir)
    jobs_pattern, jobs_subdir = _jobs_glob(processor_name)
    jobs_path      = _find_jobs_file(workdir, pattern=jobs_pattern, subdir=jobs_subdir)
    if jobs_path is None:
        print(f"[ERROR] No {jobs_pattern} found in {workdir}. Nothing to cancel.", file=sys.stderr)
        sys.exit(1)
    hpc_mode = getattr(args, "hpc_mode", False)
    container = _resolve_container_arg(processor_name, getattr(args, "container", None))
    processor = _load_local_processor(processor_name, workdir, jobs_path,
                                      hpc_mode=hpc_mode, container=container)
    if not hasattr(processor, "cancel"):
        print(f"[ERROR] '{processor_name}' does not support cancel().", file=sys.stderr)
        sys.exit(1)
    processor.cancel()


def _proc_run_stage_unit(args):
    """Internal action: one HPC child job's unit of work (GMTSAR_S1
    stack_mode). Not meant to be run by hand -- see this parser's --help
    description and gmtsar_s1.py's run_stage_unit() docstring for why this
    exists (GMTSAR_S1 has no flat shell-command-list generator the way
    ISCE2_S1's stackSentinel.py run_NN_* files do, so each HPC child job
    re-enters `insarhub` itself to call one already-implemented per-unit
    method instead of a raw shell command line)."""
    import dataclasses
    from insarhub import Processor

    processor_name = getattr(args, "processor_name", "GMTSAR_S1")
    processor_cls  = Processor._registry[processor_name]
    workdir        = _resolve_workdir(args.workdir)

    saved_cfg = _read_proc_config_from_folder(workdir)
    if not saved_cfg:
        print(f"[ERROR] No insarhub_config.json found in {workdir}", file=sys.stderr)
        sys.exit(1)

    config_cls = getattr(processor_cls, "default_config", None)
    overrides: dict = {k: v for k, v in saved_cfg.items() if k not in _SAVED_CFG_SKIP}
    overrides["workdir"] = str(workdir)
    if overrides.get("hpc_mode"):
        # The sbatch template is the processor's own -- GMTSAR and ISCE3_Burst
        # have completely different stage sets, and writing one's template into
        # the other's workdir would fill sbatch_options.json with entries that
        # never match a real stage.
        from insarhub.processor.isce2_base import load_or_init_sbatch_options
        tmpl = getattr(processor_cls, "SBATCH_DEFAULT_TEMPLATE", None)
        if not tmpl:
            from insarhub.processor.gmtsar_s1 import _GMTSAR_SBATCH_DEFAULT_TEMPLATE
            tmpl = _GMTSAR_SBATCH_DEFAULT_TEMPLATE
        overrides["sbatch_options_per_step"] = load_or_init_sbatch_options(
            workdir, default_template=tmpl) or {}

    pairs_data = _load_pairs(args, workdir)
    raw_pairs  = pairs_data if isinstance(pairs_data, list) else next(iter(pairs_data.values()), [])
    pairs      = [tuple(str(x) for x in p) for p in raw_pairs]
    if processor_name == "GMTSAR_S1" and pairs and len(pairs[0]) == 2:
        from insarhub.processor.gmtsar_s1 import pairs_from_downloader
        slc_dir_val   = overrides.get("slc_dir") or str(workdir)
        orbit_dir_val = overrides.get("orbit_dir") or slc_dir_val
        pairs = pairs_from_downloader(pairs, slc_dir=slc_dir_val, orbit_dir=orbit_dir_val)
    if not pairs:
        print("[ERROR] No pairs found.", file=sys.stderr)
        sys.exit(1)

    valid_keys  = {f.name for f in dataclasses.fields(config_cls)} if config_cls else set()
    init_kwargs = {k: v for k, v in overrides.items() if k in valid_keys}
    cfg         = config_cls(**init_kwargs)
    processor   = processor_cls(pairs=pairs, config=cfg)

    if not hasattr(processor, "run_stage_unit"):
        print(f"[ERROR] '{processor_name}' does not support run-stage-unit.", file=sys.stderr)
        sys.exit(1)
    try:
        # GMTSAR_S1 takes (stage, index, subswath); ISCE3_Burst has no
        # subswath concept and takes (stage, index). Pass only what the
        # processor's own signature accepts rather than assuming one shape.
        import inspect as _inspect
        _params = _inspect.signature(processor.run_stage_unit).parameters
        if "subswath" in _params:
            ok = processor.run_stage_unit(args.stage, args.index, args.subswath)
        else:
            ok = processor.run_stage_unit(args.stage, args.index)
    except Exception as e:
        print(f"[ERROR] run-stage-unit failed (stage={args.stage!r} subswath={args.subswath!r} "
              f"index={args.index!r}): {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0 if ok else 1)


def _proc_local_watch(args):
    processor_name   = getattr(args, "processor_name", "ISCE2_S1")
    workdir          = _resolve_workdir(args.workdir)
    refresh_interval = getattr(args, "interval", 60)
    jobs_pattern, jobs_subdir = _jobs_glob(processor_name)
    jobs_path        = _find_jobs_file(workdir, pattern=jobs_pattern, subdir=jobs_subdir)
    if jobs_path is None:
        print(f"[ERROR] No {jobs_pattern} found in {workdir}. Run submit first.", file=sys.stderr)
        sys.exit(1)

    hpc_mode = getattr(args, "hpc_mode", False)
    container = _resolve_container_arg(processor_name, getattr(args, "container", None))
    processor = _load_local_processor(processor_name, workdir, jobs_path,
                                      hpc_mode=hpc_mode, container=container)
    # refresh_interval (ISCE2_Base) vs poll_interval (GMTSAR_S1) -- pass both
    # spellings, _call_if_supported keeps only the one the method actually has.
    _call_if_supported(processor.watch, refresh_interval=refresh_interval,
                       poll_interval=refresh_interval)


def cmd_analyzer(args, extra_args: list[str]):
    from insarhub import Analyzer

    # --list-analyzers (works without a sub-action)
    if args.list_analyzers:
        print("Available analyzers:")
        for name in Analyzer.available():
            print(f"  {name}")
        return

    analyzer_name = getattr(args, "analyzer_name", "Hyp3_Mintpy_SBAS")

    if analyzer_name not in Analyzer._registry:
        print(f"[ERROR] Unknown analyzer '{analyzer_name}'. Use --list-analyzers.",
              file=sys.stderr)
        sys.exit(1)

    action = getattr(args, "az_action", None)

    if getattr(args, "list_options", False):
        _az_run(args, extra_args)
        return

    if action == "run":
        _az_run(args, extra_args)
    elif action == "cleanup":
        _az_cleanup(args)
    elif extra_args or any(
        hasattr(args, f) for f in vars(args)
        if f not in ("command", "az_action", "analyzer_name", "workdir",
                     "list_analyzers", "list_options", "debug")
    ):
        # Config overrides without a subcommand — update .mintpy.cfg and exit
        _az_run(args, extra_args)


def _az_run(args, extra_args: list[str]):
    import dataclasses
    from insarhub import Analyzer
    from insarhub.commands import PrepDataCommand, AnalyzeCommand, PlotCommand

    analyzer_cls = Analyzer._registry[args.analyzer_name]

    overrides: dict = {}
    config_cls = getattr(analyzer_cls, "default_config", None)
    # Collect overrides from extra_args (flags passed after subcommand)
    _apply_config_overrides(overrides, config_cls, extra_args,
                            skip_fields=_ANALYZER_SKIP_FIELDS, label=args.analyzer_name)
    if config_cls is not None and dataclasses.is_dataclass(config_cls):
        # Also collect overrides from args (pre-registered flags on p_analyzer, no subcommand)
        for f in dataclasses.fields(config_cls):
            if f.name in _ANALYZER_SKIP_FIELDS or f.name in overrides:
                continue
            val = getattr(args, f.name, None)  # only present if user explicitly set it (SUPPRESS default)
            if val is not None:
                overrides[f.name] = val
        # Bare --container resolves to the analyzer's default image.
        if getattr(args, "container", None) is not None:
            overrides["container"] = _resolve_container_arg(args.analyzer_name, args.container)

    # Self-contained analyzers (GMTSAR_SBAS, ISCE3_Dolphin_PL) run their whole
    # pipeline inside analyzer.run(), which itself re-invokes into --container
    # when set. They have no .mintpy.cfg and no prep_data/--step/plot
    # orchestration, so route them straight to run() instead of the MintPy step
    # machinery below. The MintPy-family check is POSITIVE: a subclass of
    # Mintpy_SBAS_Base_Analyzer is the only kind that takes the orchestrated
    # path; everything else is self-contained.
    from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer
    if not issubclass(analyzer_cls, Mintpy_SBAS_Base_Analyzer) and not args.list_options:
        if getattr(args, "az_action", None) is None:
            print(f"[ERROR] '{args.analyzer_name}' has no .mintpy.cfg -- use the "
                  f"'run' action (e.g. 'insarhub analyzer -N {args.analyzer_name} "
                  f"-w <workdir> run [--container <image>]').", file=sys.stderr)
            sys.exit(1)
        overrides["debug"] = getattr(args, "debug", False)
        workdir = _resolve_workdir(args.workdir)
        for analysis_dir in _iter_analysis_dirs(workdir):
            tag = f"[{analysis_dir.name}] " if analysis_dir != workdir else ""
            analyzer = Analyzer.create(args.analyzer_name, workdir=analysis_dir, **overrides)
            result = AnalyzeCommand(analyzer).run()
            _fail(result, f"{args.analyzer_name} {tag}".strip())
        return

    run_prep = False
    run_plot_explicit = False
    mintpy_steps: list[str] | None = None
    steps = getattr(args, 'step', None) or ['all']  # default: run everything including prep_data
    expanded: list[str] = []
    for s in steps:
        if s in ('prep_data', 'prep'):  # 'prep' is accepted as alias for 'prep_data'
            run_prep = True
        elif s == 'all':
            run_prep = True
            expanded.extend(_MINTPY_ALL_STEPS)
        elif s == 'plot':
            # Not a real MintPy step name (TimeSeriesAnalysis.run() would
            # silently ignore it) -- handled separately below via plot().
            run_plot_explicit = True
        else:
            expanded.append(s)
    mintpy_steps = expanded or None  # None → AnalyzeCommand uses full default

    # Config-only mode: overrides provided but no subcommand — update .mintpy.cfg and exit
    if getattr(args, "az_action", None) is None and not args.list_options:
        if overrides:
            workdir = _resolve_workdir(args.workdir)
            for analysis_dir in _iter_analysis_dirs(workdir):
                cfg_path = analysis_dir / ".mintpy.cfg"
                label = analysis_dir.name if analysis_dir != workdir else workdir.name
                if not cfg_path.exists():
                    print(f"[WARNING] No .mintpy.cfg in [{label}]. "
                          f"Run '--step prep_data' first.", file=sys.stderr)
                    continue
                _update_mintpy_cfg(cfg_path, overrides)
                print(f"[{label}] Updated: {list(overrides.keys())}")
        return

    if args.list_options:
        workdir = _resolve_workdir(args.workdir)
        analysis_dirs = _iter_analysis_dirs(workdir)
        config_cls = getattr(analyzer_cls, "default_config", None)
        for analysis_dir in analysis_dirs:
            label = analysis_dir.name if analysis_dir != workdir else workdir.name
            cfg_path = analysis_dir / ".mintpy.cfg"
            if cfg_path.exists():
                if overrides:
                    _update_mintpy_cfg(cfg_path, overrides)
                values = _read_mintpy_cfg(cfg_path)
            else:
                values = {}
            _print_config_options(config_cls,
                                  display_label=f"{args.analyzer_name} [{label}]",
                                  skip_fields=_ANALYZER_SKIP_FIELDS,
                                  value_overrides=values)
        return

    overrides["debug"] = getattr(args, "debug", False)
    workdir = _resolve_workdir(args.workdir)

    # Auto-plot mirrors MintPy's own CLI semantics (a bulk multi-step run()
    # auto-plots) even though steps are executed one at a time below for
    # per-step progress reporting -- that means run()'s own internal
    # len(run_steps) > 1 check never actually fires here, so it's
    # replicated at this orchestration level instead. Explicit '--step plot'
    # always plots regardless of how many other steps were requested.
    should_plot = run_plot_explicit or len(mintpy_steps or []) > 1

    # Build ordered step list for display
    display_steps = (["prep_data"] if run_prep else []) + (mintpy_steps or []) + (["plot"] if should_plot else [])
    total = len(display_steps)

    for analysis_dir in _iter_analysis_dirs(workdir):
        tag = f"[{analysis_dir.name}] " if analysis_dir != workdir else ""
        if tag:
            print(f"\n{tag}Starting analysis...")
        analyzer = Analyzer.create(args.analyzer_name, workdir=analysis_dir, **overrides)

        step_num = 1
        hpc = getattr(analyzer.config, "hpc_mode", False)

        # HPC mode: submit everything (prep_data + MintPy steps + plot) as a
        # single sbatch job; 'plot' is a real token the CLI understands (see
        # above), so it survives the sbatch job's own re-invocation of this
        # same command.
        if hpc:
            hpc_steps = (["prep_data"] if run_prep else []) + (mintpy_steps or []) + (["plot"] if should_plot else [])
            job_id = analyzer.submit_hpc(steps=hpc_steps or None)
            if job_id is None:
                sys.exit(0)  # sbatch_options.json was just created/updated — stop for review
            continue

        if run_prep:
            print(f"\nStep {step_num}/{total}: prep_data")
            step_num += 1
            result = PrepDataCommand(analyzer).run()
            _fail(result, f"prep_data {tag}".strip())
            if mintpy_steps is None and not should_plot:
                continue  # only 'prep_data' was requested for this dir

        for step in (mintpy_steps or []):
            print(f"\nStep {step_num}/{total}: {step}")
            step_num += 1
            result = AnalyzeCommand(analyzer, steps=[step]).run()
            _fail(result, f"{step} {tag}".strip())

        if should_plot:
            print(f"\nStep {step_num}/{total}: plot")
            step_num += 1
            result = PlotCommand(analyzer).run()
            _fail(result, f"plot {tag}".strip())


def _az_cleanup(args):
    from insarhub import Analyzer

    workdir = _resolve_workdir(args.workdir)

    for analysis_dir in _iter_analysis_dirs(workdir):
        tag = f"[{analysis_dir.name}] " if analysis_dir != workdir else ""
        analyzer = Analyzer.create(args.analyzer_name, workdir=analysis_dir, debug=args.debug)
        if not hasattr(analyzer, "cleanup"):
            print(f"[ERROR] '{args.analyzer_name}' does not support cleanup.", file=sys.stderr)
            sys.exit(1)
        if tag:
            print(f"{tag}Cleaning up...")
        analyzer.cleanup()


def cmd_utils(args, extra_args: list[str]):
    action = getattr(args, "ut_action", None)

    if action == "clip":
        from insarhub.utils.tool import clip_hyp3_s1
        workdir = _resolve_workdir(args.workdir)
        aoi_raw = args.aoi
        if len(aoi_raw) == 1:
            aoi = aoi_raw[0]  # file path
        elif len(aoi_raw) == 4:
            try:
                aoi = [float(v) for v in aoi_raw]
            except ValueError:
                print("[ERROR] --aoi expects 4 floats or a single file path.", file=sys.stderr)
                sys.exit(1)
        else:
            print("[ERROR] --aoi expects 4 floats (minlon minlat maxlon maxlat) or a file path.",
                  file=sys.stderr)
            sys.exit(1)
        clip_hyp3_s1(workdir=workdir, aoi=aoi)

    elif action == "h5-to-raster":
        from insarhub.utils.postprocess import h5_to_raster
        h5_to_raster(h5_file=args.input, out_raster=args.output)

    elif action == "save-footprint":
        from insarhub.utils.postprocess import save_footprint
        save_footprint(raster_file=args.input, out_footprint=args.output)

    elif action == "slurm":
        from insarhub.utils.tool import Slurmjob_Config
        cfg = Slurmjob_Config(
            job_name=args.job_name,
            time=args.time,
            partition=args.partition,
            nodes=args.nodes,
            ntasks=args.ntasks,
            cpus_per_task=args.cpus,
            mem=args.mem,
            gpus=args.gpus,
            conda_env=args.conda_env,
            modules=args.modules,
            mail_user=args.mail_user,
            mail_type=args.mail_type,
            account=args.account,
            qos=args.qos,
            command=args.job_command,
        )
        out_path = cfg.to_script(args.output)
        print(f"SLURM script written → {out_path}")

    elif action == "plot-network":
        import json
        from insarhub.utils import plot_pair_network as _plot_pair_network

        in_path = Path(args.input).expanduser().resolve()
        pairs = [tuple(p) for p in json.loads(in_path.read_text())]

        # Auto-detect baselines file beside the pairs file
        bl_path = Path(args.baselines).expanduser().resolve() if args.baselines else None
        if bl_path is None:
            stem = in_path.stem.replace("pairs", "baselines")
            candidate = in_path.parent / f"{stem}.json"
            if candidate.exists():
                bl_path = candidate
        baselines = {}
        if bl_path and bl_path.exists():
            raw_bl = json.loads(bl_path.read_text())
            baselines = {tuple(k.split("|||")): float(v) for k, v in raw_bl.items()}

        out_path = Path(args.output).expanduser().resolve() if args.output else in_path.parent / "network.png"
        _plot_pair_network(pairs, baselines, save_path=out_path, title=args.title)
        print(f"Network plot saved → {out_path}")

    elif action == "era5-download":
        from insarhub.utils.batch import ERA5Downloader
        explicit_output = None if args.output == "." else args.output
        downloader = ERA5Downloader(
            output_dir=explicit_output,
            num_processes=args.num_processes,
            max_retries=args.max_retries,
        )
        downloader.download_batch(args.workdir)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_HANDLERS = {
    "downloader": cmd_downloader,
    "processor":  cmd_processor,
    "analyzer":   cmd_analyzer,
    "utils":      cmd_utils,
}


def main():
    parser = create_parser()
    args, extra_args = parser.parse_known_args()

    # Nothing in the library installs a logging handler, so until this was
    # added every logger.info/warning/error was dropped silently -- Python's
    # last-resort handler only passes WARNING and above, and insarhub/__init__
    # additionally called logging.disable(CRITICAL), which killed even those.
    # That made whole commands look like no-ops (`cancel` scancelled every job
    # and printed nothing at all).
    #
    # WARNING is the default level, not INFO: warnings and errors are what was
    # genuinely being lost, while INFO is per-step progress chatter that turns
    # ordinary output into a wall of "[INFO] [ 0%] ..." lines. --verbose opts
    # into it, -vv into DEBUG. basicConfig is a no-op when a handler already
    # exists, so an embedding application's own setup still wins; the root
    # stays at WARNING regardless so third-party libraries (matplotlib,
    # botocore, asyncio, ...) never flood the terminal.
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s")
    _v = getattr(args, "verbose", 0) or 0
    logging.getLogger("insarhub").setLevel(
        logging.DEBUG if _v >= 2 else logging.INFO if _v == 1 else logging.WARNING)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    _EXTRA_ARGS_COMMANDS = {"downloader", "processor", "analyzer"}
    if extra_args and args.command not in _EXTRA_ARGS_COMMANDS:
        print(f"[WARNING] Unrecognized arguments ignored: {extra_args}", file=sys.stderr)
    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    if args.command == "downloader":
        if (not extra_args
                and not args.list_downloaders
                and not args.list_options
                and not args.pipeline
                and not args.AOI
                and not args.download
                and not args.select_pairs
                and args.footprint is None
                and args.config is None):
            parser.parse_args(["downloader", "--help"])  # prints and exits
        handler(args, extra_args)
    elif args.command == "processor":
        if (not getattr(args, "list_processors", False)
                and not getattr(args, "list_options", False)
                and not getattr(args, "proc_action", None)):
            parser.parse_args(["processor", "--help"])  # prints and exits
        handler(args, extra_args)
    elif args.command == "analyzer":
        _az_has_overrides = bool(extra_args) or any(
            hasattr(args, f) for f in vars(args)
            if f not in ("command", "az_action", "analyzer_name", "workdir",
                         "list_analyzers", "list_options", "debug")
        )
        if (not getattr(args, "list_analyzers", False)
                and not getattr(args, "list_options", False)
                and not getattr(args, "az_action", None)
                and not _az_has_overrides):
            parser.parse_args(["analyzer", "--help"])  # prints and exits
        handler(args, extra_args)
    elif args.command == "utils":
        if not getattr(args, "ut_action", None):
            parser.parse_args(["utils", "--help"])  # prints and exits
        handler(args, extra_args)
    else:
        handler(args, extra_args)


if __name__ == "__main__":
    main()
