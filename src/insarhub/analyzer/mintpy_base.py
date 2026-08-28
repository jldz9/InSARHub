
import dataclasses
import getpass
import json
import logging
import os
import requests
import shutil
import subprocess
import sys
from pathlib import Path

from colorama import Fore, Style

from insarhub.config.defaultconfig import Mintpy_SBAS_Base_Config
from insarhub.config.paths import MintPyPaths, Hyp3Paths
from insarhub.core.base import BaseAnalyzer
from insarhub.utils.tool import write_workflow_marker

logger = logging.getLogger(__name__)


_MINTPY_PLOT_PATCHED = False


def _patch_mintpy_plot_bugs() -> None:
    """Work around a MintPy crash that fires on a *clean* stack.

    ``quick_overview`` runs ``unwrap_error_phase_closure --action calculate``,
    which after writing ``numTriNonzeroIntAmbiguity.h5`` calls
    ``plot.plot_num_triplet_with_nonzero_integer_ambiguity``. That plotter does
    ``vmax = int(np.nanmax(data)); ax.hist(..., bins=vmax)`` -- so when every
    pixel has ZERO non-zero-ambiguity triplets (i.e. the unwrapping was perfect,
    common for a small high-quality GMTSAR stack), ``vmax == 0`` and numpy
    raises ``bins must be positive, when an integer``. The H5 is already written
    at that point; only the histogram is broken. We wrap the plotter so a
    non-positive vmax is a quiet no-op instead of aborting the whole run.

    Idempotent and process-local: patches the attribute on ``mintpy.utils.plot``
    that ``unwrap_error_phase_closure`` looks up at call time.
    """
    global _MINTPY_PLOT_PATCHED
    if _MINTPY_PLOT_PATCHED:
        return
    try:
        import numpy as _np
        from mintpy.utils import plot as _pp, readfile as _rf
        _orig = _pp.plot_num_triplet_with_nonzero_integer_ambiguity

        def _safe(fname, *args, **kwargs):
            try:
                data = _rf.read(fname)[0]
                if not _np.isfinite(_np.nanmax(data)) or int(_np.nanmax(data)) <= 0:
                    logger.info("quick_overview: no non-zero-ambiguity triplets "
                                "(clean unwrap) — skipping MintPy's bins=0 histogram.")
                    return None
            except Exception:
                pass
            return _orig(fname, *args, **kwargs)

        _pp.plot_num_triplet_with_nonzero_integer_ambiguity = _safe
        _MINTPY_PLOT_PATCHED = True
    except Exception as e:  # pragma: no cover - mintpy shape changed; don't block the run
        logger.debug("could not patch MintPy plot bug: %s", e)



class Mintpy_SBAS_Base_Analyzer(BaseAnalyzer):

    description = "Generic MintPy SBAS analyzer, fully customizable configs."
    compatible_processor = 'all'
    default_config = Mintpy_SBAS_Base_Config
    '''
    Base class for Mintpy SBAS analysis. This class provides a template for implementing 
    specific analysis methods using the Mintpy software package.
    '''
    # Per-analyzer output folder under workdir (see MintPyPaths.subdir).
    # Subclasses override: "hyp3_mintpy" / "isce_mintpy" / "gmtsar_mintpy".
    MINTPY_SUBDIR = "mintpy"

    def __init__(self, config: Mintpy_SBAS_Base_Config | None = None):
        super().__init__(config)

        # absolute: MintPy's TimeSeriesAnalysis.open() os.chdir()s into
        # mintpy_dir, and its dask workers re-resolve paths in their own cwd --
        # a relative workdir then doubles up (".../gmtsar_mintpy/p100_f466/...")
        # and every stack path breaks. Found via a real run.
        self.workdir   = Path(self.config.workdir).expanduser().resolve()
        self._paths    = MintPyPaths(Path(self.workdir), type(self).MINTPY_SUBDIR)
        self._hyp3_paths = Hyp3Paths(Path(self.workdir))
        self.mintpy_dir = self._paths.mintpy_dir
        self.tmp_dir   = self._paths.tmp_dir
        self.clip_dir  = self._paths.clip_dir
        self.cfg_path  = self.mintpy_dir / '.mintpy.cfg'
        write_workflow_marker(self.workdir, analyzer=type(self).name)

    def prep_data(self):
        """Write the MintPy config file to workdir."""
        # not INSARHUB_CONTAINER_CHILD: when this already runs INSIDE the
        # container (re-invoked by _run_via_container), config.container is still
        # set, so without this guard it would `docker run` AGAIN inside the
        # MintPy image (which has no docker CLI) -- the nested call fails
        # silently, prep_data never resolves mintpy.load.*, and load_data then
        # dies with a missing ifgramStack.h5. The container side must run
        # prep_data locally. Mirrors the processor submit/retry guards.
        if self.config.container and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
            return self._run_via_container(["prep_data"])
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
        self._resolve_adaptive_coherence()
        self.config.write_mintpy_config(self.cfg_path)

    # ------------------------------------------------------------------ #
    #  Adaptive coherence thresholds                                      #
    # ------------------------------------------------------------------ #
    #: Value that asks InSARHub to derive a threshold from THIS stack.
    #: Distinct from "auto", which is MintPy's own token and resolves to its
    #: fixed defaults (0.7 network / 0.4 inversion) regardless of the data.
    ADAPTIVE = "adaptive"

    #: Ceilings on the adaptive coherence thresholds. The adaptive calc only
    #: needs to kick in for low-coherence stacks -- if the stack could support a
    #: threshold at or above the cap, that's already good and there's no reason
    #: to cut tighter, so it's clamped to the cap. In effect: only a value below
    #: the cap comes from the adaptive calculation; anything above uses the cap.
    ADAPTIVE_NETWORK_COH_CAP = 0.6     # network_minCoherence
    ADAPTIVE_MASK_COH_CAP = 0.6        # networkInversion_maskThreshold
    ADAPTIVE_REFERENCE_COH_CAP = 0.85  # reference_minCoherence

    def _pair_mean_coherence(self) -> dict[str, float]:
        """{pair_dir_name: mean coherence} read from the configured corFile
        glob. Works for any processor, since every SBAS analyzer sets
        mintpy.load.corFile to a per-pair glob."""
        import glob as _glob
        import numpy as np

        pattern = str(getattr(self.config, "load_corFile", "") or "")
        if not pattern or pattern == "auto":
            return {}
        out: dict[str, float] = {}
        for f in sorted(_glob.glob(pattern)):
            try:
                from osgeo import gdal
                gdal.UseExceptions()
                a = gdal.Open(f).ReadAsArray().astype("float32")
            except Exception:                                    # noqa: BLE001
                continue
            a = a[np.isfinite(a) & (a > 0)]
            if a.size:
                out[Path(f).parent.name] = float(a.mean())
        return out

    @staticmethod
    def _network_is_connected(pairs: list[tuple[str, str]], dates: set[str]) -> bool:
        """Every date reachable from any other via the kept interferograms."""
        if not dates:
            return False
        adj: dict[str, set[str]] = {d: set() for d in dates}
        for a, b in pairs:
            if a in adj and b in adj:
                adj[a].add(b); adj[b].add(a)
        seen, stack = set(), [next(iter(dates))]
        while stack:
            d = stack.pop()
            if d in seen:
                continue
            seen.add(d)
            stack.extend(adj[d] - seen)
        return seen == dates

    def _adaptive_min_coherence(self, coh: dict[str, float],
                                redundancy: float = 1.5) -> float | None:
        """The STRICTEST coherence threshold this stack can afford.

        A fixed threshold is a guess about absolute coherence, which varies
        hugely with band, land cover and season -- MintPy's 0.7 default is
        reasonable for a well-correlated C-band stack and discards everything
        here, where the best pair reaches 0.70 and the median is 0.47. With
        keepMinSpanTree on, that does not error; it silently collapses the
        network to its spanning tree, which removes every closed triplet and
        with them any redundancy for least squares or unwrapping-error repair.

        So rather than pick a number, pick the largest threshold that still
        leaves a network worth inverting:

          1. it must span every date (no isolated acquisition), and
          2. it must keep >= redundancy x (n_dates - 1) interferograms --
             1.5 means half again as many as a spanning tree, so closed
             triplets survive.

        Returns None when the stack cannot satisfy both, leaving the
        configured value untouched rather than inventing one.
        """
        if len(coh) < 2:
            return None
        pairs = {}
        for name, c in coh.items():
            parts = name.replace("-", "_").split("_")
            if len(parts) == 2:
                pairs[name] = (parts[0], parts[1])
        if len(pairs) < 2:
            return None
        dates = {d for p in pairs.values() for d in p}
        need = max(1, int(round(redundancy * (len(dates) - 1))))

        best = None
        for t in sorted(set(round(c, 3) for c in coh.values())):
            kept = [pairs[n] for n, c in coh.items() if c >= t and n in pairs]
            if len(kept) >= need and self._network_is_connected(kept, dates):
                best = t
        if best is None:
            return None
        # Cap at ADAPTIVE_NETWORK_COH_CAP: the adaptive sweep only matters for
        # low-coherence stacks. If it found the network survives a threshold at
        # or above the cap, that's already a good network -- clamp to the cap
        # rather than cut tighter. Lowering the threshold only keeps MORE pairs,
        # so the clamped network is still connected + redundant.
        return min(best, self.ADAPTIVE_NETWORK_COH_CAP)

    def _pixel_mean_coherence(self):
        """Per-pixel mean spatial coherence across the stack (2-D array), read
        from the configured corFile glob -- MintPy's avgSpatialCoherence before
        it exists. Returns None if the grids are missing or not co-registered."""
        import glob as _glob
        import numpy as np

        pattern = str(getattr(self.config, "load_corFile", "") or "")
        if not pattern or pattern == "auto":
            return None
        stack = []
        for f in sorted(_glob.glob(pattern)):
            try:
                from osgeo import gdal
                gdal.UseExceptions()
                a = gdal.Open(f).ReadAsArray().astype("float32")
            except Exception:                                    # noqa: BLE001
                continue
            a[~np.isfinite(a)] = np.nan
            a[a <= 0] = np.nan
            stack.append(a)
        if not stack or len({s.shape for s in stack}) != 1:
            return None
        return np.nanmean(np.stack(stack), axis=0)

    def _adaptive_mask_threshold(self, keep_frac: float = 0.85) -> float | None:
        """A networkInversion.maskThreshold that keeps ~keep_frac of the stack's
        coherence OBSERVATIONS in the inversion.

        maskThreshold masks each pixel out of each interferogram whose spatial
        coherence is below it. MintPy's fixed 0.4 (and any value at or above the
        stack's own coherence) drops nearly every observation on a low-coherence
        GMTSAR stack -> numInvIfgram 0 -> an all-zero time series and velocity.
        Set it at the (1-keep_frac) percentile of the POOLED per-pair coherence
        so most observations survive; None if no corFile grids are readable."""
        import glob as _glob
        import numpy as np

        pattern = str(getattr(self.config, "load_corFile", "") or "")
        if not pattern or pattern == "auto":
            return None
        vals = []
        for f in sorted(_glob.glob(pattern)):
            try:
                from osgeo import gdal
                gdal.UseExceptions()
                a = gdal.Open(f).ReadAsArray().astype("float32")
            except Exception:                                    # noqa: BLE001
                continue
            a = a[np.isfinite(a) & (a > 0)]
            if a.size:
                vals.append(a)
        if not vals:
            return None
        pooled = np.concatenate(vals)
        thr = float(np.percentile(pooled, (1.0 - keep_frac) * 100.0))
        # Cap like network_minCoherence: only a value below the cap comes from
        # the adaptive percentile; at/above it, use the cap.
        return round(min(self.ADAPTIVE_MASK_COH_CAP, max(0.0, thr)), 3)

    def _adaptive_reference_min_coherence(self) -> float | None:
        """A reference.minCoherence floor derived from THIS stack.

        The reference point is chosen among pixels whose average spatial
        coherence exceeds this floor; MintPy's fixed 0.85 finds no pixel at all
        on a low-coherence stack and errors. Instead pick the level that keeps
        the most-coherent ~2% of pixels as reference candidates, clamped so it
        never demands more than MintPy's 0.85 nor drops below 0.30 (a reference
        pixel should still be genuinely reliable)."""
        import numpy as np
        m = self._pixel_mean_coherence()
        if m is None:
            return None
        v = m[np.isfinite(m)]
        if v.size < 100:
            return None
        thr = float(np.nanpercentile(v, 98))
        # below the cap use the adaptive percentile; at/above it use the cap.
        return round(min(self.ADAPTIVE_REFERENCE_COH_CAP, max(0.30, thr)), 2)

    def _resolve_adaptive_coherence(self) -> None:
        """Replace ADAPTIVE sentinels with values derived from the stack."""
        pair_fields = [f for f in ("network_minCoherence",
                                   "networkInversion_maskThreshold")
                       if str(getattr(self.config, f, "")).lower() == self.ADAPTIVE]
        ref_adaptive = (str(getattr(self.config, "reference_minCoherence", "")).lower()
                        == self.ADAPTIVE)
        if not pair_fields and not ref_adaptive:
            return

        import numpy as np
        # Per-pair thresholds: network selection + inversion pixel mask.
        if pair_fields:
            coh = self._pair_mean_coherence()
            if not coh:
                logger.warning("adaptive coherence: no per-pair coherence found via "
                               "mintpy.load.corFile; leaving thresholds unchanged")
                for f in pair_fields:
                    setattr(self.config, f, "auto")
            else:
                vals = np.array(list(coh.values()))
                thr = self._adaptive_min_coherence(coh)
                print(f"{Fore.CYAN}Adaptive coherence: {len(coh)} pairs, "
                      f"coherence {vals.min():.2f}–{vals.max():.2f} "
                      f"(median {np.median(vals):.2f}){Fore.RESET}")
                if thr is None:
                    logger.warning("adaptive coherence: no threshold keeps the network "
                                   "connected with redundancy; falling back to MintPy auto")
                    for f in pair_fields:
                        setattr(self.config, f, "auto")
                else:
                    for f in pair_fields:
                        if f == "network_minCoherence":
                            setattr(self.config, f, round(float(thr), 2))
                            kept = int((vals >= thr).sum())
                            print(f"  network.minCoherence -> {thr:.2f}  "
                                  f"(keeps {kept}/{len(coh)} interferograms)")
                        else:
                            # Pixel mask threshold: this masks per-pixel-per-
                            # ifgram OBSERVATIONS out of the inversion, so it
                            # must sit BELOW the stack's own coherence -- a fixed
                            # 0.4/0.5 (or even a 0.1 floor) on a low-coherence
                            # GMTSAR stack drops nearly every observation, leaving
                            # numInvIfgram=0 and an all-zero time series. Derive
                            # it from the observation distribution so ~85% survive.
                            px = self._adaptive_mask_threshold()
                            if px is None:                       # no corr grids
                                px = round(max(0.0, float(thr) * 0.5), 3)
                            setattr(self.config, f, px)
                            print(f"  networkInversion.maskThreshold -> {px:.3f} "
                                  f"(keeps ~85% of coherence observations)")

        # Reference-point floor: per-PIXEL average coherence, not per-pair.
        if ref_adaptive:
            rthr = self._adaptive_reference_min_coherence()
            if rthr is None:
                logger.warning("adaptive reference.minCoherence: could not derive "
                               "from per-pixel coherence; falling back to MintPy auto")
                setattr(self.config, "reference_minCoherence", "auto")
            else:
                setattr(self.config, "reference_minCoherence", rthr)
                print(f"  reference.minCoherence -> {rthr:.2f}")

    def _cfg_load_paths_resolved(self) -> bool:
        """True once prep_data has written a real ``mintpy.load.unwFile`` into
        the cfg (i.e. not still ``auto``). Used to decide whether load_data can
        run on its own or needs prep_data first."""
        if not self.cfg_path.exists():
            return False
        for ln in self.cfg_path.read_text().splitlines():
            if ln.strip().startswith("mintpy.load.unwFile"):
                v = ln.partition("=")[2].strip()
                return v not in ("auto", "", "None")
        return False

    def _sync_runtime_cfg(self) -> None:
        """Rewrite ``.mintpy.cfg`` from the current (possibly overridden) config,
        preserving whatever ``prep_data`` computed only into the file.

        ``prep_data`` writes the geocoded load paths (``mintpy.load.unwFile`` …),
        the resolved ``metaFile``/``demFile``/``baselineDir`` and an appended
        ``HEADING`` straight into the cfg. A per-step process (e.g. a container
        ``--step invert_network``) has all of those back at ``"auto"`` on
        ``self.config``, so a blind ``write_mintpy_config`` would clobber them.
        Instead: emit a fresh cfg from config, but for any ``mintpy.load.*`` key
        the config leaves unset keep the file's value, and carry over any key the
        config never emits at all (``HEADING``). Everything else — the network /
        inversion / correction knobs a user overrode — is taken from config.
        """
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.mintpy_dir / ".mintpy.cfg.tmp"
        self.config.write_mintpy_config(tmp)
        new_lines = tmp.read_text().splitlines()
        tmp.unlink()

        def _parse(text: str) -> dict[str, str]:
            d: dict[str, str] = {}
            for ln in text.splitlines():
                if "=" in ln and not ln.strip().startswith("#"):
                    k, _, v = ln.partition("=")
                    d[k.strip()] = v.strip()
            return d

        existing = _parse(self.cfg_path.read_text()) if self.cfg_path.exists() else {}

        out, seen = [], set()
        for ln in new_lines:
            if "=" in ln and not ln.strip().startswith("#"):
                k = ln.partition("=")[0].strip()
                v = ln.partition("=")[2].strip()
                seen.add(k)
                # Keep the value prep_data resolved into the file when this
                # per-step process only has a placeholder on self.config: the
                # geocoded load paths (still "auto" here) and any coherence
                # threshold prep_data derived from the stack (still "adaptive").
                if k in existing and (
                        (k.startswith("mintpy.load.") and v in ("auto", "", "None"))
                        or v.lower() == self.ADAPTIVE):
                    out.append(f"{k:<40} = {existing[k]}")
                    continue
            out.append(ln)
        # keep cfg-only keys the config never emits (e.g. HEADING)
        for k, v in existing.items():
            if k not in seen:
                out.append(f"{k:<40} = {v}")
        self.cfg_path.write_text("\n".join(out) + "\n")

    def _validate_cds_token(self, key: str) -> bool:
        """Validate a CDS API token via a lightweight HTTP request (no download)."""
        import requests as _requests
        endpoints = [
            # Fast profile endpoint (new CDS API)
            ("GET", "https://cds.climate.copernicus.eu/api/account/me",
             {"PRIVATE-TOKEN": key}),
            # Fallback: jobs list
            ("GET", "https://cds.climate.copernicus.eu/api/retrieve/v1/jobs",
             {"PRIVATE-TOKEN": key}),
        ]
        for method, url, headers in endpoints:
            try:
                resp = _requests.request(method, url, headers=headers,
                                         params={"limit": 1}, timeout=30)
                if resp.status_code == 200:
                    return True
                if resp.status_code in (401, 403):
                    return False
            except _requests.exceptions.Timeout:
                continue
            except Exception:
                continue
        # If all endpoints timed out, assume valid to avoid blocking the user
        print(f"{Fore.YELLOW}CDS API unreachable (timeout) — assuming token is valid.{Fore.RESET}")
        return True

    def _cds_authorize(self):
        """Ensure valid CDS credentials exist, prompting the user if needed."""
        cdsapirc_path = Path.home() / ".cdsapirc"
        # Try existing .cdsapirc first
        if cdsapirc_path.is_file():
            key = None
            for line in cdsapirc_path.read_text().splitlines():
                if line.strip().startswith("key:"):
                    key = line.split(":", 1)[1].strip()
                    break
            if key and self._validate_cds_token(key):
                return True
            print(f"{Fore.YELLOW}CDS token in .cdsapirc is invalid or expired. Will prompt login.\n")

        # Prompt user for a valid token
        while True:
            self._cds_token = getpass.getpass("Enter your CDS api token at https://cds.climate.copernicus.eu/profile: ")
            if not self._validate_cds_token(self._cds_token):
                print(f"{Fore.RED}Authentication failed. Please check your token and try again.\n")
                continue
            cdsapirc_path.write_text(f"url: https://cds.climate.copernicus.eu/api\nkey: {self._cds_token}\n")
            print(f"{Fore.GREEN}Credentials saved to {cdsapirc_path}.\n")
            return True
    
    def _serialize_config_overrides(self) -> str:
        """Serialize non-default config fields back to '--flag value' CLI args.

        Used to re-invoke `insarhub analyzer ... run` (via SLURM or a
        container) with the same resolved config. `container` itself is
        always excluded — it's a per-invocation flag, and including it here
        would make a container/HPC re-invocation try to launch another
        nested container.
        """
        _skip = {"name", "workdir", "debug", "hpc_mode", "container"}
        config_cls = type(self.config)
        defaults = {}
        for f in dataclasses.fields(config_cls):
            if f.default is not dataclasses.MISSING:
                defaults[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:
                defaults[f.name] = f.default_factory()

        override_flags = []
        for f in dataclasses.fields(config_cls):
            if f.name in _skip:
                continue
            val = getattr(self.config, f.name)
            if val == defaults.get(f.name):
                continue
            if isinstance(val, bool):
                if val:
                    override_flags.append(f"--{f.name}")
            elif isinstance(val, (list, tuple)):
                override_flags.append(f"--{f.name} " + " ".join(str(v) for v in val))
            elif isinstance(val, dict):
                override_flags.append(f"--{f.name} '{json.dumps(val)}'")
            elif val is not None:
                override_flags.append(f"--{f.name} {val}")

        return (" " + " ".join(override_flags)) if override_flags else ""

    @staticmethod
    def _plot_result_safe(app, max_attempts: int = 3) -> None:
        """Call app.plot_result(), retrying past a known matplotlib race.

        MintPy's plot_result() parallelizes per-file plotting via
        joblib.Parallel over view.py calls that all use pyplot's global,
        not-thread-safe figure registry -- two calls landing close enough
        together can both try to claim the same figure number. Pre-3.11
        matplotlib silently warned and reused the figure; matplotlib >=3.11
        made that a hard ValueError ("Figure N already exists..."),
        turning a harmless race into a crash. Since the underlying SBAS
        numbers (velocity.h5, timeseries.h5, etc.) are already fully
        computed by this point -- only the pic/ figures are at risk -- a
        plain retry is cheap and usually succeeds (view.py's own --update
        flag skips replotting files already written by the failed attempt).
        """
        import matplotlib

        for attempt in range(1, max_attempts + 1):
            try:
                app.plot_result()
                return
            except ValueError as e:
                msg = str(e)
                if "already exists" not in msg or "igure" not in msg:
                    raise  # not the known matplotlib figure-registry race
                if attempt < max_attempts:
                    print(
                        f"{Fore.YELLOW}[WARNING] Plotting hit a known matplotlib "
                        f"{matplotlib.__version__} bug (parallel figure-number "
                        f"race, see plt.figure()'s strict num-reuse check added "
                        f"in matplotlib 3.11) -- retrying ({attempt}/{max_attempts - 1})...{Fore.RESET}"
                    )
                else:
                    print(
                        f"{Fore.YELLOW}[WARNING] SBAS analysis succeeded, but "
                        f"plotting failed after {max_attempts} attempts due to a "
                        f"known matplotlib {matplotlib.__version__} bug ('{msg}'). "
                        f"All numerical results (velocity.h5, timeseries.h5, etc.) "
                        f"are complete and valid -- only mintpy_dir/pic/ figures "
                        f"may be missing/incomplete. Re-run with '--step plot' to "
                        f"try generating them again, or install matplotlib<3.11 "
                        f"to eliminate the underlying race entirely.{Fore.RESET}"
                    )

    def _run_via_container(self, steps: list[str] | None = None) -> None:
        """Re-invoke `insarhub analyzer ... run` inside self.config.container.

        The container image is expected to have `insarhub` (plus MintPy)
        installed — mirrors ISCE2_Base._reinvoke_via_container's approach for
        the processor side.
        """
        from insarhub.utils.container import wrap_container_cmd

        step_args = f" --step {' '.join(steps)}" if steps else ""
        extra = self._serialize_config_overrides()
        cli_cmd = f"insarhub analyzer -N {type(self).name} -w {self.workdir} run{step_args}{extra}"
        wrapped = wrap_container_cmd(self.config.container, cli_cmd, Path(self.workdir))

        result = subprocess.run(wrapped, shell=True)
        if result.returncode != 0:
            raise RuntimeError(f"Container run failed (exit {result.returncode}): {wrapped}")

    def submit_hpc(self, steps: list[str] | None = None) -> str | None:
        """Generate a sbatch script for the full MintPy run and submit it.

        Returns the SLURM job ID string, or None if sbatch_options.json was
        just created/updated and needs review before submitting — callers
        must check for this and stop rather than treat it as success.
        """
        from insarhub.utils.tool import Slurmjob_Config
        from insarhub.processor.isce2_base import (
            _merge_sbatch_opts, _SBATCH_DEFAULT_TEMPLATE, load_or_init_sbatch_options,
        )

        mintpy_dir = self._paths.mintpy_dir
        mintpy_dir.mkdir(parents=True, exist_ok=True)

        # This method is shared by ISCE2_Mintpy_SBAS/Hyp3_Mintpy_SBAS/GMTSAR_Mintpy_SBAS, but
        # both the sbatch_options.json step key and a *fresh* file's initial
        # content follow whichever processor the workdir actually uses (each
        # subclass's compatible_processor says which). ISCE's own step keys
        # are stackSentinel's run-file numbers, where SBAS is the 17th and
        # last step -- a GMTSAR workdir has no such numbering (its stages are
        # named align/topo/intf/merge), so "17" there would be a meaningless
        # borrowed label; it uses "sbas" instead.
        if self.compatible_processor == "GMTSAR_S1":
            from insarhub.processor.gmtsar_s1 import _GMTSAR_SBATCH_DEFAULT_TEMPLATE
            default_template = _GMTSAR_SBATCH_DEFAULT_TEMPLATE
            step_key = "sbas"
        else:
            default_template = _SBATCH_DEFAULT_TEMPLATE
            step_key = "17"
        per_step = load_or_init_sbatch_options(
            Path(self.workdir), step_key, "SBAS", default_template=default_template)
        if per_step is None:
            return None
        opts = _merge_sbatch_opts(per_step, step_key)

        _slurm_fields = {f.name for f in dataclasses.fields(Slurmjob_Config)}
        _skip = {"job_name", "output_file", "error_file", "command",
                 "modules", "conda_env", "export_env", "array", "dependency"}
        slurm_kwargs = {k: v for k, v in opts.items()
                        if k in _slurm_fields and k not in _skip}

        slurm_cfg = Slurmjob_Config(
            job_name="mintpy_sbas",
            output_file=str(mintpy_dir / "mintpy_slurm_%j.out"),
            error_file=str(mintpy_dir / "mintpy_slurm_%j.err"),
            **slurm_kwargs,
        )

        import os
        import shutil

        insarhub_bin = shutil.which("insarhub") or f"{Path(sys.executable).parent}/insarhub"
        analyzer_name = type(self).name
        current_path  = os.environ.get("PATH", "")

        step_args = ""
        if steps:
            step_args = " --step " + " ".join(steps)

        # Serialize non-default config overrides back to CLI flags so that
        # prep_data inside SLURM writes the correct .mintpy.cfg values.
        extra = self._serialize_config_overrides()

        body_cmd = f"{insarhub_bin} analyzer -N {analyzer_name} -w {self.workdir} run{step_args}{extra}"
        if self.config.container:
            from insarhub.utils.container import wrap_container_cmd
            body_cmd = wrap_container_cmd(self.config.container, body_cmd, Path(self.workdir))

        body = "\n".join([
            f'export PATH="{current_path}"',
            body_cmd,
        ])

        lines = ["#!/bin/bash"] + slurm_cfg.to_header_lines() + ["", body, ""]
        sbatch_script = mintpy_dir / "mintpy_sbas.sbatch"
        sbatch_script.write_text("\n".join(lines) + "\n")
        sbatch_script.chmod(0o755)

        result = subprocess.run(
            ["sbatch", "--parsable", str(sbatch_script)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"sbatch failed: {result.stderr.strip()}")

        job_id = result.stdout.strip().split(";")[0]

        job_file = mintpy_dir / "mintpy_job.json"
        job_file.write_text(json.dumps({
            "job_id":  job_id,
            "status":  "PENDING",
            "script":  str(sbatch_script),
            "log":     str(mintpy_dir / f"mintpy_slurm_{job_id}.out"),
        }, indent=2))

        print(f"{Fore.GREEN}MintPy SBAS job submitted: {job_id}{Style.RESET_ALL}")
        print(f"  script : {sbatch_script}")
        print(f"  log    : {mintpy_dir}/mintpy_slurm_{job_id}.out")

        return job_id
        return job_id

    def run(self, steps=None):
        """
        Run the MintPy SBAS time-series analysis workflow.

        This method writes the MintPy configuration file, optionally authorizes
        CDS access for tropospheric correction, and executes the selected
        MintPy processing steps using TimeSeriesAnalysis.

        Args:
            steps (list[str] | None, optional):
                List of MintPy processing steps to execute. If None, the
                default full workflow is executed:
                    [
                        'load_data', 'modify_network', 'reference_point', 'quick_overview',
                        'correct_unwrap_error', 'invert_network', 'correct_LOD', 'correct_SET',
                        'correct_ionosphere', 'correct_troposphere',
                        'deramp', 'correct_topography', 'residual_RMS',
                        'reference_date', 'velocity', 'geocode',
                        'google_earth', 'hdfeos5'
                    ]

        Raises:
            RuntimeError: If tropospheric delay method requires CDS authorization
                and authorization fails.
            Exception: Propagates exceptions raised during MintPy execution.

        Notes:
            - If `troposphericDelay_method` is set to 'pyaps', CDS
            authorization is performed before running MintPy.
            - The configuration file is written to `self.cfg_path`.
            - Processing is executed inside `self.workdir`.
            - This method wraps MintPy TimeSeriesAnalysis for SBAS workflows.
        """
        # HPC: hand the whole analysis to SLURM instead of running MintPy in this
        # process -- mirrors processor.submit()'s hpc dispatch so the API is
        # symmetric (set hpc_mode, call run()). Returns submit_hpc()'s job id, or
        # None if it just wrote sbatch_options.json for review (call run() again
        # after tuning it). The sbatch body re-invokes `insarhub analyzer ... run`
        # WITHOUT --hpc-mode (hpc_mode is skipped by _serialize_config_overrides),
        # so the compute-node run() sees hpc_mode=False and runs locally -- no
        # resubmission loop. Guarded off inside a container child for the same
        # reason (hpc_mode isn't carried in there either).
        if getattr(self.config, "hpc_mode", False) and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
            return self.submit_hpc(steps=steps)

        # not INSARHUB_CONTAINER_CHILD: run the steps locally when already inside
        # the container (see prep_data's guard for the full rationale).
        if self.config.container and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
            return self._run_via_container(steps)

        run_steps = steps or [
            'load_data', 'modify_network', 'reference_point', 'quick_overview',
            'correct_unwrap_error', 'invert_network',
            'correct_LOD', 'correct_SET', 'correct_ionosphere', 'correct_troposphere',
            'deramp', 'correct_topography', 'residual_RMS', 'reference_date',
            'velocity', 'geocode', 'google_earth', 'hdfeos5'
        ]

        # prep_data is what fills mintpy.load.* with the real geocoded file
        # paths (plus the resolved adaptive thresholds and HEADING). The GUI
        # lets users deselect it, and load_data can be run on its own, so
        # self-heal: if it isn't in this run and the cfg still has no resolved
        # load paths, run prep_data first. Otherwise MintPy finds no files,
        # writes no ifgramStack.h5, and load_data fails. prep_data is cheap to
        # repeat (cached DEM / baselines).
        if 'prep_data' not in run_steps and not self._cfg_load_paths_resolved():
            print(f"{Fore.YELLOW}mintpy.load.* not resolved yet — running prep_data "
                  f"first to set the file locations.{Fore.RESET}")
            self.prep_data()

        if not self.cfg_path.exists():
            print(f"{Fore.YELLOW}Warning: .mintpy.cfg not found — writing config now. "
                  f"If this is a Hyp3_Mintpy_SBAS run, make sure 'prep_data' (or '--step prep') "
                  f"was completed first so load parameters are correct.{Fore.RESET}")
        # Re-apply the (possibly CLI-/GUI-overridden) config to .mintpy.cfg on
        # every run, not just the first: prep_data creates the file, so without
        # this any parameter passed to a later step (e.g. --networkInversion_
        # minTempCoh on invert_network) was silently dropped because the stale
        # file already existed. Preserves the load paths / HEADING prep_data
        # computed into the file (they are not on self.config here).
        self._sync_runtime_cfg()

        if self.config.troposphericDelay_method == 'pyaps' and 'correct_troposphere' in run_steps:
            self._cds_authorize()
        print(f'{Style.BRIGHT}{Fore.MAGENTA}Running MintPy Analysis...{Fore.RESET}')
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
        _patch_mintpy_plot_bugs()
        from mintpy.smallbaselineApp import TimeSeriesAnalysis
        app = TimeSeriesAnalysis(self.cfg_path.as_posix(), self.mintpy_dir.as_posix())
        try:
            app.open()
            app.run(steps=run_steps)
            if 'geocode' in run_steps:
                self._geocode_diagnostic_files(self.mintpy_dir)
            # Mirrors mintpy.smallbaselineApp's own CLI wrapper
            # (run_smallbaselineApp()), which calls these two after run() --
            # plot_result() is what actually populates mintpy_dir/pic/, and
            # close() is what restores the process's working directory after
            # open() changed into mintpy_dir (skipping it would leave a
            # long-running server process permanently cd'd into the last
            # analyzed folder).
            if app.template.get('mintpy.plot') and len(run_steps) > 1:
                self._plot_result_safe(app)
        finally:
            app.close()

    def plot(self) -> None:
        """(Re)generate the figures under mintpy_dir/pic/ from already-computed results.

        run()'s own post-run plotting only fires for a single bulk multi-step
        call (mirroring MintPy's own CLI semantics: len(run_steps) > 1). Both
        the CLI (`analyzer run`) and the GUI execute steps one at a time
        internally for per-step progress reporting, so that condition never
        actually triggers there — this method is the explicit, standalone
        alternative both call once after their step sequence completes
        (or on-demand, e.g. the GUI's "plot" checkbox / CLI's `--step plot`).
        """
        if self.config.container and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
            return self._run_via_container(['plot'])
        if not self.cfg_path.exists():
            raise FileNotFoundError(
                f"{self.cfg_path} not found — run prep_data and at least "
                f"load_data/invert_network/velocity before plotting."
            )
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
        _patch_mintpy_plot_bugs()
        from mintpy.smallbaselineApp import TimeSeriesAnalysis
        app = TimeSeriesAnalysis(self.cfg_path.as_posix(), self.mintpy_dir.as_posix())
        try:
            app.open()
            self._plot_result_safe(app)
        finally:
            app.close()

    def _geocode_diagnostic_files(self, mintpy_work: Path) -> None:
        """Geocode diagnostic files omitted from MintPy's default geocode step.

        MintPy only geocodes temporalCoherence, avgSpatialCoh, timeseries, velocity.
        avgPhaseVelocity, numTriNonzeroIntAmbiguity, and maskConnComp are left in
        radar coordinates. This method geocodes them into geo/ when a lookup table
        is available (radar-coord inputs). For already-geocoded inputs the method
        is a no-op.
        """
        geo_dir = mintpy_work / 'geo'
        if not geo_dir.exists():
            return  # geocode step skipped by MintPy (inputs already geocoded)

        try:
            from mintpy.utils import utils as _mut
            _, _, lookup_file = _mut.check_loaded_dataset(str(mintpy_work), print_msg=False)[:3]
        except Exception:
            return

        if not lookup_file:
            return  # geocoded inputs — no lookup table

        _DIAG = ['avgPhaseVelocity.h5', 'numTriNonzeroIntAmbiguity.h5', 'maskConnComp.h5']
        to_geo = [
            str(mintpy_work / f) for f in _DIAG
            if (mintpy_work / f).exists() and not (geo_dir / f'geo_{f}').exists()
        ]
        if not to_geo:
            return

        try:
            import mintpy.cli.geocode as _geo_cli
            iargs = to_geo + ['-l', lookup_file, '--outdir', str(geo_dir), '--update']
            print(f'{Fore.CYAN}Geocoding diagnostic files: {[Path(f).name for f in to_geo]}{Fore.RESET}')
            _geo_cli.main(iargs)
        except Exception as e:
            print(f'{Fore.YELLOW}Warning: could not geocode diagnostic files: {e}{Fore.RESET}')

    def cleanup(self):
        """
        Remove temporary files and directories generated during processing.

        This method deletes the temporary working directories and any `.zip`
        archives in `self.workdir`. If debug mode is enabled, temporary files
        are preserved and a message is printed instead.

        Behavior:
            - Deletes `self.tmp_dir` and `self.clip_dir` if they exist.
            - Deletes all `.zip` files in `self.workdir`.
            - Prints informative messages for each removal or failure.
            - Respects `self.config.debug`; no files are deleted in debug mode.

        Raises:
            Exception: Propagates any unexpected errors raised during removal.

        Notes:
            - Useful for freeing disk space after large InSAR or MintPy
            processing workflows.
            - Temporary directories should contain only non-essential files
            to avoid accidental data loss.
        """

        if self.config.debug:
            print(f"{Fore.YELLOW}Debug mode is enabled. Keeping temporary files at: {self.workdir}{Fore.RESET}")
            return
        print(f"{Fore.CYAN}Step: Cleaning up temporary directories...{Fore.RESET}")

        for folder in [self.tmp_dir, self.clip_dir]:
            if folder.exists() and folder.is_dir():
                try:
                    shutil.rmtree(folder)
                    print(f"  Removed: {folder.relative_to(self.workdir)}")
                except Exception as e:
                    print(f"{Fore.RED}  Failed to remove {folder}: {e}{Fore.RESET}")
                    
        _hyp3_dir = self._hyp3_paths.output_dir
        zips = list(_hyp3_dir.glob('*.zip')) if _hyp3_dir.exists() else list(Path(self.workdir).glob('*.zip'))
        if zips:
            print(f"{Fore.CYAN}Step: Removing zip archives...{Fore.RESET}")
            for zf in zips:
                try:
                    zf.unlink()
                    print(f"  Removed: {zf.name}")
                except Exception as e:
                    print(f"{Fore.RED}  Failed to remove {zf.name}: {e}{Fore.RESET}")

        print(f"{Fore.GREEN}Cleanup complete.{Fore.RESET}")