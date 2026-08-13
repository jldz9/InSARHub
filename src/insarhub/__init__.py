#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import platform
import sys
import logging

from pathlib import Path
from colorama import init
init(autoreset=True)
from colorama import Fore, Style, Back

# NOTE: do not reintroduce `logging.disable(logging.CRITICAL)` here. It was a
# process-global kill switch -- not scoped to InSARHub's own loggers, and not
# undoable by a caller's own logging setup -- so it silently swallowed every
# logger.info/warning/error in the entire codebase (and any host application's
# too, since importing insarhub applied it to their loggers as well). Whole
# commands looked like no-ops as a result: `processor ... cancel` scancelled
# every job and printed nothing at all. It also wasn't buying anything -- with
# it removed, importing insarhub and every heavy submodule emits no log records
# whatsoever, even at DEBUG. (The one visible import-time line, pyproj's
# "unable to set PROJ database path", comes from the warnings module, which
# logging.disable() never affected.) Silence third-party chatter by setting a
# level on that library's own logger instead; cli/main.py's main() configures
# the CLI's levels (root WARNING, insarhub INFO).
from insarhub._version import __version__
_system_info = platform.system()

# ---------------------MintPy Configuration-----------------------------------
# MintPy is a required dependency.
# Configuration followed the MintPy post-installation setup
# https://github.com/insarlab/MintPy/blob/main/docs/installation.md#3-post-installation-setup
import mintpy

# b. Dask for parallel processing (MintPy's compute_cluster option)
from dask import config as dask_config
tmp_dir = Path.home().joinpath('.dask','tmp')
tmp_dir.mkdir(parents=True, exist_ok=True)
dask_config.set({'temporary_directory':str(tmp_dir)})

# c. Extra environment variables setup
os.environ["VRT_SHARED_SOURCE"] = "0"
os.environ["HDF5_DISABLE_VERSION_CHECK"] = "2"
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

# ---------------------PROJ data-dir sanity (conda envs)----------------------
# MUST run before rasterio/GDAL/pyproj are imported anywhere below.
#
# conda-forge's proj package ships etc/conda/activate.d/proj4-activate.sh,
# which unconditionally exports
#     PROJ_DATA="${CONDA_PREFIX}/share/proj"
# on `conda activate`. GDAL, rasterio and pyproj all honour that variable.
# The problem is that an environment can easily end up with a share/proj that
# is OLDER than the libproj those wheels are linked against -- e.g. a GMTSAR
# env where gmt/gdal pinned proj 9.2 (proj.db layout 1.2) while rasterio
# bundles 9.7 (layout 1.6). libproj refuses to read a database whose layout
# predates it, so every CRS call dies with
#     "proj.db contains DATABASE.LAYOUT.VERSION.MINOR = 2 whereas a number
#      >= 6 is expected. It comes from another PROJ installation."
# and, because dem_stitcher builds a CRS at import time, `import insarhub`
# itself fails. Unset, the same env works fine: each library falls back to
# its own bundled, self-consistent proj.db.
#
# So: pick the NEWEST proj.db available, and only point PROJ_DATA at a
# directory if it is at least as new as what the libraries bundle. When the
# activated environment's copy is the stale one, drop the variable entirely
# rather than guessing a replacement.
def _proj_db_layout(db: Path) -> tuple[int, int] | None:
    """(major, minor) of a proj.db's DATABASE.LAYOUT.VERSION, or None."""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            meta = dict(con.execute("select key, value from metadata").fetchall())
        finally:
            con.close()
        return (int(meta["DATABASE.LAYOUT.VERSION.MAJOR"]),
                int(meta["DATABASE.LAYOUT.VERSION.MINOR"]))
    except Exception:
        return None


def _sanitize_proj_data() -> None:
    # NOT Path(__file__).parent.parent -- under `pip install -e .` that is the
    # source checkout's src/, where none of these packages live, so every
    # bundled database would be "missing" and the stale one would win.
    import sysconfig
    site = Path(sysconfig.get_paths()["purelib"])
    candidates = [Path(p) for p in (os.environ.get("PROJ_DATA"),
                                    os.environ.get("PROJ_LIB")) if p]
    candidates.append(Path(sys.prefix) / "share" / "proj")
    bundled = [site / pkg / sub for pkg, sub in
               (("rasterio", "proj_data"), ("pyogrio", "proj_data"),
                ("pyproj", "proj_dir/share/proj"))]

    best_bundled = max(
        ((d, v) for d in bundled
         if (v := _proj_db_layout(d / "proj.db")) is not None),
        key=lambda t: t[1], default=(None, None))[1]

    for cand in candidates:
        ver = _proj_db_layout(cand / "proj.db")
        if ver is None:
            continue
        if best_bundled is None or ver >= best_bundled:
            # Usable: make every library agree on it.
            os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = str(cand)
            return
        break   # first existing candidate is stale -> fall through

    # Nothing usable was pointed at: remove the stale pointers so each
    # library uses its own bundled database.
    for var in ("PROJ_DATA", "PROJ_LIB"):
        os.environ.pop(var, None)


_sanitize_proj_data()

# ---------------------Check runing environment -----------
if 'SLURM_MEM_PER_NODE' in os.environ:
    if int(os.environ['SLURM_MEM_PER_NODE'])<=512:
        # Value is small, assuming GB
        _memory_gb = int(os.environ['SLURM_MEM_PER_NODE'])
    elif int(os.environ['SLURM_MEM_PER_NODE'])>512&int(os.environ['SLURM_MEM_PER_NODE'])<=524288:
        # This range of value would assume to be MB
        _memory_gb = int(int(os.environ['SLURM_MEM_PER_NODE'])/1024)
    elif int(os.environ['SLURM_MEM_PER_NODE'])>524288:
        # Value is too large, assume mem is KB
    
        _memory_gb = int(int(os.environ['SLURM_MEM_PER_NODE'])/(1024**2))
    _cpu_core = int(os.environ['SLURM_CPUS_PER_TASK'])
    _manager = 'slurm'
elif 'PBS_NUM_PPN' in os.environ:
    _memory_gb = int(int(os.environ['PBS_MEM']))
    _cpu_core = int(os.environ['PBS_NUM_PPN'])
    _manager = 'pbs'
elif 'LSB_JOB_NUMPROC' in os.environ:
    _memory_gb = int(int(os.environ['LSB_JOB_MEMLIMIT'])/1024)
    _cpu_core = int(os.environ['LSB_JOB_NUMPROC '])
    _manager = 'lsf'
else:
    import psutil
    _memory_gb = round(psutil.virtual_memory().total/1024**3)
    _cpu_core = os.cpu_count()
    _manager = 'local'

_env = {
        "memory": _memory_gb,
        "cpu": _cpu_core,
        "manager": _manager,
        "system": _system_info,

    }
# ---------------------package imports---------------------

from .core.registry import (
    Downloader,
    Processor,
    Analyzer,
)

from .core.base import (
    BaseDownloader,
    LocalProcessor,
    CloudProcessor,
    BaseAnalyzer,
)

from .config.defaultconfig import (
    ASF_Base_Config,
    Hyp3_Base_Config,
    Mintpy_SBAS_Base_Config,
    S1_SLC_Config
)   

from .downloader import (
    ASF_Base_Downloader,
    S1_SLC,
)

from .processor import (
    Hyp3_S1,
    ISCE2_S1,
)

from .analyzer import (
    Mintpy_SBAS_Base_Analyzer,
    Hyp3_SBAS,
    Hyp3_SBAS_Config,
    ISCE_SBAS,
)

from .downloader.s1_slc import S1_SLC_Config

from .utils import (
    tool,
    postprocess,
    batch
)

__all__ = [
    "BaseDownloader",
    "LocalProcessor",
    "CloudProcessor",
    "BaseAnalyzer",
    "Downloader",
    "Processor",
    "Analyzer",
    "ASF_Base_Config",
    "ASF_Base_Downloader",
    "S1_SLC",
    "S1_SLC_Config",
    "Hyp3_S1", 
    "Mintpy_SBAS_Base_Config",
    "Mintpy_SBAS_Base_Analyzer",
    "Hyp3_SBAS",
    "Hyp3_SBAS_Config",

]



