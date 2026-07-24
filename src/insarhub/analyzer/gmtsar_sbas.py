# -*- coding: utf-8 -*-
"""
GMTSAR_SBAS — GMTSAR-native SBAS time-series inversion.

Consumes a coherent stack produced by GMTSAR_S1 with stack_mode=True
(workdir/gmtsar/: intf.in, baseline_table.dat, intf/<pair>/) and runs
GMTSAR's own prep_sbas + sbas C binary -- no MintPy. Output (disp_*.grd
cumulative displacement per date, vel.grd linear velocity) → workdir/gmtsar_sbas/.

Shells out to GMTSAR exactly like GMTSAR_S1 does (its own conda env's `gmt`
+ the sbas binary via an explicit PATH), never reimplements the inversion.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from insarhub.config import GMTSAR_SBAS_Config
from insarhub.config.paths import GMTSARPaths
from insarhub.core.base import BaseAnalyzer

logger = logging.getLogger(__name__)


class GMTSAR_SBAS(BaseAnalyzer):
    name = "GMTSAR_SBAS"
    description = (
        "GMTSAR-native SBAS time-series (prep_sbas + sbas C binary). Consumes "
        "a GMTSAR_S1 stack_mode stack (workdir/gmtsar/), produces disp_*.grd + "
        "vel.grd in workdir/gmtsar_sbas/. No MintPy."
    )
    compatible_processor = "GMTSAR_S1"
    default_config = GMTSAR_SBAS_Config

    def __init__(self, config: GMTSAR_SBAS_Config | None = None):
        super().__init__(config)
        self.config: GMTSAR_SBAS_Config = self.config or GMTSAR_SBAS_Config()
        if not self.config.gmtsar_root or not self.config.gmtsar_env_bin:
            raise ValueError(
                "GMTSAR_SBAS_Config.gmtsar_root and gmtsar_env_bin are both "
                "required -- the sbas binary and `gmt` come from GMTSAR's own "
                "install/conda env, not InSARHub's. See GMTSAR_S1's "
                "_subprocess_env() for the same requirement."
            )

    # ── paths ──────────────────────────────────────────────────────────────
    @property
    def workdir(self) -> Path:
        # absolute: subprocesses run with cwd=sbas_dir, so a relative workdir
        # would not resolve from there
        return Path(self.config.workdir).expanduser().resolve()

    @property
    def _gmtsar_paths(self) -> GMTSARPaths:
        return GMTSARPaths(self.workdir)

    @property
    def stack_dir(self) -> Path:
        """Where GMTSAR_S1 stack_mode wrote its output."""
        return self._gmtsar_paths.case_dir

    @property
    def sbas_dir(self) -> Path:
        return self._gmtsar_paths.sbas_dir

    def _subprocess_env(self) -> dict:
        """PATH-prepend GMTSAR bin + its conda env bin, same mechanism as
        GMTSAR_S1._subprocess_env() (see that docstring for the real bug)."""
        cfg = self.config
        env = os.environ.copy()
        prepend = []
        if cfg.gmtsar_env_bin:
            prepend.append(str(cfg.gmtsar_env_bin))
        if cfg.gmtsar_root:
            env["GMTSAR"] = str(cfg.gmtsar_root)
            prepend.append(str(Path(cfg.gmtsar_root) / "bin"))
        if prepend:
            env["PATH"] = ":".join(prepend) + ":" + env.get("PATH", "")
        return env

    # ── network pruning ────────────────────────────────────────────────────
    def _nan_fraction(self, grd: Path) -> float:
        """Fraction of NaN pixels in a .grd (netCDF)."""
        try:
            from netCDF4 import Dataset
            import numpy as np
            with Dataset(grd) as ds:
                for name in ("z", "Band1"):
                    if name in ds.variables:
                        a = ds.variables[name][:]
                        return float(np.ma.getmaskarray(a).mean()
                                     if np.ma.isMaskedArray(a) else np.isnan(a).mean())
        except Exception as exc:
            logger.warning("could not read %s (%s); assuming usable", grd, exc)
        return 0.0

    def _stem_julian(self, aligned_stem: str) -> int | None:
        """Julian id GMTSAR names pair dirs with, from the stem's PRM."""
        prm = self._gmtsar_paths.meta_raw_dir / f"{aligned_stem}.PRM"
        if not prm.exists():
            return None
        for l in prm.read_text().splitlines():
            if l.strip().startswith("SC_clock_start"):
                try:
                    return int(float(l.split("=")[1]))
                except (ValueError, IndexError):
                    return None
        return None

    def _prune_network(self, intf_in: Path, intf_path: Path) -> Path | None:
        """Drop decorrelated pairs, then any date left orphaned, then keep
        only the largest connected component -- writing a pruned intf.in.

        GMTSAR's sbas solves for every date using pixels valid in EVERY
        interferogram, and does no filtering of its own: a single badly
        decorrelated pair (e.g. a snow-melt/vegetation season crossing) nulls
        out nearly the whole velocity map. Confirmed on real data: 4 of 27
        pairs were ~91% NaN and left only 0.5% of pixels valid; dropping them
        (and the 2 dates they alone connected) took that to 63%.
        """
        import networkx as nx
        cfg = self.config
        lines = [l.strip() for l in intf_in.read_text().splitlines() if l.strip()]

        kept, dropped = [], []
        for line in lines:
            ref, rep = line.split(":")
            rid, pid = self._stem_julian(ref), self._stem_julian(rep)
            grd = intf_path / f"{rid}_{pid}" / cfg.phase_grd
            frac = self._nan_fraction(grd) if grd.exists() else 1.0
            (dropped if frac > cfg.max_nan_fraction else kept).append((line, frac))

        if not dropped:
            print(f"  network: all {len(lines)} pairs pass "
                  f"(NaN <= {cfg.max_nan_fraction:.0%})")
            return None
        for line, frac in dropped:
            print(f"  dropped {line}  ({frac:.0%} NaN > {cfg.max_nan_fraction:.0%})")

        # keep the largest connected component so sbas isn't singular
        g = nx.Graph()
        g.add_edges_from(tuple(l.split(":")) for l, _ in kept)
        if g.number_of_nodes():
            main = max(nx.connected_components(g), key=len)
            before = len(kept)
            kept = [(l, f) for l, f in kept
                    if set(l.split(":")) <= main]
            if len(kept) < before:
                print(f"  dropped {before - len(kept)} more pair(s) outside the "
                      f"largest connected component")

        if not kept:
            raise RuntimeError(
                "auto_prune removed every pair -- raise max_nan_fraction "
                f"(currently {cfg.max_nan_fraction}) or fix the interferograms.")

        scenes = sorted({s for l, _ in kept for s in l.split(":")})
        # resolve(): prep_sbas runs with cwd=sbas_dir, so a relative path here
        # (from a relative workdir) would not resolve from there
        out = self.sbas_dir.resolve() / "intf_pruned.in"
        out.write_text("\n".join(l for l, _ in kept) + "\n")
        print(f"  network: kept {len(kept)}/{len(lines)} pairs, "
              f"{len(scenes)} dates -> {out.name}")
        self._kept_scenes = {self._stem_julian(s) for s in scenes}
        return out

    # ── pipeline ───────────────────────────────────────────────────────────
    def prep_data(self) -> str:
        """Run prep_sbas → intf.tab + scene.tab in sbas_dir. Returns the
        sbas command line prep_sbas echoes (with N/S/xdim/ydim filled in)."""
        # resolve() everything: prep_sbas runs with cwd=sbas_dir, so relative
        # workdir paths (e.g. workdir='p100_f466') would not resolve from there.
        stack = self.stack_dir.resolve()
        intf_in = stack / "intf.in"
        # GMTSAR's preproc_batch_tops writes baseline_table.dat into raw/
        # (its own cwd), not the case-dir root.
        baseline = self._gmtsar_paths.baseline_table_auto.resolve()
        intf_path = self._gmtsar_paths.product_dir().resolve()
        for p in (intf_in, baseline, intf_path):
            if not p.exists():
                raise FileNotFoundError(
                    f"{p} not found -- run GMTSAR_S1 with stack_mode=True first."
                )
        self.sbas_dir.mkdir(parents=True, exist_ok=True)

        cfg = self.config
        if cfg.auto_prune:
            intf_in = self._prune_network(intf_in, intf_path) or intf_in
        # prep_sbas intf.in baseline_table.dat <intf_path> <phase_grd> <corr_grd>
        cmd = ["prep_sbas", str(intf_in), str(baseline), str(intf_path),
               cfg.phase_grd, cfg.corr_grd]
        proc = subprocess.run(cmd, cwd=str(self.sbas_dir), capture_output=True,
                              text=True, env=self._subprocess_env())
        if proc.returncode != 0:
            raise RuntimeError(f"prep_sbas failed:\n{proc.stdout}\n{proc.stderr}")
        # prep_sbas prints:  sbas intf.tab scene.tab <N> <S> <xdim> <ydim>
        m = re.search(r"^sbas\s+intf\.tab\s+scene\.tab\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
                      proc.stdout, re.MULTILINE)
        if not m:
            raise RuntimeError(
                f"prep_sbas did not echo an sbas command line:\n{proc.stdout}")
        n, s, xdim, ydim = (int(v) for v in m.groups())

        # prep_sbas always rebuilds scene.tab from the FULL baseline_table, so
        # after pruning it still lists dropped dates -- leaving them in makes
        # sbas's least-squares system singular for those (orphan) columns.
        kept = getattr(self, "_kept_scenes", None)
        if kept:
            tab = self.sbas_dir / "scene.tab"
            rows = [l for l in tab.read_text().splitlines() if l.strip()]
            keep_rows = [l for l in rows if int(l.split()[0]) in kept]
            if len(keep_rows) != len(rows):
                tab.write_text("\n".join(keep_rows) + "\n")
                print(f"  scene.tab: {len(rows)} -> {len(keep_rows)} dates "
                      f"(dropped orphaned dates)")
            s = len(keep_rows)
        n = len([l for l in (self.sbas_dir / "intf.tab").read_text().splitlines() if l.strip()])
        return f"sbas intf.tab scene.tab {n} {s} {xdim} {ydim}"

    def run(self, steps=None) -> None:
        """prep_data() (if needed) then run the sbas inversion with the
        configured flags → disp_*.grd + vel.grd in sbas_dir."""
        sbas_base = self.prep_data()   # "sbas intf.tab scene.tab N S xdim ydim"
        print(f"prep_sbas OK -> {sbas_base}")
        cfg = self.config
        cmd = sbas_base.split()
        if cfg.smooth:
            cmd += ["-smooth", str(cfg.smooth)]
        if cfg.atm_iters:
            cmd += ["-atm", str(cfg.atm_iters)]
        cmd += ["-wavelength", str(cfg.wavelength),
                "-incidence", str(cfg.incidence),
                "-range", str(cfg.range_dist)]
        if cfg.rms:
            cmd += ["-rms"]
        if cfg.dem_err:
            cmd += ["-dem"]

        log = self.sbas_dir / "sbas.log"
        print(f"Running SBAS inversion: {' '.join(cmd)}\n  (log: {log})")
        # stream sbas's own progress lines live (tee to console + log) instead
        # of burying them in the log until the end
        with open(log, "w") as lf:
            proc = subprocess.Popen(cmd, cwd=str(self.sbas_dir),
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    text=True, bufsize=1,
                                    env=self._subprocess_env())
            for line in proc.stdout:
                print(f"  [sbas] {line.rstrip()}", flush=True)
                lf.write(line)
            proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"sbas failed (rc={proc.returncode}) -- see {log}")
        n_disp = len(list(self.sbas_dir.glob("disp_*.grd")))
        print(f"SBAS inversion complete: {self.sbas_dir}\n"
              f"  vel.grd + {n_disp} disp_*.grd (cumulative displacement per date)")
        logger.info("SBAS inversion complete: %s (vel.grd, disp_*.grd)", self.sbas_dir)

    def extract_time_series(self, lon: float, lat: float,
                            m_rng: int = 5, m_azi: int = 5) -> Path:
        """Point time series via GMTSAR's extract_one_time_series →
        time_series.dat in sbas_dir. Needs a PRM + dem.grd + scene.tab."""
        scene_tab = self.sbas_dir / "scene.tab"
        dem = self._gmtsar_paths.dem_grd
        prm = next(self._gmtsar_paths.raw_dir.glob("S1_*.PRM"), None)
        if prm is None:
            raise FileNotFoundError("no S1_*.PRM in stack raw/ for llt2rat")
        cmd = ["extract_one_time_series", str(lon), str(lat), str(prm),
               str(dem), str(scene_tab), str(m_rng), str(m_azi)]
        subprocess.run(cmd, cwd=str(self.sbas_dir), check=True,
                       env=self._subprocess_env())
        return self.sbas_dir / "time_series.dat"
