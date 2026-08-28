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
import math
import os
import re
import subprocess
from pathlib import Path

from colorama import Fore

from insarhub.config import GMTSAR_SBAS_Config
from insarhub.config.paths import GMTSARPaths
from insarhub.core.base import BaseAnalyzer

logger = logging.getLogger(__name__)


class GMTSAR_SBAS(BaseAnalyzer):
    name = "GMTSAR_SBAS"
    aliases = ("GMTSAR_TS",)   # legacy name
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
        # Resolved by _resolve_stack_inputs() for a stack-mode run: the raw/
        # whose baseline_table.dat + metadata PRM describe the actual data
        # (not the default first F<N>/raw), and the radar-coordinate pair dir.
        self._raw_dir: Path | None = None
        self._intf_path: Path | None = None

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

    @staticmethod
    def _unset(v) -> bool:
        """A config value the user didn't set -- treat as 'auto-detect'."""
        return v is None or str(v).strip().lower() in ("", "auto", "none")

    def _subprocess_env(self) -> dict:
        """PATH-prepend GMTSAR bin + its conda env bin, same mechanism as
        GMTSAR_S1._subprocess_env() (see that docstring for the real bug).

        gmtsar_root / gmtsar_env_bin are auto-detected (not user input): an
        explicit config value wins, otherwise _find_gmtsar_root / _find_gmtsar_
        env_bin locate them from $GMTSAR, a GMTSAR script on PATH, or the
        container's own install."""
        from insarhub.processor.gmtsar_s1 import (
            _find_gmtsar_root, _find_gmtsar_env_bin,
        )
        cfg = self.config
        root = None if self._unset(cfg.gmtsar_root) else Path(cfg.gmtsar_root)
        env_bin = None if self._unset(cfg.gmtsar_env_bin) else Path(cfg.gmtsar_env_bin)
        root = _find_gmtsar_root(root)
        env_bin = _find_gmtsar_env_bin(env_bin)

        env = os.environ.copy()
        env["GMTSAR"] = str(root)
        env["PATH"] = ":".join([str(env_bin), str(Path(root) / "bin"),
                                env.get("PATH", "")])
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

    @staticmethod
    def _baseline_stem_to_julian(baseline: Path) -> dict[str, int]:
        """Map baseline_table.dat col1 (measurement stem) -> int(col2) Julian id.

        prep_sbas keys its ``grep <stem> baseline_table.dat`` on col1, so the
        intf.in it reads must name MEASUREMENT stems (``s1a-iw2-slc-vv-...-005``),
        not the processor's aligned stems (``S1_<date>_ALL_F<N>``) which grep
        never matches -- a mismatch that left ref_id/rep_id empty and prep_sbas
        producing garbage rows. col2 carries the Julian id GMTSAR names the
        pair dirs with.
        """
        out: dict[str, int] = {}
        for ln in baseline.read_text().splitlines():
            parts = ln.split()
            if len(parts) >= 2:
                try:
                    out[parts[0]] = int(float(parts[1]))
                except ValueError:
                    continue
        return out

    def _write_stack_intf_in(self, intf_dir: Path, baseline: Path) -> Path | None:
        """intf.in in prep_sbas's ``ref:rep`` form (measurement stems), built
        from the pair dirs present under intf_dir and the baseline table."""
        jul2stem = {j: s for s, j in self._baseline_stem_to_julian(baseline).items()}
        lines: list[str] = []
        for d in sorted(p for p in intf_dir.iterdir() if p.is_dir()):
            try:
                j1, j2 = (int(x) for x in d.name.split("_"))
            except ValueError:
                continue
            if j1 in jul2stem and j2 in jul2stem:
                lines.append(f"{jul2stem[j1]}:{jul2stem[j2]}")
            else:
                logger.warning("GMTSAR_SBAS: pair %s not in baseline_table.dat; "
                               "skipped", d.name)
        if not lines:
            return None
        out = self.sbas_dir / "intf.in"
        out.write_text("\n".join(lines) + "\n")
        return out

    def _prune_network(self, intf_in: Path, intf_path: Path,
                       baseline: Path) -> Path | None:
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
        stem2jul = self._baseline_stem_to_julian(baseline)
        lines = [l.strip() for l in intf_in.read_text().splitlines() if l.strip()]

        kept, dropped = [], []
        for line in lines:
            ref, rep = line.split(":")
            rid, pid = stem2jul.get(ref), stem2jul.get(rep)
            if rid is None or pid is None:
                dropped.append((line, 1.0))
                continue
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
        self._kept_scenes = {stem2jul[s] for s in scenes}
        return out

    # ── pipeline ───────────────────────────────────────────────────────────
    def _resolve_stack_inputs(self) -> tuple[Path, Path, Path, str, str] | None:
        """(intf.in, baseline_table.dat, radar_intf_dir, phase_grd, corr_grd)
        for a stack_mode run, or None when this isn't a stack-mode layout.

        Detects the radar-coordinate pair dir (per-pair unwrap.grd/corr.grd)
        and the matching raw/ (baseline_table.dat + metadata PRM) across the
        three layouts GMTSAR_S1 stack_mode leaves behind:

            flat                 gmtsar/intf_all   + gmtsar/raw
            subswath, AOI-in-one F<N>/intf_all     + F<N>/raw
            subswath, merged     gmtsar/merge      + F<N>/raw   (true multi)

        For the single-subswath case merge/ only holds geocoded *_ll.grd
        symlinks (no radar unwrap.grd), so the radar check skips it and falls
        through to the F<N>/intf_all that actually produced the stack -- the
        same subswath narrowing GMTSAR_Mintpy_SBAS's _meta_raw_dir does. intf.in
        is (re)written with measurement stems because prep_sbas greps
        baseline_table.dat by col1 (see _write_stack_intf_in).
        """
        p = self._gmtsar_paths
        phase, corr = self.config.phase_grd, self.config.corr_grd

        candidates: list[tuple[Path, Path]] = []
        # merged radar product first (true multi), then each subswath, then flat.
        candidates.append((p.merge_dir, p.meta_raw_dir))
        for d in p._swath_dirs():
            candidates.append((d / "intf_all", d / "raw"))
        candidates.append((p.intf_all_dir, p.raw_dir))

        for intf_dir, raw_dir in candidates:
            baseline = raw_dir / "baseline_table.dat"
            if not baseline.exists():
                continue
            pairs = ([x for x in intf_dir.iterdir() if x.is_dir()]
                     if intf_dir.is_dir() else [])
            if not any((d / phase).exists() for d in pairs):
                continue
            intf_in = self._write_stack_intf_in(intf_dir, baseline)
            if intf_in is None:
                continue
            self._raw_dir = raw_dir
            self._intf_path = intf_dir
            return (intf_in.resolve(), baseline.resolve(), intf_dir.resolve(),
                    phase, corr)
        return None

    def _resolve_sbas_inputs(self) -> tuple[Path, Path, Path, str, str]:  # noqa: D401
        """Return (intf.in, baseline_table.dat, product_dir, phase_name,
        corr_name) for prep_sbas, from EITHER a stack_mode stack or p2p output.

        stack_mode gives native radar-coord grids on a common grid (its whole
        point); p2p geocodes each pair separately, so for p2p we fall back to
        the geocoded *_ll grids staged onto a common grid -- the exact same
        inputs GMTSAR_Mintpy_SBAS feeds MintPy, run through GMTSAR's own sbas
        instead. All resolve()d: prep_sbas runs with cwd=sbas_dir."""
        cfg = self.config
        stack_inputs = self._resolve_stack_inputs()
        if stack_inputs is not None:
            intf_in, baseline, intf_path, phase, corr = stack_inputs
            if cfg.auto_prune:
                intf_in = self._prune_network(intf_in, intf_path, baseline) or intf_in
            return intf_in, baseline, intf_path, phase, corr
        return self._resolve_sbas_inputs_p2p()

    def _resolve_sbas_inputs_p2p(self) -> tuple[Path, Path, Path, str, str]:
        """p2p → sbas inputs. Reuses GMTSAR_Mintpy_SBAS's proven p2p staging
        (per-pair geocoded grids clipped to a common grid) and baseline builder,
        then writes intf.in in prep_sbas's `ref_stem:rep_stem` form from the
        baseline table (prep_sbas keys the pair dir off int(col2)=Julian, which
        is exactly how the staged dirs are named)."""
        from insarhub.analyzer.gmtsar_mintpy_sbas import GMTSAR_Mintpy_SBAS
        from insarhub.config.defaultconfig import GMTSAR_Mintpy_SBAS_Config

        helper = GMTSAR_Mintpy_SBAS(GMTSAR_Mintpy_SBAS_Config(workdir=str(self.workdir)))
        intf = helper._p2p_product_dir()
        if intf is None:
            raise FileNotFoundError(
                f"No stack_mode intf.in under {self.stack_dir} and no p2p "
                f"merge/*_ll.grd either -- run GMTSAR_S1 first (stack_mode or p2p)."
            )
        clip = helper._consistent_intf_dir(intf)

        baseline = helper._gmtsar_paths.baseline_table_auto
        if not baseline.exists():
            if not helper._build_baseline_table(baseline):
                raise FileNotFoundError(
                    "could not build baseline_table.dat from the p2p per-date PRMs "
                    "(need one .PRM + .LED per date under gmtsar/<case>/F<n>/raw/)."
                )

        # Julian id (int of col2) -> stem (col1), to write intf.in `ref:rep`.
        jul2stem: dict[int, str] = {}
        for ln in baseline.read_text().splitlines():
            p = ln.split()
            if len(p) >= 2:
                try:
                    jul2stem[int(float(p[1]))] = p[0]
                except ValueError:
                    continue
        lines = []
        for d in sorted(p for p in clip.iterdir() if p.is_dir()):
            try:
                j1, j2 = (int(x) for x in d.name.split("_"))
            except ValueError:
                continue
            if j1 in jul2stem and j2 in jul2stem:
                lines.append(f"{jul2stem[j1]}:{jul2stem[j2]}")
            else:
                logger.warning("p2p sbas: pair %s not in baseline_table; skipped", d.name)
        if not lines:
            raise RuntimeError(
                "no p2p pairs matched the baseline table -- Julian dir names and "
                "baseline_table.dat dates disagree.")
        intf_in = self.sbas_dir / "intf.in"
        intf_in.write_text("\n".join(lines) + "\n")
        print(f"{Fore.CYAN}p2p: {len(lines)} pair(s) -> GMTSAR-native sbas on the "
              f"geocoded common grid ({clip}){Fore.RESET}")
        # p2p pairs are geocoded; auto_prune's radar-stem logic doesn't apply.
        # Also tells run() to skip the radar->ll projection: sbas ran on lon/lat
        # grids, so vel.grd/disp_*.grd come out geographic already.
        self._p2p_geocoded = True
        return (intf_in.resolve(), baseline.resolve(), clip.resolve(),
                "unwrap_ll.grd", "corr_ll.grd")

    def prep_data(self) -> str:
        """Run prep_sbas → intf.tab + scene.tab in sbas_dir. Returns the
        sbas command line prep_sbas echoes (with N/S/xdim/ydim filled in)."""
        self.sbas_dir.mkdir(parents=True, exist_ok=True)
        intf_in, baseline, intf_path, phase_name, corr_name = self._resolve_sbas_inputs()
        # prep_sbas intf.in baseline_table.dat <intf_path> <phase_grd> <corr_grd>
        cmd = ["prep_sbas", str(intf_in), str(baseline), str(intf_path),
               phase_name, corr_name]
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

    def _resolve_geometry(self) -> tuple[float, float]:
        """sbas's -range and -incidence for this scene: (metres, degrees).

        Both feed one term in sbas -- ``scale = 4*pi / wl / rng / sin(theta)``
        (sbas.c), which forms the DEM-error column of the design matrix
        (``G[...] = bperp[i] * scale`` in sbas_utils.c). Neither scales the
        displacement time series itself, which is why GMTSAR's recipe calls
        the incidence value "largely irrelevant" -- but both are scene
        geometry that the super-master PRM already knows exactly, so neither
        is guessed.

        -range follows the recipe's formula (§12b):

            Range = ({[(c / rng_samp_rate) / 2] * ((x_min + x_max) / 2)} / 2)
                    + near_range

        -incidence is the standard spherical-earth conversion from that
        range, with Rs = earth_radius + SC_height:

            cos(look) = (Rs^2 + R^2 - Re^2) / (2 * Rs * R)
            sin(inc)  = Rs * sin(look) / Re

        Measured on a real 3-subswath p100_f466 frame this gives 39.53 deg at
        mid-swath, against sbas's built-in default of 37 (~6% off on that
        term) and the recipe's own hardcoded 40 (~1% off) -- so the recipe's
        choice is about right for Sentinel-1 IW while sbas's default is not,
        and computing it is exact for any frame or sensor. Validated against
        the published Sentinel-1 IW geometry: evaluating the same conversion
        at the merged grid's near and far edges gives 30.49 and 45.98 deg,
        reproducing the documented ~29.1-46.0 deg IW1-IW3 span.

        Falls back to config.range_dist / config.incidence with a warning if
        the PRM can't be read.
        """
        cfg = self.config
        try:
            raw = self._raw_dir or self._gmtsar_paths.meta_raw_dir
            prm = next(iter(sorted(raw.glob("S1_*_ALL_F*.PRM"))), None)
            if prm is None:
                raise FileNotFoundError("no super-master PRM")
            txt = prm.read_text()

            def _prm(key: str) -> float:
                m = re.search(rf"(?m)^{key}\s*=\s*(\S+)", txt)
                if not m:
                    raise KeyError(key)
                return float(m.group(1))

            rng_samp_rate = _prm("rng_samp_rate")
            near_range = _prm("near_range")

            intf_path = self._intf_path or self._gmtsar_paths.product_dir()
            grd = next(iter(sorted(intf_path.glob(f"*/{cfg.phase_grd}"))), None)
            if grd is None:
                raise FileNotFoundError(f"no {cfg.phase_grd} to size the grid from")
            info = subprocess.run(["gmt", "grdinfo", "-C", str(grd)],
                                  capture_output=True, text=True,
                                  env=self._subprocess_env())
            fields = info.stdout.split()
            x_min, x_max = float(fields[1]), float(fields[2])

            # NB: the recipe's prose and its worked example disagree. Written
            # out it reads "({[(c/rng_samp_rate)/2] * ((x_min+x_max)/2)} / 2)
            # + near_range", but its own numbers are
            # "({[3e8/64345238.125714/2] * 47704} / 2) + 845481.851848 =
            # 901,085" -- i.e. it multiplies by x_max and halves that, not by
            # the midpoint and halves again. Taking the prose literally gives
            # 873,283 for the same scene, 28 km low. The worked example is
            # the physically sensible one and is what's implemented here:
            #   range = (slant range per sample) * (midpoint sample) + near_range
            # Exact c is used where the recipe writes "~3 x 10^8 m/s"
            # (a 0.004% difference).
            c = 299792458.0
            rng = ((c / rng_samp_rate) / 2.0) * ((x_min + x_max) / 2.0) + near_range

            earth_radius = _prm("earth_radius")
            sc_height = _prm("SC_height")
            rs = earth_radius + sc_height
            look = math.acos((rs ** 2 + rng ** 2 - earth_radius ** 2) / (2 * rs * rng))
            inc = math.degrees(math.asin(rs * math.sin(look) / earth_radius))

            print(f"  -range {rng:.0f} m  -incidence {inc:.2f} deg  "
                  f"(computed from {prm.name}: rng_samp_rate={rng_samp_rate:.6g}, "
                  f"near_range={near_range:.6g}, Re={earth_radius:.0f}, "
                  f"H={sc_height:.0f}, x={x_min:.0f}..{x_max:.0f})")
            return rng, inc
        except Exception as e:
            logger.warning(
                "Could not compute sbas -range/-incidence from the PRM (%s); falling "
                "back to config.range_dist=%s, config.incidence=%s. Both are "
                "frame-specific -- verify them, or set them explicitly.",
                e, cfg.range_dist, cfg.incidence)
            return float(cfg.range_dist), float(cfg.incidence)

    def _run_via_container(self) -> None:
        """Re-invoke `insarhub analyzer ... run` inside self.config.container,
        which ships GMTSAR + insarhub. Mirrors Mintpy_SBAS_Base._run_via_container."""
        import subprocess
        from insarhub.utils.container import wrap_container_cmd
        cli_cmd = f"insarhub analyzer -N {type(self).name} -w {self.workdir} run --step sbas"
        wrapped = wrap_container_cmd(self.config.container, cli_cmd, Path(self.workdir))
        result = subprocess.run(wrapped, shell=True)
        if result.returncode != 0:
            raise RuntimeError(f"Container run failed (exit {result.returncode}): {wrapped}")

    def run(self, steps=None) -> None:
        """prep_data() (if needed) then run the sbas inversion with the
        configured flags → disp_*.grd + vel.grd in sbas_dir."""
        if self.config.container and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
            return self._run_via_container()
        sbas_base = self.prep_data()   # "sbas intf.tab scene.tab N S xdim ydim"
        print(f"prep_sbas OK -> {sbas_base}")
        cfg = self.config
        cmd = sbas_base.split()
        if cfg.smooth:
            cmd += ["-smooth", str(cfg.smooth)]
        if cfg.atm_iters:
            cmd += ["-atm", str(cfg.atm_iters)]
        rng, inc = self._resolve_geometry()
        cmd += ["-wavelength", str(cfg.wavelength),
                "-incidence", f"{inc:.2f}",
                "-range", f"{rng:.0f}"]
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
        if getattr(self, "_p2p_geocoded", False):
            self._alias_geocoded_outputs()
        else:
            self.geocode()

    def _alias_geocoded_outputs(self) -> None:
        """p2p sbas ran on geocoded (_ll) grids, so vel.grd/disp_*.grd are
        ALREADY in lon/lat -- no proj_ra2ll needed. Write *_ll.grd aliases so
        anything expecting GMTSAR's geocoded naming (the results viewer, the
        recipe's Google-Earth step) finds them."""
        print(f"{Fore.CYAN}p2p: sbas ran on geocoded grids -- vel.grd/disp_*.grd "
              f"are already lon/lat; writing *_ll.grd aliases.{Fore.RESET}")
        for grd in ([self.sbas_dir / "vel.grd"]
                    + sorted(self.sbas_dir.glob("disp_*.grd"))):
            if grd.exists():
                out = grd.with_name(f"{grd.stem}_ll.grd")
                if not out.exists():
                    out.symlink_to(grd.name)

    def geocode(self) -> None:
        """Project sbas's radar-coordinate output into lat/lon (recipe §12c).

        sbas writes vel.grd and disp_*.grd in RADAR coordinates, which can't
        be mapped or compared against anything geographic until projected --
        the recipe's own last step, and the reason its results are viewable
        in Google Earth. Needs trans.dat, which the merge stage produced
        (mergeprep, for a multi-subswath stack); linked in rather than copied,
        as the recipe does, since it can be well over a gigabyte.

        Non-fatal: the inversion itself has already succeeded and its radar
        grids are on disk by this point, so a missing trans.dat degrades this
        to a warning rather than throwing away the run.
        """
        env = self._subprocess_env()
        candidates = [self._gmtsar_paths.merge_dir / "trans.dat"]
        if self._raw_dir is not None:
            candidates.append(self._raw_dir.parent / "topo" / "trans.dat")
        candidates.append(self._gmtsar_paths.topo_dir / "trans.dat")
        trans_src = next((c for c in candidates if c.exists()), None)
        if trans_src is None:
            logger.warning(
                "No trans.dat found (looked in %s) -- skipping geocoding; "
                "vel.grd/disp_*.grd remain in radar coordinates.",
                ", ".join(str(c) for c in candidates))
            return

        trans = self.sbas_dir / "trans.dat"
        if not trans.exists():
            trans.symlink_to(os.path.relpath(trans_src, self.sbas_dir))

        targets = [p for p in ([self.sbas_dir / "vel.grd"]
                               + sorted(self.sbas_dir.glob("disp_*.grd"))) if p.exists()]
        done = 0
        for grd in targets:
            out = grd.with_name(f"{grd.stem}_ll.grd")
            if out.exists():
                done += 1
                continue
            # proj_ra2ll.csh caches raln.grd/ralt.grd and will not regenerate
            # them, which is what makes projecting many grids cheap after the
            # first -- but also why a stale pair has to be cleared by hand.
            r = subprocess.run(["proj_ra2ll.csh", "trans.dat", grd.name, out.name],
                               cwd=str(self.sbas_dir), capture_output=True,
                               text=True, env=env)
            if r.returncode == 0 and out.exists():
                done += 1
            else:
                logger.warning("proj_ra2ll failed for %s: %s",
                               grd.name, (r.stderr or r.stdout).strip()[:300])

        vel_ll = self.sbas_dir / "vel_ll.grd"
        if vel_ll.exists():
            cpt = self.sbas_dir / "vel_ll.cpt"
            with open(cpt, "w") as fh:
                subprocess.run(["gmt", "grd2cpt", vel_ll.name, "-Z", "-Cjet"],
                               cwd=str(self.sbas_dir), stdout=fh,
                               stderr=subprocess.DEVNULL, env=env)
            if cpt.stat().st_size:
                subprocess.run(["grd2kml.csh", "vel_ll", cpt.name],
                               cwd=str(self.sbas_dir), capture_output=True, env=env)
        print(f"  geocoded {done}/{len(targets)} grid(s) -> *_ll.grd"
              + (" (+ vel_ll.kml)" if (self.sbas_dir / "vel_ll.kml").exists() else ""))

    def extract_time_series(self, lon: float, lat: float,
                            m_rng: int = 5, m_azi: int = 5) -> Path:
        """Point time series via GMTSAR's extract_one_time_series →
        time_series.dat in sbas_dir. Needs a PRM + dem.grd + scene.tab."""
        scene_tab = self.sbas_dir / "scene.tab"
        dem = self._gmtsar_paths.dem_grd
        raw = self._raw_dir or self._gmtsar_paths.raw_dir
        prm = next(raw.glob("S1_*.PRM"), None)
        if prm is None:
            raise FileNotFoundError("no S1_*.PRM in stack raw/ for llt2rat")
        cmd = ["extract_one_time_series", str(lon), str(lat), str(prm),
               str(dem), str(scene_tab), str(m_rng), str(m_azi)]
        subprocess.run(cmd, cwd=str(self.sbas_dir), check=True,
                       env=self._subprocess_env())
        return self.sbas_dir / "time_series.dat"
