
import getpass
import requests
import shutil
from pathlib import Path

import pyaps3
from colorama import Fore, Style
from mintpy.utils import readfile
from mintpy.smallbaselineApp import TimeSeriesAnalysis

from insarhub.config.defaultconfig import Mintpy_SBAS_Base_Config
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

        self.workdir = self.config.workdir
        self.tmp_dir = self.workdir.joinpath('tmp')
        self.clip_dir = self.workdir.joinpath('clip')
        self.cfg_path = self.workdir.joinpath('.mintpy.cfg')
        write_workflow_marker(self.workdir, analyzer=type(self).name)

    def prep_data(self):
        """Write the MintPy config file to workdir."""
        self.config.write_mintpy_config(self.cfg_path)

    def _validate_cds_token(self, key: str) -> bool:
        """Validate a CDS API token via a lightweight HTTP request (no download)."""
        try:
            import requests as _requests
            resp = _requests.get(
                "https://cds.climate.copernicus.eu/api/retrieve/v1/jobs",
                headers={"PRIVATE-TOKEN": key},
                params={"limit": 1},
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

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
        if self.config.troposphericDelay_method == 'pyaps':
            self._cds_authorize()

        run_steps = steps or [
            'load_data', 'modify_network', 'reference_point', 'quick_overview', 'invert_network',
            'correct_LOD', 'correct_SET', 'correct_ionosphere', 'correct_troposphere',
            'deramp', 'correct_topography', 'residual_RMS', 'reference_date',
            'velocity', 'geocode', 'google_earth', 'hdfeos5'
        ]
        print(f'{Style.BRIGHT}{Fore.MAGENTA}Running MintPy Analysis...{Fore.RESET}')
        app = TimeSeriesAnalysis(self.cfg_path.as_posix(), self.workdir.as_posix())
        app.open()
        app.run(steps=run_steps)

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
                    
        zips = list(self.workdir.glob('*.zip'))
        if zips:
            print(f"{Fore.CYAN}Step: Removing zip archives...{Fore.RESET}")
            for zf in zips:
                try:
                    zf.unlink()
                    print(f"  Removed: {zf.name}")
                except Exception as e:
                    print(f"{Fore.RED}  Failed to remove {zf.name}: {e}{Fore.RESET}")

        print(f"{Fore.GREEN}Cleanup complete.{Fore.RESET}")