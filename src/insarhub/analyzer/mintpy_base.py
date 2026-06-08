
import dataclasses
import getpass
import json
import requests
import shutil
import subprocess
import sys
from pathlib import Path

import pyaps3
from colorama import Fore, Style
from mintpy.utils import readfile
from mintpy.smallbaselineApp import TimeSeriesAnalysis

from insarhub.config.defaultconfig import Mintpy_SBAS_Base_Config
from insarhub.config.paths import MintPyPaths, Hyp3Paths
from insarhub.core.base import BaseAnalyzer
from insarhub.utils.tool import write_workflow_marker


class Mintpy_SBAS_Base_Analyzer(BaseAnalyzer):

    description = "Generic MintPy SBAS analyzer, fully customizable configs."
    compatible_processor = 'all'
    default_config = Mintpy_SBAS_Base_Config
    '''
    Base class for Mintpy SBAS analysis. This class provides a template for implementing 
    specific analysis methods using the Mintpy software package.
    '''
    def __init__(self, config: Mintpy_SBAS_Base_Config | None = None):
        super().__init__(config)

        self.workdir   = self.config.workdir
        self._paths    = MintPyPaths(Path(self.workdir))
        self._hyp3_paths = Hyp3Paths(Path(self.workdir))
        self.mintpy_dir = self._paths.mintpy_dir
        self.tmp_dir   = self._paths.tmp_dir
        self.clip_dir  = self._paths.clip_dir
        self.cfg_path  = self.workdir.joinpath('.mintpy.cfg')
        write_workflow_marker(self.workdir, analyzer=type(self).name)

    def prep_data(self):
        """Write the MintPy config file to workdir."""
        self.config.write_mintpy_config(self.cfg_path)

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
    
    def submit_hpc(self, steps: list[str] | None = None) -> str:
        """Generate a sbatch script for the full MintPy run and submit it.

        Returns the SLURM job ID string.
        """
        from insarhub.utils.tool import Slurmjob_Config

        mintpy_dir = self._paths.mintpy_dir
        mintpy_dir.mkdir(parents=True, exist_ok=True)

        # Merge user opts over defaults
        _defaults = {"time": "24:00:00", "ntasks": 1, "cpus_per_task": 16, "mem": "128G", "partition": "all"}
        opts = {**_defaults, **(self.config.hpc_sbatch_opts or {})}

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

        body = "\n".join([
            f'export PATH="{current_path}"',
            f"{insarhub_bin} analyzer -N {analyzer_name} -w {self.workdir} run",
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
                        'invert_network', 'correct_LOD', 'correct_SET',
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
        run_steps = steps or [
            'load_data', 'modify_network', 'reference_point', 'quick_overview', 'invert_network',
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
        app = TimeSeriesAnalysis(self.cfg_path.as_posix(), self.workdir.as_posix())
        app.open()
        app.run(steps=run_steps)
        if 'geocode' in run_steps:
            self._geocode_diagnostic_files(self.cfg_path.parent)

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