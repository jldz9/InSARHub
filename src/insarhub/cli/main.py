# -*- coding: utf-8 -*-
"""
HPC-compatible CLI for InSARHub.

Every command handler builds an instance from CLI args, then delegates all
logic to the shared command layer (insarhub.commands). The same command
classes are used by the Panel frontend, so there is no duplicated business logic.

Pipeline subcommands
--------------------
insarhub downloader -N S1_SLC --AOI -113.05 37.74 -112.68 38.00 --start 2021-01-01 --end 2022-01-01
insarhub downloader ... --select-pairs --download --orbit-files
insarhub processor -N Hyp3_S1 -w /data/bryce submit
insarhub processor -N Hyp3_S1 -w /data/bryce refresh
insarhub processor -N Hyp3_S1 -w /data/bryce download
insarhub processor -N Hyp3_S1 -w /data/bryce retry
insarhub processor -N Hyp3_S1 -w /data/bryce watch --interval 300
insarhub processor -N Hyp3_S1 -w /data/bryce credits
insarhub processor -N ISCE2      -w /data/bryce run    (local — not yet implemented)
insarhub analyzer   -N Hyp3_SBAS -w /data/bryce run
insarhub analyzer   -N Hyp3_SBAS -w /data/bryce cleanup

Utilities
---------
insarhub utils clip           --workdir /data/bryce --aoi -113.05 37.74 -112.68 38.00
insarhub utils h5-to-raster   --input velocity.h5
insarhub utils save-footprint --input velocity.h5
insarhub utils slurm          --job-name insar_run --cpus 8 --mem 32G --command "insarhub analyzer -N Hyp3_SBAS -w /data/bryce run"
insarhub utils era5-download  -w /data/bryce -o /data/era5
"""

import argparse
import json
import re
import sys
from pathlib import Path

from insarhub._version import __version__
from insarhub.utils.defaults import SELECT_PAIRS_DEFAULTS as _SP, DOWNLOAD_DEFAULTS as _DL


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
             "Sets relativeOrbit and frame; takes precedence over --relativeOrbit/--frame.",
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
    g_down.add_argument("--workers", metavar="INT", type=int, default=_DL["max_workers"],
                        help=f"Parallel download workers (default: {_DL['max_workers']})")
    g_down.add_argument("--footprint", metavar="PATH",
                        help="Save footprint map image to this path")

    # ------------------------------------------------------------------ #
    # processor — submit + manage InSAR processing jobs
    #
    # Usage: insarhub processor -N <ProcessorName> <action> [options]
    #
    # HyP3 processors (online): submit | refresh | download | retry | watch | credits
    # Local processors (ISCE_S1): submit | refresh | retry | watch
    # ------------------------------------------------------------------ #
    p_proc = sub.add_parser(
        "processor",
        help="Submit and manage InSAR processing jobs",
        description=(
            "Select a processor with -N and run an action.\n"
            "\nHyP3 (online) processor actions:\n"
            "  submit           Submit pairs to HyP3\n"
            "  refresh          Pull latest job statuses\n"
            "  download         Download completed outputs\n"
            "  retry            Resubmit failed jobs\n"
            "  watch            Poll until all jobs complete\n"
            "  credits          Show remaining HyP3 credits\n"
            "\nLocal processor actions (ISCE_S1):\n"
            "  submit           Submit pairs to local ISCE2 processor\n"
            "  refresh          Show current job statuses\n"
            "  retry            Resubmit failed pairs\n"
            "  watch            Poll until all pairs complete\n"
            "\nRun 'insarhub processor -N <name> <action> --help' for action details."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
                        help="Path to a saved processor config JSON; "
                             "omit the value to use <workdir>/processor_config.json")
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
                       help="Path to a saved processor config JSON; "
                            "omit the value to use <workdir>/processor_config.json")
    g_sub.add_argument("--credential-pool", metavar="PATH",
                       help="JSON {username: password} for multi-account HyP3 submission")
    g_sub.add_argument("--name-prefix", metavar="STR", default="ifg",
                       help="Job name prefix (default: ifg)")
    g_sub.add_argument("--max-workers", metavar="INT", type=int, default=4,
                       help="Parallel submission workers (default: 4)")
    g_sub.add_argument("--dry-run", action="store_true",
                       help="Print what would be submitted without sending any jobs to HyP3")
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

    # --- download  (HyP3) --------------------------------------------- #
    p_proc_dl = proc_sub.add_parser("download", help="Download completed HyP3 job outputs")
    _add_job_file(p_proc_dl)

    # --- retry  (HyP3) ------------------------------------------------- #
    p_proc_retry = proc_sub.add_parser("retry", help="Resubmit failed HyP3 jobs")
    _add_job_file(p_proc_retry)

    # --- watch  (HyP3) ------------------------------------------------- #
    p_proc_watch = proc_sub.add_parser(
        "watch", help="Poll HyP3 until all jobs complete, downloading results as they succeed")
    _add_job_file(p_proc_watch)
    p_proc_watch.add_argument("--interval", metavar="SEC", type=int, default=300,
                              help="Seconds between refreshes (default: 300)")

    # --- credits  (HyP3) ----------------------------------------------- #
    p_proc_credits = proc_sub.add_parser("credits", help="Show remaining HyP3 processing credits")
    _add_credential_pool(p_proc_credits)

    # ------------------------------------------------------------------ #
    # analyzer — prepare + run MintPy SBAS time-series analysis
    #
    # Actions: prep | run | cleanup
    # Config fields for the chosen analyzer are passed as extra --KEY VALUE
    # flags (run only) and resolved dynamically at runtime.
    # ------------------------------------------------------------------ #
    _step_table = (
        "Available steps for --step:\n"
        "\n"
        "  Keyword       Description\n"
        "  ----------    ----------------------------------------\n"
        "  prep          Prepare HyP3 data (unzip, clip, configure)\n"
        "  all           prep + all MintPy steps below (default if --step omitted)\n"
        "\n"
        "  MintPy step             \n"
        "  --------------------\n"
        + "".join(f"  {s}\n" for s in _MINTPY_ALL_STEPS)
        + "\n"
        "Examples:\n"
        "  insarhub analyzer -N Hyp3_SBAS run\n"
        "  insarhub analyzer -N Hyp3_SBAS --compute_maxMemory 30 run --step velocity\n"
        "  insarhub analyzer -N Hyp3_SBAS --list-options\n"
        "  insarhub analyzer -N Hyp3_SBAS cleanup\n"
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
    p_analyzer.add_argument(
        "-N", "--name", metavar="STR", default="Hyp3_SBAS", dest="analyzer_name",
        help="Analyzer name (default: Hyp3_SBAS; see --list-analyzers)",
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
        from insarhub.config.defaultconfig import Hyp3_SBAS_Config
        import dataclasses as _dc, typing as _typing
        _hints = _typing.get_type_hints(Hyp3_SBAS_Config)
        for _f in _dc.fields(Hyp3_SBAS_Config):
            if _f.name in _ANALYZER_SKIP_FIELDS:
                continue
            _kwargs = _field_argparse_kwargs(_hints.get(_f.name, str), None)
            _kwargs["default"] = argparse.SUPPRESS
            _kwargs["help"] = argparse.SUPPRESS
            try:
                p_analyzer.add_argument("--" + _f.name, dest=_f.name, **_kwargs)
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
    return Path(raw).expanduser().resolve() if raw else Path.cwd()


def _find_job_files(job_dir: Path, override: str | None = None) -> list[Path]:
    """Return all hyp3*.json job files in job_dir, or just the override file if given."""
    if override:
        return [Path(override).expanduser().resolve()]
    return sorted(job_dir.glob("hyp3*.json"))


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


def _iter_job_dirs(workdir: Path, job_file_override: str | None) -> list[Path]:
    """
    Return the list of directories to operate on for lifecycle commands.

    Resolution order:
      1. --job-file given  → parent directory of that file only
      2. p*_f* subdirs that contain any hyp3*.json  → each subdir
      3. workdir itself  (flat / single-group case)
    """
    if job_file_override:
        return [Path(job_file_override).expanduser().resolve().parent]
    subdirs = sorted(
        d for d in workdir.iterdir()
        if d.is_dir() and _parse_group_key(d.name) and any(d.glob("hyp3*.json"))
    )
    return subdirs if subdirs else [workdir]


def _iter_analysis_dirs(workdir: Path) -> list[Path]:
    """
    Return the list of directories to run analysis on.

    Resolution order:
      1. p*_f* subdirs that contain any *.zip files  → each subdir
      2. workdir itself  (flat / single-group case)
    """
    subdirs = sorted(
        d for d in workdir.iterdir()
        if d.is_dir() and _parse_group_key(d.name) and any(d.glob("*.zip"))
    )
    return subdirs if subdirs else [workdir]


def _load_hyp3_processor(workdir: Path, job_file: Path | None = None,
                          credential_pool_path: str | None = None,
                          processor_name: str = "Hyp3_S1"):
    """Build a HyP3 processor, loading saved jobs from job_file when provided."""
    from insarhub import Processor

    overrides: dict = {"workdir": workdir}
    if job_file:
        overrides["saved_job_path"] = job_file

    pool = _load_credential_pool(credential_pool_path)
    if pool:
        overrides["earthdata_credentials_pool"] = pool

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


def _str_to_bool(v: str) -> bool:
    """Convert a string like 'true'/'false'/'1'/'0' to bool."""
    if v.lower() in ("true", "1", "yes"):
        return True
    if v.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false, got '{v}'")


def _field_argparse_kwargs(annotation, default) -> dict:
    """Return kwargs for ArgumentParser.add_argument() inferred from a type annotation."""
    import typing
    import dataclasses

    base = _unwrap_optional(annotation)
    origin = typing.get_origin(base)

    if base is bool:
        return {"type": _str_to_bool, "default": default, "metavar": "BOOL"}

    if origin is list:
        inner_args = typing.get_args(base)
        inner = inner_args[0] if inner_args else str
        return {"nargs": "+", "type": inner, "default": default}

    if base in (int, float, str, Path):
        return {"type": str if base is Path else base, "default": default, "metavar": base.__name__.upper()}

    # Fallback for complex types (e.g. tuple, custom classes) — accept as string
    return {"type": str, "default": default, "metavar": "VALUE"}


_MINTPY_ALL_STEPS = [
    'load_data', 'modify_network', 'reference_point', 'quick_overview', 'invert_network',
    'correct_LOD', 'correct_SET', 'correct_ionosphere', 'correct_troposphere',
    'deramp', 'correct_topography', 'residual_RMS', 'reference_date',
    'velocity', 'geocode', 'google_earth', 'hdfeos5',
]

_SEARCH_SKIP_FIELDS = {"name"}  # handled via CLI flags or internal

# Fields handled by static flags or internal state in cmd_processor
_SUBMIT_SKIP_FIELDS = {
    "name", "workdir", "pairs", "saved_job_path",
    "earthdata_credentials_pool",
    "name_prefix", "max_workers",
}

# Fields handled by static flags or internal state in cmd_analyzer
_ANALYZER_SKIP_FIELDS = {"name", "workdir", "debug"}


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

        flag = "--" + field.name
        kwargs = _field_argparse_kwargs(annotation, None)
        kwargs["default"] = _UNSET  # distinguish "not provided" from any real value
        kwargs["help"] = argparse.SUPPRESS  # hidden; shown only via --list-options
        try:
            p.add_argument(flag, dest=field.name, **kwargs)
        except argparse.ArgumentError:
            pass  # skip duplicate flags (e.g. --workdir already added)

    p._unset_sentinel = _UNSET  # type: ignore[attr-defined]

    return p


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


def _read_config_json(cfg_path: Path) -> dict:
    """Read a JSON config file and return {field: value}, empty dict if missing or unreadable."""
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text())
    except Exception:
        return {}


def _write_config_json(cfg_path: Path, overrides: dict) -> None:
    """Merge overrides into the existing JSON config file, then write back."""
    existing = _read_config_json(cfg_path)
    existing.update(overrides)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(existing, indent=2, default=str))


def _find_jobs_file(workdir: Path, pattern: str) -> Path | None:
    """Return the first matching jobs JSON file in workdir or its p*_f* subdirs."""
    for p in sorted(workdir.glob(pattern)):
        return p
    for subdir in sorted(workdir.iterdir()):
        if subdir.is_dir() and _parse_group_key(subdir.name):
            for p in sorted(subdir.glob(pattern)):
                return p
    return None


def _find_subfolder_config(workdir: Path, filename: str) -> Path | None:
    """Return the config file path from the first p*_f* subfolder that has it."""
    if not workdir.is_dir():
        return None
    for subdir in sorted(workdir.iterdir()):
        if subdir.is_dir() and _parse_group_key(subdir.name):
            cfg = subdir / filename
            if cfg.exists():
                return cfg
    return None


def _read_dl_config_from_folder(folder: Path) -> dict:
    """Read downloader config from insarhub_config.json or fallback downloader_config.json."""
    insarhub_cfg = folder / "insarhub_config.json"
    if insarhub_cfg.exists():
        try:
            data = json.loads(insarhub_cfg.read_text())
            cfg = data.get("downloader", {}).get("config", {})
            if cfg:
                return cfg
        except Exception:
            pass
    return _read_config_json(folder / "downloader_config.json")


def _read_proc_config_from_folder(folder: Path) -> dict:
    """Read processor config from insarhub_config.json or fallback processor_config.json."""
    insarhub_cfg = folder / "insarhub_config.json"
    if insarhub_cfg.exists():
        try:
            data = json.loads(insarhub_cfg.read_text())
            cfg = data.get("processor", {}).get("config", {})
            if cfg:
                return cfg
        except Exception:
            pass
    return _read_config_json(folder / "processor_config.json")


_GROUP_KEY_RE = re.compile(r"p(\d+)_f(\d+)")


def _parse_group_key(key: str) -> tuple[int, int] | None:
    """Parse 'p100_f466' → (100, 466); return None if key doesn't match pattern."""
    m = _GROUP_KEY_RE.fullmatch(key)
    return (int(m.group(1)), int(m.group(2))) if m else None


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

    # Fall back to flat stack_p0_f0.json (new) or pairs.json (legacy)
    for auto in (workdir / "stack_p0_f0.json", workdir / "pairs.json"):
        if auto.is_file():
            print(f"[pairs] Auto-loading {auto}")
            data = json.loads(auto.read_text())
            return data.get("pairs", []) if isinstance(data, dict) else data
    print(f"[ERROR] No pairs file found under current workdir {workdir}. Use --pairs-file, --pairs, or run "
          "'insarhub downloader --select-pairs' first.", file=sys.stderr)
    sys.exit(1)


def _generate_consecutive_pairs(results: dict) -> dict:
    """
    For each (path, frame) stack, sort scenes by date and generate consecutive
    (reference, secondary) pairs.

    Returns
    -------
    dict
        ``{"path_frame": [["ref_scene", "sec_scene"], ...], ...}``
    """
    from dateutil.parser import isoparse

    out = {}
    for key, scenes in results.items():
        sorted_scenes = sorted(scenes, key=lambda s: isoparse(s.properties["startTime"]))
        pairs = [
            [sorted_scenes[i].properties["sceneName"],
             sorted_scenes[i + 1].properties["sceneName"]]
            for i in range(len(sorted_scenes) - 1)
        ]
        if pairs:
            out[f"{key[0]}_{key[1]}"] = pairs
    return out


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
            anals = [
                n for n, c in Analyzer._registry.items()
                if getattr(c, 'compatible_processor', None) in (None, 'all', pname)
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
            values = _read_config_json(cfg_path) if cfg_path else {}
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
        saved_cfg = _read_config_json(cfg_path)
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
    if config_cls is not None and dataclasses.is_dataclass(config_cls):
        config_parser = _build_config_parser(config_cls)
        config_ns, unknown = config_parser.parse_known_args(extra_args)
        if unknown:
            print(f"[ERROR] Unknown flags for '{args.downloader_name}': {unknown}", file=sys.stderr)
            sys.exit(1)
        for f in dataclasses.fields(config_cls):
            val = getattr(config_ns, f.name, None)
            if val is not getattr(config_parser, '_unset_sentinel', None) and val is not None:
                overrides[f.name] = val
    elif extra_args:
        print(f"[WARNING] Extra args ignored (no config dataclass): {extra_args}", file=sys.stderr)

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

    stacks_filter: list[tuple] | None = None
    if args.stacks:
        parsed = []
        for token in args.stacks:
            parts = token.split(":")
            if len(parts) != 2:
                print(f"[ERROR] Invalid --stacks token '{token}' — expected PATH:FRAME", file=sys.stderr)
                sys.exit(1)
            try:
                parsed.append((int(parts[0]), int(parts[1])))
            except ValueError:
                print(f"[ERROR] PATH and FRAME must be integers, got '{token}'", file=sys.stderr)
                sys.exit(1)
        # Broad search to reduce API response; exact-pair filter applied after search
        overrides["relativeOrbit"] = list(dict.fromkeys(p for p, _ in parsed))
        overrides["frame"]         = list(dict.fromkeys(f for _, f in parsed))
        stacks_filter = parsed
    else:
        # Reconstruct stacks_filter from saved config lists so the exact-pair filter
        # is re-applied on reload (prevents ASF returning all cross-combinations)
        _ro = overrides.get("relativeOrbit")
        _fr = overrides.get("frame")
        if (isinstance(_ro, list) and isinstance(_fr, list)
                and len(_ro) == len(_fr) and len(_ro) > 0):
            stacks_filter = list(zip([int(x) for x in _ro], [int(x) for x in _fr]))

    overrides["workdir"] = workdir

    downloader = Downloader.create(args.downloader_name,
                                   **{k: v for k, v in overrides.items()
                                      if k not in ("name", "config")})

    result = SearchCommand(downloader).run()
    _fail(result, "search")

    if stacks_filter:
        from insarhub.commands import FilterCommand
        _fail(FilterCommand(downloader, {"path_frame": stacks_filter}).run(), "filter")

    SummaryCommand(downloader).run()

    if args.footprint:
        FootprintCommand(downloader, save_path=args.footprint).run()

    if args.select_pairs:
        import json
        from dataclasses import asdict
        from insarhub.utils.tool import write_workflow_marker
        from insarhub.utils import plot_pair_network as _plot_pair_network
        from insarhub.app.state import write_insarhub_config

        pairs, baselines, scene_bperp, prefetch_cache = downloader.select_pairs(
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
        )

        from insarhub.utils import PairQuality
        from insarhub.utils.pair_quality._db import PairQualityDB

        def _seed_cli_cache(folder, prefetch):
            """Seed the folder's quality cache with weather/snow from select_pairs."""
            weather = prefetch.get("weather", {})
            snow    = prefetch.get("snow", {})
            if not weather and not snow:
                return
            lat = prefetch.get("lat", 0.0)
            lon = prefetch.get("lon", 0.0)
            try:
                from insarhub.utils.pair_quality._cache import CacheManager
                cache = CacheManager(folder)
                for date, feats in weather.items():
                    if feats:
                        cache.set("weather", f"{lat:.3f}:{lon:.3f}:{date}", feats)
                for date, feats in snow.items():
                    if feats:
                        cache.set("snow_modis", f"{lat:.3f}:{lon:.3f}:{date}", feats)
                cache.save()
            except Exception as exc:
                print(f"[quality] Warning: could not seed weather cache ({exc})")

        dl_workdir = downloader.config.workdir

        # Build scenes_by_stack for DB precompute
        active = downloader.active_results
        scenes_by_stack: dict[tuple, list[str]] = {}
        bperp_by_stack:  dict[tuple, dict[str, float]] = {}
        if isinstance(active, dict):
            for k, prods in active.items():
                scenes_by_stack[k] = [p.properties["sceneName"] for p in prods]
        else:
            scenes_by_stack[(0, 0)] = [p.properties["sceneName"] for p in active]

        if isinstance(pairs, dict):
            for (path, frame), group_pairs in pairs.items():
                subdir = dl_workdir / f"p{path}_f{frame}"
                subdir.mkdir(parents=True, exist_ok=True)
                write_workflow_marker(subdir, downloader=type(downloader).name)
                cfg = {k: v for k, v in asdict(downloader.config).items() if k != 'workdir'}
                cfg['relativeOrbit'] = path
                cfg['frame'] = frame
                write_insarhub_config(subdir, {"downloader": {"type": type(downloader).name, "config": cfg}})
                sp = scene_bperp.get((path, frame)) or {}
                stack_scenes = scenes_by_stack.get((path, frame), [])
                bperp_by_stack[(path, frame)] = {k: float(v) for k, v in sp.items()}
                stack_data = {
                    "pairs":     [list(p) for p in group_pairs],
                    "baselines": {k: float(v) for k, v in sp.items()},
                    "scenes":    stack_scenes,
                    "pair_quality": {"scores": {}, "factors": {}},
                }
                stack_path = subdir / f"stack_p{path}_f{frame}.json"
                stack_path.write_text(json.dumps(stack_data, indent=2, default=str))
                _seed_cli_cache(subdir, prefetch_cache.get((path, frame), {}))
                print(f"[quality] Scoring all possible pairs — P{path}/F{frame}…")
                try:
                    db = PairQualityDB(subdir)
                    db.build(
                        {(path, frame): stack_scenes},
                        {(path, frame): bperp_by_stack.get((path, frame), {})},
                    )
                    db_data = json.loads((subdir / ".insarhub_pair_quality_db.json").read_text())
                    all_scores  = db_data.get("scores", {})
                    all_factors = db_data.get("factors", {})
                    quality_scores = {}
                    quality_factors = {}
                    for pair in group_pairs:
                        for k in [f"{pair[0]}:{pair[1]}", f"{pair[1]}:{pair[0]}"]:
                            if k in all_scores:
                                quality_scores[k]  = all_scores[k]
                                quality_factors[k] = all_factors.get(k, {})
                    stack_data["pair_quality"] = {"scores": quality_scores, "factors": quality_factors}
                    stack_path.write_text(json.dumps(stack_data, indent=2, default=str))
                    print(f"[quality] {db_data.get('_n_pairs', 0)} pairs scored, {len(quality_scores)} selected")
                except Exception as exc:
                    print(f"[quality] Warning: scoring failed ({exc}), plotting without scores")
                    quality_scores  = None
                    quality_factors = None
                _plot_pair_network(
                    group_pairs, baselines[(path, frame)],
                    scene_baselines=scene_bperp.get((path, frame)),
                    title=f"Interferogram Network — P{path}/F{frame}",
                    save_path=subdir / f"network_p{path}_f{frame}.png",
                    quality_scores=quality_scores,
                    quality_factors=quality_factors,
                )
                print(f"[pairs] p{path}_f{frame}: {len(group_pairs)} pairs → {stack_path}")
        else:
            dl_workdir.mkdir(parents=True, exist_ok=True)
            write_workflow_marker(dl_workdir, downloader=type(downloader).name)
            cfg = {k: v for k, v in asdict(downloader.config).items() if k != 'workdir'}
            write_insarhub_config(dl_workdir, {"downloader": {"type": type(downloader).name, "config": cfg}})
            sp = scene_bperp if isinstance(scene_bperp, dict) else {}
            stack_scenes = scenes_by_stack.get((0, 0), [])
            stack_data = {
                "pairs":     [list(p) for p in pairs],
                "baselines": {k: float(v) for k, v in sp.items()},
                "scenes":    stack_scenes,
                "pair_quality": {"scores": {}, "factors": {}},
            }
            stack_path = dl_workdir / "stack_p0_f0.json"
            stack_path.write_text(json.dumps(stack_data, indent=2, default=str))
            _seed_cli_cache(dl_workdir, prefetch_cache)
            print("[quality] Scoring all possible pairs…")
            try:
                db = PairQualityDB(dl_workdir)
                db.build({(0, 0): stack_scenes}, {(0, 0): {k: float(v) for k, v in sp.items()}})
                db_data = json.loads((dl_workdir / ".insarhub_pair_quality_db.json").read_text())
                all_scores  = db_data.get("scores", {})
                all_factors = db_data.get("factors", {})
                quality_scores  = {}
                quality_factors = {}
                for pair in pairs:
                    for k in [f"{pair[0]}:{pair[1]}", f"{pair[1]}:{pair[0]}"]:
                        if k in all_scores:
                            quality_scores[k]  = all_scores[k]
                            quality_factors[k] = all_factors.get(k, {})
                stack_data["pair_quality"] = {"scores": quality_scores, "factors": quality_factors}
                stack_path.write_text(json.dumps(stack_data, indent=2, default=str))
                print(f"[quality] {db_data.get('_n_pairs', 0)} pairs scored")
            except Exception as exc:
                print(f"[quality] Warning: scoring failed ({exc}), plotting without scores")
                quality_scores  = None
                quality_factors = None
            _plot_pair_network(pairs, baselines, scene_baselines=scene_bperp,
                               save_path=dl_workdir / "network.png",
                               quality_scores=quality_scores,
                               quality_factors=quality_factors)
            print(f"[pairs] Saved {len(pairs)} pairs → {stack_path}")

    if args.download:
        orbit_dir = args.orbit_files if isinstance(args.orbit_files, str) else None
        dl_kwargs: dict = {"max_workers": args.workers}
        if hasattr(downloader, "download") and "download_orbit" in downloader.download.__code__.co_varnames:
            dl_kwargs["download_orbit"] = bool(args.orbit_files)
        result = DownloadScenesCommand(downloader, **dl_kwargs).run()
        _fail(result, "download")
        if args.orbit_files and hasattr(downloader, "download_orbit"):
            downloader.download_orbit(save_dir=orbit_dir)
    elif args.orbit_files:
        orbit_dir = args.orbit_files if isinstance(args.orbit_files, str) else None
        if hasattr(downloader, "download_orbit"):
            downloader.download_orbit(save_dir=orbit_dir)
        else:
            print("[WARNING] This downloader does not support orbit file download.", file=sys.stderr)


def cmd_processor(args, extra_args: list[str]):
    from insarhub import Processor
    from insarhub.core.base import Hyp3Processor, LocalProcessor

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
    is_hyp3  = issubclass(processor_cls, Hyp3Processor)
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
            values = _read_config_json(cfg_path) if cfg_path else {}
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
        _LOCAL_ACTIONS = {"submit", "refresh", "retry", "watch"}
        if action == "submit":
            _proc_local_submit(args, extra_args)
        elif action == "refresh":
            _proc_local_refresh(args)
        elif action == "retry":
            _proc_local_retry(args)
        elif action == "watch":
            _proc_local_watch(args)
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
        saved_cfg = _read_config_json(cfg_path)
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
    # Strip metadata keys that are not dataclass fields
    overrides: dict = {k: v for k, v in saved_cfg.items() if k not in ("processor_type", "name")}
    config_cls = getattr(processor_cls, "default_config", None)
    if config_cls is not None and dataclasses.is_dataclass(config_cls):
        config_parser = _build_config_parser(config_cls, skip_fields=_SUBMIT_SKIP_FIELDS)
        config_ns, unknown = config_parser.parse_known_args(extra_args)
        if unknown:
            print(f"[ERROR] Unknown flags for '{processor_name}': {unknown}", file=sys.stderr)
            sys.exit(1)
        for f in dataclasses.fields(config_cls):
            if f.name in _SUBMIT_SKIP_FIELDS:
                continue
            val = getattr(config_ns, f.name, None)
            if val is not getattr(config_parser, '_unset_sentinel', None) and val is not None:
                overrides[f.name] = val
    elif extra_args:
        print(f"[WARNING] Extra args ignored (no config dataclass): {extra_args}", file=sys.stderr)

    overrides["name_prefix"] = args.name_prefix
    overrides["max_workers"] = args.max_workers

    pool = _load_credential_pool(getattr(args, "credential_pool", None))
    if pool:
        overrides["earthdata_credentials_pool"] = pool

    dry_run = getattr(args, "dry_run", False)

    # On dry-run: preview submission — find every process directory at workdir level
    # and one level of subdirectories, write processor config into insarhub_config.json.
    if dry_run:
        from insarhub.app.state import write_insarhub_config
        _skip_write = _SUBMIT_SKIP_FIELDS | {"earthdata_credentials_pool", "workdir", "pairs"}
        _preview_overrides = {k: v for k, v in overrides.items()
                              if k not in _SUBMIT_SKIP_FIELDS | {"name", "config"}}

        def _is_process_dir(d: Path) -> bool:
            return (d / "insarhub_config.json").exists() or (d / "downloader_config.json").exists()

        def _stamp_process_dir(d: Path) -> None:
            try:
                _preview_proc = Processor.create(processor_name, workdir=d,
                                                 pairs=[], **_preview_overrides)
                proc_cfg = {f.name: getattr(_preview_proc.config, f.name)
                            for f in dataclasses.fields(_preview_proc.config)
                            if f.name not in _skip_write}
                write_insarhub_config(d, {"processor": {"type": processor_name, "config": proc_cfg}})
                print(f"[dry-run] {d}")
                print(f"[dry-run]   wrote  insarhub_config.json  processor={processor_name}")
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
        processor = Processor.create(processor_name, **group_overrides)
        # Write full resolved config into insarhub_config.json (consistent with GUI)
        _skip_write = _SUBMIT_SKIP_FIELDS | {"earthdata_credentials_pool", "workdir", "pairs"}
        from insarhub.app.state import write_insarhub_config as _wic
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
    for job_dir in _iter_job_dirs(workdir, args.job_file):
        for jf in _find_job_files(job_dir, args.job_file):
            tag = f"[{job_dir.name}/{jf.name}] " if job_dir != workdir else f"[{jf.name}] "
            print(f"{tag}Refreshing…")
            processor = _load_hyp3_processor(job_dir, job_file=jf, processor_name=processor_name)
            _fail(RefreshCommand(processor).run(), f"refresh {tag}".strip())


def _proc_download_results(args):
    from insarhub.commands import RefreshCommand, DownloadResultsCommand
    processor_name = getattr(args, "processor_name", "Hyp3_S1")
    workdir = _resolve_workdir(args.workdir)
    for job_dir in _iter_job_dirs(workdir, args.job_file):
        for jf in _find_job_files(job_dir, args.job_file):
            tag = f"[{job_dir.name}/{jf.name}] " if job_dir != workdir else f"[{jf.name}] "
            print(f"{tag}Downloading results…")
            processor = _load_hyp3_processor(job_dir, job_file=jf, processor_name=processor_name)
            RefreshCommand(processor).run()
            _fail(DownloadResultsCommand(processor).run(), f"download {tag}".strip())


def _proc_retry(args):
    from insarhub.commands import RetryCommand
    processor_name = getattr(args, "processor_name", "Hyp3_S1")
    workdir = _resolve_workdir(args.workdir)
    for job_dir in _iter_job_dirs(workdir, args.job_file):
        for jf in _find_job_files(job_dir, args.job_file):
            tag = f"[{job_dir.name}/{jf.name}] " if job_dir != workdir else f"[{jf.name}] "
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
    for job_dir in _iter_job_dirs(workdir, args.job_file):
        for jf in _find_job_files(job_dir, args.job_file):
            entries.append((job_dir, jf, _load_hyp3_processor(job_dir, job_file=jf,
                                                               processor_name=processor_name)))

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
                        for j in new:
                            downloaded[jf].add(j.job_id)

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
                        processor.download()
                    processor.batchs = old_batchs

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

    processor_name = getattr(args, "processor_name", "ISCE_S1")
    processor_cls  = Processor._registry[processor_name]
    workdir        = _resolve_workdir(args.workdir)

    _cfg = args.config if (args.config and args.config != "__default__") else None
    _default_cfg_requested = (args.config == "__default__")
    if _cfg:
        cfg_path  = Path(_cfg).expanduser().resolve()
        saved_cfg = _read_config_json(cfg_path)
    elif _default_cfg_requested:
        _direct  = workdir / "processor_config.json"
        cfg_path = _direct if _direct.exists() else _find_subfolder_config(workdir, "processor_config.json")
        saved_cfg = _read_config_json(cfg_path) if cfg_path else {}
    else:
        saved_cfg = {}

    config_cls = getattr(processor_cls, "default_config", None)
    overrides: dict = {}
    if config_cls is not None and dataclasses.is_dataclass(config_cls):
        config_parser = _build_config_parser(config_cls, skip_fields=_SUBMIT_SKIP_FIELDS)
        config_ns, unknown = config_parser.parse_known_args(extra_args)
        if unknown:
            print(f"[ERROR] Unknown flags: {unknown}", file=sys.stderr)
            sys.exit(1)
        overrides = {k: v for k, v in vars(config_ns).items() if v is not None}

    merged = {**saved_cfg, **overrides, "workdir": str(workdir)}

    processor = Processor.create(processor_name, **{
        k: v for k, v in merged.items()
        if (config_cls is None or hasattr(config_cls, k))
    })

    pairs_path = getattr(args, "pairs", None) or merged.get("pairs")
    if pairs_path:
        pairs_path = Path(pairs_path).expanduser().resolve()
        import json
        data = json.loads(pairs_path.read_text())
        pairs = data if isinstance(data, list) else data.get("pairs", [])
    else:
        pairs = None

    processor.submit(pairs=pairs)
    processor.save()


def _proc_local_refresh(args):
    import json
    from insarhub import Processor

    processor_name = getattr(args, "processor_name", "ISCE_S1")
    workdir        = _resolve_workdir(args.workdir)
    jobs_path      = _find_jobs_file(workdir, pattern="isce_jobs*.json")
    if jobs_path is None:
        print("[ERROR] No isce_jobs.json found. Run submit first.", file=sys.stderr)
        sys.exit(1)

    processor = Processor.create(processor_name, saved_job_path=str(jobs_path), workdir=str(workdir))
    processor.refresh()


def _proc_local_retry(args):
    from insarhub import Processor

    processor_name = getattr(args, "processor_name", "ISCE_S1")
    workdir        = _resolve_workdir(args.workdir)
    jobs_path      = _find_jobs_file(workdir, pattern="isce_jobs*.json")
    if jobs_path is None:
        print("[ERROR] No isce_jobs.json found. Run submit first.", file=sys.stderr)
        sys.exit(1)

    processor = Processor.create(processor_name, saved_job_path=str(jobs_path), workdir=str(workdir))
    processor.retry()


def _proc_local_watch(args):
    from insarhub import Processor

    processor_name    = getattr(args, "processor_name", "ISCE_S1")
    workdir           = _resolve_workdir(args.workdir)
    refresh_interval  = getattr(args, "refresh_interval", 60)
    jobs_path         = _find_jobs_file(workdir, pattern="isce_jobs*.json")
    if jobs_path is None:
        print("[ERROR] No isce_jobs.json found. Run submit first.", file=sys.stderr)
        sys.exit(1)

    processor = Processor.create(processor_name, saved_job_path=str(jobs_path), workdir=str(workdir))
    processor.watch(refresh_interval=refresh_interval)


def cmd_analyzer(args, extra_args: list[str]):
    from insarhub import Analyzer

    # --list-analyzers (works without a sub-action)
    if args.list_analyzers:
        print("Available analyzers:")
        for name in Analyzer.available():
            print(f"  {name}")
        return

    analyzer_name = getattr(args, "analyzer_name", "Hyp3_SBAS")

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
    from insarhub.commands import PrepDataCommand, AnalyzeCommand

    analyzer_cls = Analyzer._registry[args.analyzer_name]

    overrides: dict = {}
    config_cls = getattr(analyzer_cls, "default_config", None)
    if config_cls is not None and dataclasses.is_dataclass(config_cls):
        # Collect overrides from extra_args (flags passed after subcommand)
        config_parser = _build_config_parser(config_cls, skip_fields=_ANALYZER_SKIP_FIELDS)
        config_ns, unknown = config_parser.parse_known_args(extra_args)
        if unknown:
            print(f"[ERROR] Unknown flags for '{args.analyzer_name}': {unknown}",
                  file=sys.stderr)
            sys.exit(1)
        for f in dataclasses.fields(config_cls):
            if f.name in _ANALYZER_SKIP_FIELDS:
                continue
            val = getattr(config_ns, f.name, None)
            if val is not getattr(config_parser, '_unset_sentinel', None) and val is not None:
                overrides[f.name] = val
        # Also collect overrides from args (pre-registered flags on p_analyzer, no subcommand)
        for f in dataclasses.fields(config_cls):
            if f.name in _ANALYZER_SKIP_FIELDS or f.name in overrides:
                continue
            val = getattr(args, f.name, None)  # only present if user explicitly set it (SUPPRESS default)
            if val is not None:
                overrides[f.name] = val
    elif extra_args:
        print(f"[WARNING] Extra args ignored (no config dataclass): {extra_args}",
              file=sys.stderr)

    run_prep = False
    mintpy_steps: list[str] | None = None
    steps = getattr(args, 'step', None) or ['all']  # default: run everything including prep
    expanded: list[str] = []
    for s in steps:
        if s == 'prep':
            run_prep = True
        elif s == 'all':
            run_prep = True
            expanded.extend(_MINTPY_ALL_STEPS)
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
                          f"Run '--step prep' first.", file=sys.stderr)
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
            if not cfg_path.exists():
                print(f"\n[WARNING] No .mintpy.cfg found in [{label}]. "
                      f"Run 'insarhub analyzer -N {args.analyzer_name} -w {analysis_dir} run --step prep' first.\n",
                      file=sys.stderr)
                continue
            if overrides:
                _update_mintpy_cfg(cfg_path, overrides)
            values = _read_mintpy_cfg(cfg_path)
            _print_config_options(config_cls,
                                  display_label=f"{args.analyzer_name} [{label}]",
                                  skip_fields=_ANALYZER_SKIP_FIELDS,
                                  value_overrides=values)
        return

    overrides["debug"] = getattr(args, "debug", False)
    workdir = _resolve_workdir(args.workdir)

    # Build ordered step list for display
    display_steps = (["prep"] if run_prep else []) + (mintpy_steps or [])
    total = len(display_steps)

    for analysis_dir in _iter_analysis_dirs(workdir):
        tag = f"[{analysis_dir.name}] " if analysis_dir != workdir else ""
        if tag:
            print(f"\n{tag}Starting analysis...")
        analyzer = Analyzer.create(args.analyzer_name, workdir=analysis_dir, **overrides)

        step_num = 1
        if run_prep:
            print(f"\nStep {step_num}/{total}: prep_data")
            step_num += 1
            result = PrepDataCommand(analyzer).run()
            _fail(result, f"prep {tag}".strip())
            if mintpy_steps is None:
                continue  # only 'prep' was requested for this dir

        for step in (mintpy_steps or []):
            print(f"\nStep {step_num}/{total}: {step}")
            step_num += 1
            result = AnalyzeCommand(analyzer, steps=[step]).run()
            _fail(result, f"{step} {tag}".strip())


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
