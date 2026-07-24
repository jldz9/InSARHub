# -*- coding: utf-8 -*-
"""
GMTSAR_MINTPY_SBAS — MintPy SBAS on a GMTSAR stack_mode stack.

Hands the coherent stack GMTSAR_S1 (stack_mode=True) produces
(workdir/gmtsar/: baseline_table.dat + per-pair geocoded *_ll.grd) to MintPy
via its own prep_gmtsar.py loader + smallbaselineApp. Mirror of ISCE_SBAS /
Hyp3_SBAS, differing only in _set_load_parameters() (wires the mintpy.load.*
keys prep_gmtsar.py reads from GMTSAR's output layout). Output → workdir/mintpy/.
"""
from __future__ import annotations

from pathlib import Path

from colorama import Fore

from insarhub.config import GMTSAR_MINTPY_SBAS_Config
from insarhub.config.paths import GMTSARPaths
from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer


class GMTSAR_MINTPY_SBAS(Mintpy_SBAS_Base_Analyzer):
    name                 = "GMTSAR_MINTPY_SBAS"
    description          = "SBAS time-series of a GMTSAR stack_mode stack using MintPy (prep_gmtsar.py)."
    compatible_processor = "GMTSAR_S1"
    default_config       = GMTSAR_MINTPY_SBAS_Config
    # own output dir (workdir/gmtsar_mintpy/) -- separate from Hyp3_SBAS/
    # ISCE_SBAS runs on the same workdir; layout via MintPyPaths
    MINTPY_SUBDIR        = "gmtsar_mintpy"

    def __init__(self, config: GMTSAR_MINTPY_SBAS_Config | None = None):
        super().__init__(config)
        self._gmtsar_paths = GMTSARPaths(Path(self.workdir))

    @property
    def stack_dir(self) -> Path:
        """Where GMTSAR_S1 stack_mode wrote its output."""
        return self._gmtsar_paths.case_dir

    def prep_data(self) -> None:
        """Auto-discover the GMTSAR stack outputs and write the MintPy config."""
        if self.config.container:
            return self._run_via_container(["prep_data"])

        stack = self.stack_dir
        # preproc_batch_tops writes baseline_table.dat into raw/, not the root.
        baseline = self._gmtsar_paths.baseline_table_auto
        intf_dir = self._gmtsar_paths.product_dir()
        if not baseline.exists():
            raise FileNotFoundError(
                f"{baseline} not found -- run GMTSAR_S1 with stack_mode=True "
                "and wait for the align stage to finish."
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
            if btc.exists():
                for l in btc.read_text().splitlines():
                    if l.strip().startswith("filter_wavelength"):
                        fw = l.split("=")[1].strip()
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
        import os as _os
        env = _os.environ.copy()
        if self.config.gmtsar_env_bin:
            envroot = str(Path(self.config.gmtsar_env_bin).parent)
            env["PATH"] = f"{self.config.gmtsar_env_bin}:" + env.get("PATH", "")
            env.setdefault("PROJ_DATA", f"{envroot}/share/proj")
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
        """Return a directory whose */unwrap_ll.grd all share one grid size,
        clipping every pair to their common (intersection) region when they
        don't.

        GMTSAR geocodes each pair to its own valid-data extent, so a pair with
        less coherence can come out a few columns narrower (real case: one pair
        1830x1870 vs 1830x1880 -- 10 columns short on the west edge). MintPy's
        own skip_files_with_inconsistent_size() *detects* that but can't drop
        it: it removes by matching the pair's `yymmdd` dates as a substring of
        the file path, and GMTSAR names pair dirs by Julian date
        (2021115_2021151), so nothing matches and the file loads anyway ->
        "Can't broadcast (1830,1870) -> (1830,1880)". Clipping keeps every pair
        (vs. dropping one) at the cost of the few edge columns not common to
        all -- the grids share registration and increments, so this is an exact
        cut, no resampling.
        """
        from collections import Counter
        import shutil, subprocess as _sp

        pairs = sorted(d for d in intf_all.iterdir() if d.is_dir())
        shapes = {d: self._grd_shape(d / "unwrap_ll.grd") for d in pairs}
        shapes = {d: s for d, s in shapes.items() if s}
        if not shapes:
            return intf_all
        modal, _ = Counter(shapes.values()).most_common(1)[0]
        odd = [d for d, s in shapes.items() if s != modal]
        if not odd:
            return intf_all
        for d in odd:
            print(f"  size mismatch {d.name}: {shapes[d]} != modal {modal}")

        # common region = intersection over every pair
        regions = {d: self._grd_region(d / "unwrap_ll.grd") for d in shapes}
        regions = {d: r for d, r in regions.items() if r}
        if not regions:
            return intf_all
        R = (max(r[0] for r in regions.values()), min(r[1] for r in regions.values()),
             max(r[2] for r in regions.values()), min(r[3] for r in regions.values()))
        Rstr = f"{R[0]}/{R[1]}/{R[2]}/{R[3]}"
        print(f"  clipping all {len(regions)} pairs to common region {Rstr}")

        # same convention as Hyp3_SBAS._clip_rasters(): clipped copies live
        # in clip_dir (MintPyPaths), not the source stack
        links = self.clip_dir
        if links.exists():
            shutil.rmtree(links)
        links.mkdir(parents=True)

        env = self._gdal_env()
        kept = 0
        for d in regions:
            out = links / d.name
            out.mkdir()
            ok = True
            for name in ("unwrap_ll.grd", "corr_ll.grd"):
                src = d / name
                if not src.exists():
                    ok = False
                    break
                p = _sp.run(["gmt", "grdcut", str(src), f"-R{Rstr}", f"-G{out / name}"],
                            capture_output=True, text=True, env=env)
                if p.returncode != 0:
                    print(f"  grdcut failed for {d.name}/{name}: {p.stderr.strip()}")
                    ok = False
                    break
            if ok:
                kept += 1
            else:
                shutil.rmtree(out, ignore_errors=True)

        cut_shapes = {self._grd_shape(p / "unwrap_ll.grd")
                      for p in links.iterdir() if p.is_dir()}
        print(f"  clipped {kept}/{len(regions)} pairs -> {links} "
              f"(uniform grid: {cut_shapes.pop() if len(cut_shapes) == 1 else cut_shapes})")
        return links

    def _set_load_parameters(self) -> None:
        """Wire the mintpy.load.* keys prep_gmtsar.py reads from GMTSAR's
        geocoded stack output. prep_gmtsar globs `<fbase>_ll*.grd` and derives
        LAT/LON_REF + geo-transform from the *_ll.grd files themselves, so the
        essential inputs are the unwrapped/coherence _ll grids, one sample PRM
        (metadata), and baseline_table.dat (per-date baselines)."""
        stack = self.stack_dir
        intf = self._consistent_intf_dir(self._gmtsar_paths.product_dir())

        self.config.load_unwFile     = str(intf / "*" / "unwrap_ll.grd")
        self.config.load_corFile     = str(intf / "*" / "corr_ll.grd")
        self.config.load_baselineDir = str(self._gmtsar_paths.baseline_table_auto)

        # prefer the aligned super-master PRM (S1_<date>_ALL_F<sw>, first in
        # date order = the reference every scene was coregistered to) over the
        # pre-alignment per-time PRMs
        raw = self._gmtsar_paths.meta_raw_dir   # F<N>/raw for a merged stack
        prm = (next(iter(sorted(raw.glob("S1_*_ALL_F*.PRM"))), None)
               or next(raw.glob("S1_*.PRM"), None))
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
        src_dem = self._gmtsar_paths.dem_grd
        sample = next(iter(sorted(intf.glob("*/unwrap_ll.grd"))), None)
        if src_dem.exists() and sample is not None:
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
