# -*- coding: utf-8 -*-
"""
GMTSAR_Mintpy_SBAS — MintPy SBAS on a GMTSAR stack_mode stack.

Hands the coherent stack GMTSAR_S1 (stack_mode=True) produces
(workdir/gmtsar/: baseline_table.dat + per-pair geocoded *_ll.grd) to MintPy
via its own prep_gmtsar.py loader + smallbaselineApp. Mirror of ISCE2_Mintpy_SBAS /
Hyp3_Mintpy_SBAS, differing only in _set_load_parameters() (wires the mintpy.load.*
keys prep_gmtsar.py reads from GMTSAR's output layout). Output → workdir/mintpy/.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from colorama import Fore

from insarhub.config import GMTSAR_Mintpy_SBAS_Config
from insarhub.config.paths import GMTSARPaths
from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer

logger = logging.getLogger(__name__)


class GMTSAR_Mintpy_SBAS(Mintpy_SBAS_Base_Analyzer):
    name                 = "GMTSAR_Mintpy_SBAS"
    aliases              = ("GMTSAR_MINTPY_SBAS", "GMTSAR_MINTPY_TS", "GMTSAR_Mintpy_TS")   # legacy names
    description          = "SBAS time-series of a GMTSAR stack_mode stack using MintPy (prep_gmtsar.py)."
    compatible_processor = "GMTSAR_S1"
    default_config       = GMTSAR_Mintpy_SBAS_Config
    # own output dir (workdir/gmtsar_mintpy/) -- separate from Hyp3_Mintpy_SBAS/
    # ISCE2_Mintpy_SBAS runs on the same workdir; layout via MintPyPaths
    MINTPY_SUBDIR        = "gmtsar_mintpy"

    def __init__(self, config: GMTSAR_Mintpy_SBAS_Config | None = None):
        super().__init__(config)
        self._gmtsar_paths = GMTSARPaths(Path(self.workdir))
        #: subswath _collect_date_prms pinned the baselines to (p2p only)
        self._baseline_swath: int | None = None

    @property
    def stack_dir(self) -> Path:
        """Where GMTSAR_S1 stack_mode wrote its output."""
        return self._gmtsar_paths.case_dir

    #: The acquisition start time in a .SAFE name: S1A_..._20210108T133459_...
    _SAFE_DATE_RE = re.compile(r"(\d{8})T\d{6}")

    @staticmethod
    def _to_gmtsar_julian(yyyymmdd: str) -> str:
        """``20210108`` -> ``2021007``, GMTSAR's pair-directory naming.

        The day field is the offset from Jan 1, not the 1-based day of year:
        8 Jan is 007, matching what preproc_batch_tops writes and what
        MintPy's ptime.yyyyddd2yyyymmdd() reads back.
        """
        from datetime import date
        d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
        return f"{d.year}{(d - date(d.year, 1, 1)).days:03d}"

    def _p2p_product_dir(self) -> Path | None:
        """Stage p2p output into the ``<dir>/<pair>/unwrap_ll.grd`` shape both
        MintPy and _consistent_intf_dir expect. None when this isn't p2p output.

        The two modes put the merged product at different depths::

            stack_mode   merge/2021007_2021019/unwrap_ll.grd
            p2p          gmtsar/<ref>.SAFE_<sec>.SAFE/merge/unwrap_ll.grd

        because p2p_S1_TOPS_Frame always writes merge/ into its own cwd, so
        GMTSAR_S1 gives every pair its own case dir to stop pair 2 overwriting
        pair 1. That extra level means the ``<intf>/*/unwrap_ll.grd`` glob
        matches nothing at gmtsar/ (too shallow) and exactly one pair at a case
        dir. Symlinking sidesteps it without copying multi-GB grids.

        The staged directories MUST keep GMTSAR's Julian ``yyyyddd_yyyyddd``
        naming. prep_gmtsar derives the pair from the directory name and
        converts it itself::

            date1, date2 = os.path.basename(ifg_dir).split('_')
            date1 = ptime.yyyyddd2yyyymmdd(date1)

        so a calendar-named ``20210108_20210120`` is read as year 2021 day 120
        -> 20210501, which is not in the baseline table: ``KeyError: '20210501'``.
        (Tried it -- naming them by real dates looks tidier and would also fix
        MintPy's skip_files_with_inconsistent_size(), which matches ``yymmdd``
        against the path and so never matches a Julian directory. But that
        function is a nicety and prep_gmtsar is mandatory, so Julian wins.)
        """
        import shutil

        found = sorted(self.stack_dir.glob("*/merge/unwrap_ll.grd"))
        if not found:
            return None
        dest = self.mintpy_dir / "p2p_pairs"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        staged = 0
        for unw in found:
            case = unw.parent.parent
            # 4 timestamps in "<ref>.SAFE_<sec>.SAFE" (start/stop each); the
            # two starts are the pair's dates.
            dates = self._SAFE_DATE_RE.findall(case.name)
            if len(dates) < 3:
                logger.warning("p2p: no date pair in case name %r; skipped", case.name)
                continue
            out = dest / f"{self._to_gmtsar_julian(dates[0])}_" \
                         f"{self._to_gmtsar_julian(dates[2])}"
            out.mkdir(exist_ok=True)
            ok = True
            for name in ("unwrap_ll.grd", "corr_ll.grd"):
                src = unw.parent / name
                if not src.exists():
                    logger.warning("p2p: %s has no %s; pair skipped", case.name, name)
                    ok = False
                    break
                (out / name).symlink_to(src)
            if ok:
                staged += 1
            else:
                shutil.rmtree(out, ignore_errors=True)

        if not staged:
            return None
        print(f"{Fore.CYAN}p2p layout: staged {staged} pair(s) as "
              f"<date>_<date>/ under {dest}{Fore.RESET}")
        return dest

    def _product_dir(self) -> Path:
        """The per-pair product dir for either mode (p2p staged, else native)."""
        if getattr(self, "_product_dir_cache", None) is None:
            self._product_dir_cache = (self._p2p_product_dir()
                                       or self._gmtsar_paths.product_dir())
        return self._product_dir_cache

    def prep_data(self) -> None:
        """Auto-discover the GMTSAR stack outputs and write the MintPy config."""
        if self.config.container:
            return self._run_via_container(["prep_data"])

        stack = self.stack_dir
        # preproc_batch_tops writes baseline_table.dat into raw/, not the root.
        baseline = self._gmtsar_paths.baseline_table_auto
        intf_dir = self._product_dir()
        if not baseline.exists():
            # p2p mode never produces one: baseline_table.dat is written by
            # stack_mode's preproc_batch_tops, which is the only stage that
            # sees every date at once. Build it from the per-date PRMs the
            # p2p pairs already left behind rather than requiring stack_mode,
            # which the GMTSAR developers advise against for interferogram
            # formation. Measured on pair 2021127_2021139, processed both
            # ways on p100_f466: 24 azimuth seam rows (>5 sigma) for
            # stack_mode vs 6 for p2p, and the 6 are the frame-edge pair
            # both modes share -- so 18 interior burst seams vs 0. Coherence
            # was equivalent (0.628 vs 0.634), so this is alignment, not SNR.
            built = self._build_baseline_table(baseline)
            if not built:
                raise FileNotFoundError(
                    f"{baseline} not found and could not be built from the "
                    f"per-date PRMs. MintPy's prep_gmtsar needs one row per "
                    f"date (file_ID, yyyyddd.fraction, day_cnt, b_para, "
                    f"b_perp). Either run GMTSAR_S1 with stack_mode=True, or "
                    f"check that the pairs left per-date PRMs behind "
                    f"(S1_<date>_ALL_F<n>.PRM in stack_mode, "
                    f"S1_<date>_<time>_F<n>.PRM in p2p)."
                )
        pairs = sorted(d for d in intf_dir.iterdir() if d.is_dir()) if intf_dir.exists() else []
        if not pairs:
            raise FileNotFoundError(
                f"No interferogram directories in {intf_dir}. GMTSAR_S1 "
                "stack_mode must reach the intf stage first."
            )
        print(f"{Fore.CYAN}Found {len(pairs)} interferogram pair(s). "
              f"Configuring MintPy (prep_gmtsar) load paths…{Fore.RESET}")
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
        self._set_load_parameters()
        super().prep_data()   # writes self.cfg_path
        self._ensure_prep_gmtsar_inputs()

    # Two different PRM namings, because the two GMTSAR entry points name
    # them differently:
    #   stack_mode  preproc_batch_tops -> S1_20210108_ALL_F1.PRM
    #   p2p (Frame) p2p_S1_TOPS_Frame  -> S1_20210108_133500_F1.PRM
    # Matching only the first is why MintPy SBAS could never run on p2p
    # output: the glob returned nothing, _build_baseline_table bailed at
    # "need at least 2", and no baseline_table.dat was ever written.
    _PRM_DATE_RE = re.compile(r"_(\d{8})_(?:ALL|\d{6})_F(\d)")

    def _collect_date_prms(self) -> dict[str, Path]:
        """``{YYYYMMDD: PRM}`` -- one PRM per acquisition date.

        p2p processes each pair in its own directory, so the same date's PRM
        appears once per pair it takes part in. Any copy will do: baselines are
        computed from the ORBIT state vectors, which belong to the date itself,
        not to whichever pair happened to produce that copy. Alignment changes
        rshift/ashift, not the orbit -- so this stays correct even though p2p
        aligns each pair to its own master.
        """
        by_swath: dict[str, dict[str, Path]] = {}
        for prm in sorted(self.stack_dir.rglob("*.PRM")):
            m = self._PRM_DATE_RE.search(prm.name)
            if not m:
                continue      # e.g. topo/master.PRM -- no date in the name
            date, swath = m.group(1), m.group(2)
            slot = by_swath.setdefault(swath, {})
            # Prefer the raw/ copy: it carries the orbit straight off the EOF,
            # and its .LED sits beside it (baseline_table.csh resolves led_file
            # relative to the PRM, and we run it with cwd=the PRM's parent).
            if date not in slot or (prm.parent.name == "raw"
                                    and slot[date].parent.name != "raw"):
                slot[date] = prm
        if not by_swath:
            return {}
        # Pin to ONE subswath. b_perp depends on look geometry, which differs
        # slightly between IWs, so taking F1 for one date and F2 for another
        # would fold that difference into the baselines. Best-covered wins.
        swath = max(by_swath, key=lambda s: len(by_swath[s]))
        # Recorded so _set_load_parameters can pull the metadata PRM from the
        # same subswath -- metadata and baselines should describe one geometry.
        self._baseline_swath = int(swath)
        return by_swath[swath]

    def _build_baseline_table(self, dest: Path) -> bool:
        """Write ``baseline_table.dat`` from per-date PRMs via GMTSAR's own
        ``baseline_table.csh``.

        The reference is the EARLIEST date, matching what preproc_batch_tops
        uses, so a table built here is interchangeable with a stack-mode one.
        Note the reference only sets the zero point of the perpendicular
        baselines -- it is a geometry calculation, and nothing is resampled --
        which is why this works for p2p output that has no common alignment
        master at all. MintPy reads only the date and b_perp columns.

        Every PRM is staged into one directory alongside its ``.LED`` first.
        ``baseline_table.csh`` resolves each PRM's ``led_file`` (a bare
        filename) relative to the WORKING directory, and p2p keeps each date's
        PRM in its own case dir -- so calling it across directories silently
        finds no orbit for the secondary and prints 3 columns instead of 5.
        MintPy then reads the table with ``usecols=(1, 4)`` and dies with
        ``invalid column index 4 ... with 3 columns``, having already been told
        by load_data that "prep_gmtsar.py failed, assuming its result exists"
        -- so the real cause surfaces much later as ``KeyError: 'DATE12'``.
        """
        import shutil, subprocess, tempfile

        prms = self._collect_date_prms()
        if len(prms) < 2:
            logger.warning("GMTSAR_Mintpy_SBAS: found %d date PRM(s) under %s; "
                           "need at least 2 to build a baseline table",
                           len(prms), self.stack_dir)
            return False

        tmpdir = Path(tempfile.mkdtemp(prefix="baseline_", dir=str(self.mintpy_dir)))
        staged: dict[str, Path] = {}
        for date, src in prms.items():
            led = src.with_suffix(".LED")
            if not led.exists():
                logger.warning("GMTSAR_Mintpy_SBAS: %s has no .LED beside it; "
                               "baselines for %s would be wrong", src.name, date)
                shutil.rmtree(tmpdir, ignore_errors=True)
                return False
            shutil.copy(src, tmpdir / src.name)
            shutil.copy(led, tmpdir / led.name)
            staged[date] = tmpdir / src.name
        prms = staged

        ref_date = min(prms)
        ref_prm = prms[ref_date]
        print(f"{Fore.CYAN}Building baseline_table.dat from {len(prms)} date "
              f"PRM(s), reference {ref_date}…{Fore.RESET}")

        rows: list[str] = []
        try:
            for d in sorted(prms):
                p = subprocess.run(
                    ["baseline_table.csh", ref_prm.name, prms[d].name],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=str(tmpdir))
                line = (p.stdout or "").strip()
                if p.returncode != 0 or not line:
                    logger.error("GMTSAR_Mintpy_SBAS: baseline_table.csh failed "
                                 "for %s: %s", d, (p.stderr or p.stdout or "")[-300:])
                    return False
                row = line.splitlines()[0]
                # 5 columns: file_ID, yyyyddd.frac, day_cnt, b_para, b_perp.
                # Fewer means the orbit was not resolved and the baselines are
                # simply absent -- catch it here rather than letting MintPy hit
                # "invalid column index 4" three steps downstream.
                if len(row.split()) < 5:
                    logger.error(
                        "GMTSAR_Mintpy_SBAS: baseline_table.csh returned %d "
                        "columns for %s (need 5). The .LED beside %s is "
                        "probably unreadable:\n  %s",
                        len(row.split()), d, prms[d].name, row[:160])
                    return False
                rows.append(row)

            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("\n".join(rows) + "\n")
            print(f"{Fore.GREEN}  wrote {dest} ({len(rows)} dates, "
                  f"{len(rows[0].split())} columns){Fore.RESET}")
            return True
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _ensure_prep_gmtsar_inputs(self) -> None:
        """Two extra inputs MintPy's prep_gmtsar.py requires that GMTSAR
        stack_mode doesn't produce (found via a real failed run):

        1. config.<SAT>.txt next to the metaFile PRM -- prep_gmtsar reads
           filter_wavelength from it to compute ALOOKS/RLOOKS. GMTSAR_S1
           writes batch_tops.config instead, so mirror the value into the
           file prep_gmtsar globs for.
        2. a bare HEADING key in the template -- not derivable from GMTSAR's
           PRM (orbdir=D/A only, no angle). Use MintPy's own canonical
           values for Sentinel-1 IW (utils0.py): -168 deg descending,
           -12 deg ascending, picked from the PRM's orbdir.
        """
        from pathlib import Path
        meta = Path(self.config.load_metaFile)
        raw = meta.parent

        if not list(raw.glob("config.*.txt")):
            fw = "200"
            btc = self._gmtsar_paths.batch_config
            srcs = [btc] if btc.exists() else []
            # p2p writes config.py per case instead of batch_tops.config, so
            # without this the hardcoded 200 was used regardless of the real
            # setting -- and MintPy derives ALOOKS/RLOOKS from this value. A
            # stack processed at 320 was being described to MintPy as 200.
            srcs += sorted(self.stack_dir.glob("*/config.py"))[:1]
            for src in srcs:
                for l in src.read_text().splitlines():
                    if l.strip().startswith("filter_wavelength"):
                        fw = l.split("=")[1].strip()
                        break
                else:
                    continue
                break
            (raw / "config.S1_TOPS.txt").write_text(f"filter_wavelength = {fw}\n")
            print(f"  wrote {raw / 'config.S1_TOPS.txt'} (filter_wavelength = {fw})")

        cfg_text = self.cfg_path.read_text() if self.cfg_path.exists() else ""
        if "HEADING" not in cfg_text:
            orbdir = "D"
            if meta.exists():
                for l in meta.read_text().splitlines():
                    if l.strip().startswith("orbdir"):
                        orbdir = l.split("=")[1].strip().upper()[:1]
                        break
            heading = -168.0 if orbdir == "D" else -12.0
            with open(self.cfg_path, "a") as f:
                f.write(f"HEADING = {heading}\n")
            print(f"  appended HEADING = {heading} (orbdir={orbdir}) to {self.cfg_path}")

    def _gdal_env(self) -> dict:
        """Bare subprocess env lacks the conda activation vars gdal/gmt need
        to resolve EPSG codes (real failure: "proj_create_from_database:
        Open of .../share/proj failed")."""
        import os as _os, sys as _sys
        env = _os.environ.copy()
        envroot = None
        if getattr(self.config, "gmtsar_env_bin", None):
            envroot = str(Path(self.config.gmtsar_env_bin).parent)
            env["PATH"] = f"{self.config.gmtsar_env_bin}:" + env.get("PATH", "")
        # gmtsar_env_bin lives on the PROCESSOR config; the analyzer's own
        # config usually has none, which left PROJ_DATA unset and gdal_translate
        # failing with the very error this method exists to prevent. The
        # running interpreter is already inside the right env, so use its
        # prefix. PROJ_LIB as well as PROJ_DATA -- PROJ reads the old name.
        if envroot is None or not Path(f"{envroot}/share/proj").is_dir():
            envroot = _sys.prefix
        if Path(f"{envroot}/share/proj").is_dir():
            env.setdefault("PROJ_DATA", f"{envroot}/share/proj")
            env.setdefault("PROJ_LIB", f"{envroot}/share/proj")
        if Path(f"{envroot}/share/gdal").is_dir():
            env.setdefault("GDAL_DATA", f"{envroot}/share/gdal")
        return env

    @staticmethod
    def _grd_shape(grd: Path) -> tuple[int, int] | None:
        try:
            from netCDF4 import Dataset
            with Dataset(grd) as ds:
                for name in ("z", "Band1"):
                    if name in ds.variables:
                        return tuple(ds.variables[name].shape)
        except Exception:
            return None
        return None

    def _grd_region(self, grd: Path) -> tuple[float, float, float, float] | None:
        """(x_min, x_max, y_min, y_max) of a .grd."""
        try:
            from netCDF4 import Dataset
            with Dataset(grd) as ds:
                x = ds.variables.get("lon", ds.variables.get("x"))
                y = ds.variables.get("lat", ds.variables.get("y"))
                if x is None or y is None:
                    return None
                return (float(x[0]), float(x[-1]), float(y[0]), float(y[-1]))
        except Exception:
            return None

    def _grd_increments(self, grd: Path) -> tuple[float, float] | None:
        """(x_inc, y_inc) of a .grd."""
        try:
            from netCDF4 import Dataset
            with Dataset(grd) as ds:
                x = ds.variables.get("lon", ds.variables.get("x"))
                y = ds.variables.get("lat", ds.variables.get("y"))
                if x is None or y is None or len(x) < 2 or len(y) < 2:
                    return None
                return (abs(float(x[1]) - float(x[0])), abs(float(y[1]) - float(y[0])))
        except Exception:
            return None

    def _consistent_intf_dir(self, intf_all: Path) -> Path:
        """Return a directory whose */unwrap_ll.grd all share ONE common grid,
        clipping every pair to the common overlap when they don't already.

        GMTSAR geocodes each pair to its own valid-data EXTENT but the SAME grid
        (identical pixel size + registration -- origins differ only by whole
        pixels; measured here: 540x540 vs 540x550, y-origins exactly 10 pixels
        apart). MintPy's skip_files_with_inconsistent_size() detects the size
        mismatch but can't drop it (it matches `yymmdd`, and GMTSAR names dirs by
        Julian date), so the file loads anyway -> "can't broadcast".

        Because the pairs share one underlying grid, the reconciliation is an
        exact integer-pixel CLIP onto the intersection extent -- no resampling
        (this is what HyP3's prep does: clip products to their common overlap).
        The bogus header nodata (a plain number, the Julian ref date e.g.
        2024025 -- NOT NaN, which `gmt grdcut` mishandles into 50-70% spurious
        NaN that zeroes the inversion) is masked to real NaN in NumPy; the clip
        + netCDF write go through gdal ReadAsArray/Translate. A nearest-neighbour
        warp is used only as a fallback if a pair is ever sub-pixel-misaligned.
        (We avoid gdal.Warp with srcNodata+dstNodata: GDAL 3.6.3 segfaults on
        that exact combination.)
        """
        import shutil
        import numpy as np
        from osgeo import gdal
        gdal.UseExceptions()

        pairs = sorted(d for d in intf_all.iterdir() if d.is_dir())
        geo: dict[Path, tuple] = {}     # dir -> (geotransform, W, H)
        for d in pairs:
            u = d / "unwrap_ll.grd"
            if not u.exists():
                continue
            ds = gdal.Open(str(u))
            geo[d] = (ds.GetGeoTransform(), ds.RasterXSize, ds.RasterYSize)
            ds = None
        if not geo:
            return intf_all
        if len({(W, H) for _, W, H in geo.values()}) == 1:
            return intf_all             # already one grid -> raw grids are fine

        for d, (_, W, H) in geo.items():
            print(f"  pair {d.name}: {W}x{H}")

        # Common overlap = the INTERSECTION extent. GMTSAR geocodes every pair
        # to the SAME posting and grid registration (identical pixel size, and
        # origins that differ only by whole pixels), so the pairs share one
        # underlying grid and merely cover different extents. Reconcile them by
        # an exact integer-pixel CLIP onto the common overlap -- no resampling
        # (this is what HyP3's prep does: clip products to their common overlap).
        # Only if a pair turns out sub-pixel-misaligned do we fall back to a
        # nearest-neighbour warp.
        gt0 = next(iter(geo.values()))[0]
        px, py = gt0[1], gt0[5]                       # x_step (+), y_step (-)
        minx = max(gt[0] for gt, _, _ in geo.values())
        maxy = min(gt[3] for gt, _, _ in geo.values())
        maxx = min(gt[0] + w * gt[1] for gt, w, _ in geo.values())
        miny = max(gt[3] + h * gt[5] for gt, _, h in geo.values())
        W = int(round((maxx - minx) / abs(px)))
        H = int(round((maxy - miny) / abs(py)))
        aligned = all(
            abs((minx - gt[0]) / px - round((minx - gt[0]) / px)) < 1e-3
            and abs((maxy - gt[3]) / py - round((maxy - gt[3]) / py)) < 1e-3
            for gt, _, _ in geo.values())
        new_gt = (minx, px, 0.0, maxy, 0.0, py)
        print(f"  reconciling {len(geo)} pairs onto a common {W}x{H} grid "
              f"({'exact integer-pixel clip' if aligned else 'nearest-neighbour resample'})")

        links = self.clip_dir
        if links.exists():
            shutil.rmtree(links)
        links.mkdir(parents=True)

        kept = 0
        for d in geo:
            out = links / d.name
            out.mkdir()
            ok = True
            for name in ("unwrap_ll.grd", "corr_ll.grd"):
                src = d / name
                if not src.exists():
                    ok = False
                    break
                try:
                    # Invalid pixels carry a bogus NODATA value that is a plain
                    # number (the pair's Julian ref date, e.g. 2024001) declared
                    # in the header -- NOT NaN. Mask it to real NaN so MintPy/sbas
                    # treat it as no-data instead of ~2e6 "phase". (Do NOT use
                    # gdal.Warp with srcNodata+dstNodata: GDAL 3.6.3 here
                    # SEGFAULTs on that combo -- so we clip in NumPy and write
                    # netCDF via Translate, whose write path is stable.)
                    sd = gdal.Open(str(src))
                    nd = sd.GetRasterBand(1).GetNoDataValue()
                    gt = sd.GetGeoTransform()
                    if aligned:
                        xoff = int(round((minx - gt[0]) / px))
                        yoff = int(round((maxy - gt[3]) / py))
                        arr = sd.GetRasterBand(1).ReadAsArray(xoff, yoff, W, H)
                        sd = None
                        arr = arr.astype("float32")
                        if nd is not None:
                            arr = np.where(arr == nd, np.nan, arr)
                    else:
                        a = sd.GetRasterBand(1).ReadAsArray().astype("float32")
                        if nd is not None:
                            a = np.where(a == nd, np.nan, a)
                        src_mem = gdal.GetDriverByName("MEM").Create(
                            "", sd.RasterXSize, sd.RasterYSize, 1, gdal.GDT_Float32)
                        src_mem.SetGeoTransform(gt)
                        src_mem.GetRasterBand(1).WriteArray(a)
                        sd = None
                        w = gdal.Warp("", src_mem, format="MEM",
                                      outputBounds=(minx, miny, maxx, maxy),
                                      width=W, height=H, resampleAlg="near")
                        src_mem = None
                        arr = w.GetRasterBand(1).ReadAsArray()
                        w = None
                    m = gdal.GetDriverByName("MEM").Create("", W, H, 1, gdal.GDT_Float32)
                    m.SetGeoTransform(new_gt)
                    m.GetRasterBand(1).WriteArray(arr)
                    m.GetRasterBand(1).SetNoDataValue(float("nan"))
                    gdal.Translate(str(out / name), m, format="netCDF")
                    m = None
                except Exception as exc:                 # noqa: BLE001
                    logger.warning("clip failed for %s/%s: %s", d.name, name, exc)
                    ok = False
                    break
            if ok:
                kept += 1
            else:
                shutil.rmtree(out, ignore_errors=True)

        print(f"{Fore.GREEN}  reconciled {kept}/{len(geo)} pairs -> {links} "
              f"(uniform {W}x{H} master grid){Fore.RESET}")
        return links

    def _meta_raw_dir(self) -> Path:
        """The raw/ of the subswath whose output actually populated merge/.

        _gmtsar_paths.meta_raw_dir blindly returns the FIRST F<N>/raw, which is
        wrong for an AOI-narrowed stack: it processes a single subswath that may
        not be F1 (e.g. an AOI only in F2), while F1/raw still holds stale
        aligned PRMs + baseline_table.dat from an earlier full-frame run. The
        single-subswath merge output is a symlink into F<sw>/intf_all/, so
        resolve one merge/<pair>/unwrap_ll.grd back to its real F<sw>/raw. Falls
        back to meta_raw_dir for a true multi-subswath merge (whose merged
        product is a real file, not a symlink into a subswath)."""
        default = self._gmtsar_paths.meta_raw_dir
        for u in sorted(self._product_dir().glob("*/unwrap_ll.grd")):
            try:
                real = u.resolve()
            except OSError:
                continue
            if real == u:
                continue                       # not a symlink -> real merge
            for parent in real.parents:
                if parent.name == "intf_all":
                    raw = parent.parent / "raw"
                    if raw.is_dir() and list(raw.glob("S1_*_ALL_F*.PRM")):
                        return raw
                    break
        return default

    def _set_load_parameters(self) -> None:
        """Wire the mintpy.load.* keys prep_gmtsar.py reads from GMTSAR's
        geocoded stack output. prep_gmtsar globs `<fbase>_ll*.grd` and derives
        LAT/LON_REF + geo-transform from the *_ll.grd files themselves, so the
        essential inputs are the unwrapped/coherence _ll grids, one sample PRM
        (metadata), and baseline_table.dat (per-date baselines)."""
        stack = self.stack_dir
        intf = self._consistent_intf_dir(self._product_dir())

        self.config.load_unwFile     = str(intf / "*" / "unwrap_ll.grd")
        self.config.load_corFile     = str(intf / "*" / "corr_ll.grd")

        # metadata + per-date baselines from the subswath that actually produced
        # merge/ (not a stale sibling F<N>/raw) -- see _meta_raw_dir.
        raw = self._meta_raw_dir()
        self.config.load_baselineDir = str(raw / "baseline_table.dat")
        prm = (next(iter(sorted(raw.glob("S1_*_ALL_F*.PRM"))), None)
               or next(iter(sorted(raw.glob("S1_*.PRM"))), None))
        if prm is None:
            # p2p keeps its PRMs per case, at <case>/F<N>/raw/, so meta_raw_dir
            # -- which assumes the stack_mode layout -- is empty. Falling
            # through to a "<raw>/*.PRM" glob looked harmless but pointed
            # MintPy at a directory holding only the two files this analyzer
            # had just written, and prep_gmtsar needs a real PRM for the radar
            # wavelength and orbit direction.
            #
            # Take the same subswath the baselines came from, so metadata and
            # b_perp describe one geometry rather than two.
            same_swath = sorted(self.stack_dir.glob(
                f"*/F{self._baseline_swath or 1}/raw/S1_*_F?.PRM"))
            prm = next(iter(same_swath),
                       next(iter(sorted(self.stack_dir.glob("*/F?/raw/S1_*_F?.PRM"))), None))
            if prm is not None:
                logger.info("p2p layout: metaFile taken from %s",
                            prm.relative_to(self.stack_dir))
        self.config.load_metaFile = str(prm) if prm else str(raw / "*.PRM")

        # DEM: two things must be fixed before MintPy can use GMTSAR's dem.grd
        #  1. it carries no projection -> EPSG=None -> HDF5 attr write crash
        #     ("Object dtype dtype('O') has no native HDF5 equivalent")
        #  2. it spans the whole SLC footprint at 3-arcsec, while the stack is
        #     a sub-region at 2-arcsec -> geometryGeo.h5 (2671,4121) vs
        #     ifgramStack (1830,1870) -> "could not broadcast" in plot_result.
        # So resample it onto the stack's exact grid, then stamp EPSG:4326.
        # (grdsample also reconciles the 0-360 vs -180-180 longitude
        # convention: GMTSAR geocodes to 246..247, the DEM is -114..-111.)
        # dem_grd assumes the stack layout (gmtsar/topo/dem.grd). p2p keeps the
        # shared DEM at the workdir root and symlinks each case at it, so fall
        # back through both. Getting this wrong left demFile="auto", and
        # prep_gmtsar does glob.glob("auto")[0] -> IndexError with no message
        # naming the DEM at all.
        src_dem = next(
            (p for p in (self._gmtsar_paths.dem_grd,
                         Path(self.workdir) / "topo" / "dem.grd",
                         *sorted(self.stack_dir.glob("*/topo/dem.grd"))[:1])
             if p.exists()),
            self._gmtsar_paths.dem_grd)
        sample = next(iter(sorted(intf.glob("*/unwrap_ll.grd"))), None)
        if src_dem.exists() and sample is not None:
            logger.info("DEM for MintPy: %s", src_dem)
            import subprocess as _sp
            env = self._gdal_env()
            reg, inc = self._grd_region(sample), self._grd_increments(sample)
            dem_tif = self.mintpy_dir / "dem_match.tif"
            if reg and inc:
                tmp = self.mintpy_dir / "dem_match.grd"
                _sp.run(["gmt", "grdsample", str(src_dem),
                         f"-R{reg[0]}/{reg[1]}/{reg[2]}/{reg[3]}",
                         f"-I{inc[0]}/{inc[1]}", f"-G{tmp}"],
                        check=True, capture_output=True, env=env)
                _sp.run(["gdal_translate", "-a_srs", "EPSG:4326",
                         str(tmp), str(dem_tif)],
                        check=True, capture_output=True, env=env)
                print(f"  DEM resampled onto the stack grid "
                      f"{self._grd_shape(tmp)} -> {dem_tif.name}")
                self.config.load_demFile = str(dem_tif)

        # GMTSAR's geocoded stack has no lookup-table / incidence / mask
        # rasters -- MintPy derives those from the *_ll.grd geometry itself.
        # They must be EMPTY, not "auto": prep_gmtsar treats any truthy value
        # as a real glob pattern (`glob.glob(template[key])[0]`), so "auto"
        # raises IndexError and the whole prep step silently fails (MintPy
        # swallows it as "Assuming its result exists"), leaving no per-pair
        # .rsc -> later KeyError: 'DATE12'. Found via a real run.
        for key in ("load_lookupYFile", "load_lookupXFile", "load_incAngleFile",
                    "load_azAngleFile", "load_shadowMaskFile", "load_waterMaskFile",
                    "load_connCompFile"):
            if hasattr(self.config, key) and getattr(self.config, key) == "auto":
                setattr(self.config, key, "")

        print(f"{Fore.GREEN}  unwFile     : {self.config.load_unwFile}")
        print(f"  corFile     : {self.config.load_corFile}")
        print(f"  metaFile    : {self.config.load_metaFile}")
        print(f"  baselineDir : {self.config.load_baselineDir}{Fore.RESET}")
