import getpass
import requests
from pathlib import Path

from colorama import Fore
from eof.download import download_eofs
from tqdm import tqdm

from insarhub.config import S1_SLC_Config
from .asf_base import ASF_Base_Downloader

class S1_SLC(ASF_Base_Downloader):
    name = "S1_SLC"
    description = "Sentinel-1 SLC scene search and download via ASF."
    default_config = S1_SLC_Config

    """
    A class to search and download Sentinel-1 data using ASF Search API."""

    def download(self, save_path: str | None = None, max_workers: int = None, force_asf: bool = False, download_orbit: bool = False, stop_event=None, on_progress=None):
        from insarhub.utils.defaults import DOWNLOAD_DEFAULTS as _DL
        if max_workers is None: max_workers = _DL["max_workers"]
        """Download SLC data and optionally associated orbit files.

        Args:
            save_path (str | None): Optional path to save the downloaded files. Defaults to None.
            max_workers (int): Parallel download workers. Defaults to 3.
            force_asf (bool): If True, forces downloading orbit files from ASF instead of CDSE. Defaults to False.
            download_orbit (bool): If True, also downloads orbit files after scenes. Defaults to False.
            stop_event: Optional threading.Event to cancel the download.
            on_progress: Optional callback(message, pct) called after each file completes.
        """
        super().download(save_path=save_path, max_workers=max_workers, stop_event=stop_event, on_progress=on_progress)
        if download_orbit:
            self.download_orbit(force_asf=force_asf)

    def download_orbit(self, force_asf: bool = False, save_dir: str | None = None, stop_event=None):
        """Download orbit files for the current search results.

        Orbit files can be downloaded from ASF or Copernicus Data Space Ecosystem (CDSE).
        CDSE typically releases orbit files earlier. Users will be prompted for CDSE credentials
        if not already configured in a `.netrc` file.

        Args:
            force_asf (bool): If True, forces downloading from ASF instead of CDSE. Defaults to False.
            save_dir (str | None): Directory to save orbit files. Defaults to workdir if not specified.
        """
        print("""
Orbit files can be downloaded from both ASF and Copernicus Data Space Ecosystem (CDSE) servers. Generally CDSE release orbit files a few hours to days earlier.
To download orbit file from Copernicus Data Space Ecosystem(CDSE). Please ensure you to create an account at https://dataspace.copernicus.eu/ and setup in the .netrc file.
If a .netrc file is not provide under your home directory, you will be prompt to enter your CDSE username and password.
Check documentation for how to setup .netrc file.
If CDSE download fails, ASF will be attempted as a fallback.""")

        self._has_cdse_netrc = self._check_netrc(keyword='machine dataspace.copernicus.eu')
        if self._has_cdse_netrc:
            print(f"{Fore.GREEN}Credential from .netrc was found for authentication.\n")
        else:
            while True:
                self._cdse_username = input("Enter your CDSE username: ")
                self._cdse_password = getpass.getpass("Enter your CDSE password: ")
                if not self._check_cdse_credentials(self._cdse_username, self._cdse_password):
                    print(f"{Fore.RED}Authentication failed. Please check your credentials and try again.\n")
                    continue
                else:
                    print(f"{Fore.GREEN}Authentication successful.\n")
                    netrc_path = Path.home().joinpath(".netrc")
                    cdse_entry = f"\nmachine dataspace.copernicus.eu\n    login {self._cdse_username}\n    password {self._cdse_password}\n"
                    with open(netrc_path, 'a') as f:
                        f.write(cdse_entry)
                    print(f"{Fore.GREEN}Credentials saved to {netrc_path}. You can now download orbit from CDSE without entering credentials again.\n")
                    break

        base_dir = Path(save_dir) if save_dir else (getattr(self, 'download_dir', None) or Path(getattr(self.config, 'workdir', None) or Path.cwd()))
        all_items = [(key, result) for key, results in self.results.items() for result in results]  # type: ignore[union-attr]
        with tqdm(all_items, desc="Orbit files", unit="scene", bar_format="{l_bar}{bar:20}{r_bar}") as pbar:
            for key, result in pbar:
                if stop_event is not None and stop_event.is_set():
                    tqdm.write("Orbit download stopped.")
                    break
                download_path = Path(save_dir) if save_dir else Path(base_dir) / f'p{key[0]}_f{key[1]}'
                download_path.mkdir(parents=True, exist_ok=True)
                scene_name = result.properties['sceneName']
                short_name = scene_name[:40] + "..."
                acq_time = scene_name.replace("__", "_").split("_")[4]
                already_have = False
                for eof in download_path.glob("*.EOF"):
                    parts = eof.stem.split("_V")
                    if len(parts) == 2:
                        validity = parts[1].split("_")
                        if len(validity) == 2 and validity[0] <= acq_time <= validity[1]:
                            pbar.set_postfix_str(f"skip {short_name}")
                            already_have = True
                            break
                if already_have:
                    continue
                pbar.set_postfix_str(f"fetch {short_name}")
                _save = download_path.as_posix()
                try:
                    info = download_eofs(sentinel_file=scene_name, save_dir=_save, force_asf=force_asf)
                except Exception as e:
                    if not force_asf:
                        pbar.set_postfix_str(f"CDSE fail, try ASF {short_name}")
                        try:
                            info = download_eofs(sentinel_file=scene_name, save_dir=_save, force_asf=True)
                        except Exception as e2:
                            tqdm.write(f"{Fore.RED}[ERROR] {scene_name}: {e2}")
                            info = []
                    else:
                        tqdm.write(f"{Fore.RED}[ERROR] {scene_name}: {e}")
                        info = []
                if info:
                    pbar.set_postfix_str(f"ok {short_name}")
                else:
                    tqdm.write(f"{Fore.YELLOW}[WARN] No orbit file found for: {scene_name}")
    
    def _check_cdse_credentials(self, username: str, password: str) -> bool:
        url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        data = {
            "grant_type": "password",
            "client_id": "cdse-public",
            "username": username,
            "password": password
        }
        resp = requests.post(url, data=data)
        return resp.status_code == 200 and "access_token" in resp.json()

