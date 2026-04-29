
import shutil
import zipfile
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from pathlib import Path    


from colorama import Fore, Style
from osgeo import gdal
from tqdm import tqdm

from .mintpy_base import Mintpy_SBAS_Base_Analyzer
from insarhub.config.defaultconfig import Hyp3_SBAS_Config

class Hyp3_SBAS(Mintpy_SBAS_Base_Analyzer):
    name = 'Hyp3_SBAS'
    description = "SBAS time-series analysis of HyP3 InSAR outputs using MintPy."
    compatible_processor = "Hyp3_S1"
    default_config = Hyp3_SBAS_Config
    required = ['unw_phase.tif', 'corr.tif',  'dem.tif'] # also need meta files to get the date and other info
    optional = ['lv_theta.tif', 'lv_phi.tif', 'water_mask.tif']
    def __init__(self, config: Hyp3_SBAS_Config | None = None):
        super().__init__(config)

    def prep_data(self):
        """
        Prepare input data for analysis by performing unzipping, collection, clipping, and parameter setup.

        This method orchestrates the preprocessing steps required before running the analysis workflow. 
        It ensures that all input files are available, aligned, and properly configured.

        Steps performed:
            1. `_unzip_hyp3()`: Extracts any compressed Hyp3 output files.
            2. `_collect_files()`: Gathers relevant input files (e.g., DEMs, interferograms).
            3. `_get_common_overlap(files['dem'])`: Computes the spatial overlap extent among input rasters.
            4. `_clip_rasters(files, overlap_extent)`: Clips input rasters to the common overlapping area.
            5. `_set_load_parameters()`: Sets parameters required for loading the preprocessed data into memory.

        Raises:
            FileNotFoundError: If required input files are missing.
            ValueError: If no common overlap region can be determined among rasters.
            Exception: Propagates any unexpected errors during preprocessing.

        Notes:
            - This method must be called before running the analysis workflow.
            - Designed for workflows using Hyp3-derived Sentinel-1 products.
            - Ensures consistent spatial coverage across all input datasets.
        """
        self._unzip_hyp3()
        files = self._collect_files()
        overlap_extent = self._get_common_overlap(files['dem'])
        self._clip_rasters(files, overlap_extent)
        self._set_load_parameters()
        super().prep_data()

    def _unzip_hyp3(self):
        print(f'{Fore.CYAN}Unzipping HyP3 Products...{Fore.RESET}')

        hyp3_results = list(self.workdir.rglob('*.zip'))
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        with tqdm(hyp3_results, desc="Processing", unit="file") as pbar:
            for zip_file in pbar:
                extract_target = self.tmp_dir / zip_file.stem
                with zipfile.ZipFile(zip_file, 'r') as zf:
                    needs_extraction = True
                    if extract_target.is_dir():
                        files_in_zip = {Path(f).name for f in zf.namelist() if not f.endswith('/')}
                        folder_files = {f.name for f in extract_target.iterdir() if f.is_file()}
                        if files_in_zip.issubset(folder_files):
                            needs_extraction = False
                            pbar.set_description(f"File Exist: {zip_file.stem[:30]}...")
                    if needs_extraction:
                        pbar.set_description(f"Extracting: {zip_file.stem[:30]}...")
                        if extract_target.is_dir():
                            shutil.rmtree(extract_target)
                            
                        zf.extractall(self.tmp_dir)
        print(f'\n{Fore.GREEN}Unzipping complete.{Fore.RESET}')

    def _collect_files(self):
        print(f'{Fore.CYAN}Mapping file paths...{Fore.RESET}')
        all_required = {ext.split('.')[0]: ext for ext in self.required}    
        all_optional = {ext.split('.')[0]: ext for ext in self.optional}
        files = defaultdict(list)
        files['meta'] = [m for m in self.tmp_dir.rglob('*.txt') if 'README' not in m.name]
        for cat_name, ext in {**all_required, **all_optional}.items():
            files[cat_name] = list(self.tmp_dir.rglob(f"*_{ext}"))

        missing_req = [name for name, ext in all_required.items() if not files[name]]
        if missing_req or not files['meta']:
            print(f"\033[K", end="\r") # Clear current line
            msg = []
            if missing_req: msg.append(f"Missing rasters: {missing_req}")
            if not files['meta']: msg.append("Missing metadata (.txt) files")
            
            error_report = f"{Fore.RED}CRITICAL ERROR: {'. '.join(msg)}.{Fore.RESET}\n" \
                           f"MintPy requires these files to extract dates and baselines."
            raise FileNotFoundError(error_report)
        missing_opt = [name for name in all_optional if not files[name]]

        total_pairs = len(files['unw_phase'])
        status_msg = f"{Fore.GREEN}Found {total_pairs} pairs | Metadata: OK"
        if missing_opt:
            status_msg += f" | {Fore.YELLOW}Missing optional: {missing_opt}"
        
        print(f"\r\033[K{status_msg}{Fore.RESET}")
        return files

    def _get_common_overlap(self, dem_files):
        ulx_l, uly_l, lrx_l, lry_l = [], [], [], []
        for f in dem_files:
            ds = gdal.Open(f.as_posix())
            gt = ds.GetGeoTransform() # (ulx, xres, xrot, uly, yrot, yres)
            ulx, uly = gt[0], gt[3]
            lrx, lry = gt[0] + gt[1] * ds.RasterXSize, gt[3] + gt[5] * ds.RasterYSize
            ulx_l.append(ulx)
            uly_l.append(uly)
            lrx_l.append(lrx)
            lry_l.append(lry)
            ds = None
        return  (max(ulx_l), min(uly_l), min(lrx_l), max(lry_l))
    
    def _clip_rasters(self, files, overlap_extent):
        print(f'{Fore.CYAN}Clipping rasters to common overlap...{Fore.RESET}')
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        categories = [k for k in files.keys() if k != 'meta']

        with tqdm(categories, desc="Progress", position=0, dynamic_ncols=True) as pbar_out:
            for key in pbar_out:
                file_list = files[key]
                pbar_out.set_description(f"Group: {key}")

                # Inner progress bar for individual files in this group
                # leave=False ensures the inner bar disappears when the group is done
                with tqdm(file_list, desc=f"  -> Clipping", leave=False, position=1, unit="file", dynamic_ncols=True) as pbar_in:
                    for f in pbar_in:
                        out = self.clip_dir / f"{f.stem}_clip.tif"

                        if out.exists():
                            pbar_in.set_postfix_str(f"Skip: {f.name[:15]}...")
                            # Update postfix instead of printing to avoid creating new lines
                            continue

                        pbar_in.set_postfix_str(f"File: {f.name[:15]}...")

                        try:
                            gdal.Translate(
                                destName=out.as_posix(),
                                srcDS=f.as_posix(),
                                projWin=overlap_extent
                            )
                        except Exception as e:
                            tqdm.write(f"{Fore.RED}Error clipping {f.name}: {e}{Fore.RESET}")

            # Handle metadata separately as it's just a file copy (no progress bar needed)
        if 'meta' in files:
            print(f"\r{Fore.CYAN}Step: Copying metadata files... \033[K", end="", flush=True)
            for f in files['meta']:
                shutil.copy(f, self.clip_dir / f.name)

        print(f'\n{Fore.GREEN}Clipping complete.{Fore.RESET}')

    def _set_load_parameters(self):
        self.config.load_unwFile = (self.clip_dir / '*_unw_phase_clip.tif').as_posix()
        self.config.load_corFile = (self.clip_dir / '*_corr_clip.tif').as_posix()
        self.config.load_demFile = (self.clip_dir / '*_dem_clip.tif').as_posix()
        opt_map = {
            'lv_theta': 'load_incAngleFile',
            'lv_phi': 'load_azAngleFile',
            'water_mask': 'load_waterMaskFile'
        }
        for k, cfg_attr in opt_map.items():
            if list(self.clip_dir.glob(f"*_{k}_clip.tif")):
                setattr(self.config, cfg_attr, (self.clip_dir / f"*_{k}_clip.tif").as_posix())


      