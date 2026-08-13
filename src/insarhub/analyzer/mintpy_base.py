
import dataclasses
import getpass
import json
import logging
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
        if self.config.container:
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
        return best

    def _resolve_adaptive_coherence(self) -> None:
        """Replace ADAPTIVE sentinels with values derived from the stack."""
        fields = ("network_minCoherence", "networkInversion_maskThreshold")
        wanted = [f for f in fields
                  if str(getattr(self.config, f, "")).lower() == self.ADAPTIVE]
        if not wanted:
            return
        coh = self._pair_mean_coherence()
        if not coh:
            logger.warning("adaptive coherence: no per-pair coherence found via "
                           "mintpy.load.corFile; leaving thresholds unchanged")
            for f in wanted:
                setattr(self.config, f, "auto")
            return
        import numpy as np
        vals = np.array(list(coh.values()))
        thr = self._adaptive_min_coherence(coh)
        print(f"{Fore.CYAN}Adaptive coherence: {len(coh)} pairs, "
              f"coherence {vals.min():.2f}–{vals.max():.2f} (median {np.median(vals):.2f})"
              f"{Fore.RESET}")
        if thr is None:
            logger.warning("adaptive coherence: no threshold keeps the network "
                           "connected with redundancy; falling back to MintPy auto")
            for f in wanted:
                setattr(self.config, f, "auto")
            return
        for f in wanted:
            if f == "network_minCoherence":
                setattr(self.config, f, round(float(thr), 2))
                kept = int((vals >= thr).sum())
                print(f"  network.minCoherence -> {thr:.2f}  "
                      f"(keeps {kept}/{len(coh)} interferograms)")
            else:
                # Pixel mask: the network threshold is a per-pair AVERAGE, so
                # reusing it would mask ~half of every kept pair. Sit below it.
                px = round(max(0.1, float(thr) * 0.6), 2)
                setattr(self.config, f, px)
                print(f"  networkInversion.maskThreshold -> {px:.2f}")

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

        # This method is shared by ISCE_SBAS/Hyp3_SBAS/GMTSAR_MINTPY_SBAS, but
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
        if self.config.container:
            return self._run_via_container(steps)

        run_steps = steps or [
            'load_data', 'modify_network', 'reference_point', 'quick_overview',
            'correct_unwrap_error', 'invert_network',
            'correct_LOD', 'correct_SET', 'correct_ionosphere', 'correct_troposphere',
            'deramp', 'correct_topography', 'residual_RMS', 'reference_date',
            'velocity', 'geocode', 'google_earth', 'hdfeos5'
        ]

        if not self.cfg_path.exists():
            print(f"{Fore.YELLOW}Warning: .mintpy.cfg not found — writing config now. "
                  f"If this is a Hyp3_SBAS run, make sure 'prep_data' (or '--step prep') "
                  f"was completed first so load parameters are correct.{Fore.RESET}")
            self.config.write_mintpy_config(self.cfg_path)

        if self.config.troposphericDelay_method == 'pyaps' and 'correct_troposphere' in run_steps:
            self._cds_authorize()
        print(f'{Style.BRIGHT}{Fore.MAGENTA}Running MintPy Analysis...{Fore.RESET}')
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
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
        if self.config.container:
            return self._run_via_container(['plot'])
        if not self.cfg_path.exists():
            raise FileNotFoundError(
                f"{self.cfg_path} not found — run prep_data and at least "
                f"load_data/invert_network/velocity before plotting."
            )
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
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