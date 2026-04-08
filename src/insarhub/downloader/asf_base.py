# -*- coding: utf-8 -*-
import getpass
import threading
import time
from dataclasses import asdict
from dateutil.parser import isoparse
from collections import defaultdict
from pathlib import Path


import asf_search as asf
import contextily as ctx
import dem_stitcher
import matplotlib.pyplot as plt
import rasterio as rio
from asf_search.exceptions import ASFAuthenticationError
from colorama import Fore
from pyproj import Transformer
from shapely import wkt, plotting
from shapely.ops import transform
from shapely.geometry import shape
from tqdm import tqdm

from insarhub.core.base import BaseDownloader
from insarhub.config import ASF_Base_Config
from insarhub.utils.tool import _to_wkt

class ASF_Base_Downloader(BaseDownloader):
    """
    Simplify searching and downloading satellite data using ASF Search API.
    """
    description = "Generic ASF Search API downloader. Supports Sentinel-1, ALOS, NISAR, and more."
    default_config = ASF_Base_Config
    _DATASET_GROUP_KEYS = {
        'SENTINEL-1': ('pathNumber', 'frameNumber'),
        'ALOS':       ('pathNumber', 'frameNumber'),
        'NISAR':      ('pathNumber', 'frameID'),
        'BURST':      ('pathNumber', 'burstID'),
    }
    _DATASET_PROPERTY_KEYS = {
        'SENTINEL-1': {
            'relativeOrbit': 'pathNumber',
            'absoluteOrbit': 'absoluteOrbit',
            'polarization':  'polarization',
            'flightDirection': 'flightDirection',
        },
        'ALOS': {
            'relativeOrbit': 'pathNumber',
            'absoluteOrbit': 'absoluteOrbit',
            'polarization':  'polarization',
            'flightDirection': 'flightDirection',
        },
        'NISAR': {
            'relativeOrbit': 'relativeOrbit',
            'absoluteOrbit': 'absoluteOrbit',
            'polarization':  'polarization',
            'flightDirection': 'flightDirection',
        },
    }

    def __init__(self, config: ASF_Base_Config | None = None): 
            
        """
        Initialize the Downloader with search parameters. Options was adapted from asf_search searching api. 
        You may check https://docs.asf.alaska.edu/asf_search/searching/ for more info, below only list customized parameters.
        """
        print(f"""
This downloader relies on the ASF API. Please ensure you to create an account at https://search.asf.alaska.edu/. 
If a .netrc file is not provide under your home directory, you will be prompt to enter your ASF username and password. 
Check documentation for how to setup .netrc file.\n""")
        super().__init__(config)

        if self.config.dataset is None and self.config.platform is None and not getattr(self.config, 'granule_names', None):
            raise ValueError(f"{Fore.RED}Dataset or platform must be specified for ASF search (or provide granule_names).")
        
        self.config.intersectsWith = _to_wkt(self.config.intersectsWith)
        
        
    def _asf_authorize(self):
        self._has_asf_netrc = self._check_netrc(keyword='machine urs.earthdata.nasa.gov')
        if not self._has_asf_netrc:
            while True:
                _username = input("Enter your ASF username: ")
                _password = getpass.getpass("Enter your ASF password: ")
                try:
                    self._session = asf.ASFSession().auth_with_creds(_username, _password)
                except ASFAuthenticationError:
                    print(f"{Fore.RED}Authentication failed. Please check your credentials and try again.\n")
                    continue
                print(f"{Fore.GREEN}Authentication successful.\n")
                netrc_path = Path.home().joinpath(".netrc")
                asf_entry = f"\nmachine urs.earthdata.nasa.gov\n    login {_username}\n    password {_password}\n"
                with open(netrc_path, 'a') as f:
                    f.write(asf_entry)
                print(f"{Fore.GREEN}Credentials saved to {netrc_path}. You can now use the downloader without entering credentials again.\n")
                break
        else:
            self._session = asf.ASFSession()
       
    def _check_netrc(self, keyword: str) -> bool:
        """Check if .netrc file exists in the home directory with the specified keyword.
        
        Args:
            keyword (str): The machine name to search for in .netrc file.
            
        Returns:
            bool: True if .netrc file exists and contains the keyword, False otherwise.
        """
        netrc_path = Path.home().joinpath('.netrc')
        if not netrc_path.is_file():            
            print(f"{Fore.RED}No .netrc file found in your home directory. Will prompt login.\n")
            return False
        else: 
            with netrc_path.open() as f:
                content = f.read()
                if keyword in content:
                    return True
                else:
                    print(f"{Fore.RED}no machine name {keyword} found .netrc file. Will prompt login.\n")
                    return False
                
    
    def _get_group_key(self, result) -> tuple:
        """Derive grouping key based on available properties, with fallback.
        
        Args:
            result: Search result object containing properties.
            
        Returns:
            tuple: A tuple of (path_number, frame_identifier) used for grouping results.
        """
        props = result.properties
        # Burst product — any burst ID field set in config takes highest priority
        if any([
            self.config.absoluteBurstID,
            self.config.fullBurstID,
            self.config.operaBurstID,
            self.config.relativeBurstID,
        ]):
            return (props.get('pathNumber'), props.get('burstID'))
        
        if self.config.asfFrame is not None:
            # 'asfFrame' is a search filter parameter, not a scene property name.
            # Use 'frameNumber' (the actual returned property) for consistent grouping.
            return (props.get('pathNumber'), props.get('frameNumber'))
        
        if self.config.frame is not None:
            return (props.get('pathNumber'), props.get('frameNumber'))
        
        # Dataset-level mapping
        if self.config.dataset:
            datasets = [self.config.dataset] if isinstance(self.config.dataset, str) else self.config.dataset
            for ds in datasets:
                ds_upper = ds.upper()
                if ds_upper in self._DATASET_GROUP_KEYS:
                    pk, fk = self._DATASET_GROUP_KEYS[ds_upper]
                    return (props.get(pk), props.get(fk))
        # Platform-level fallback mapping      
        if self.config.platform:
            platforms = [self.config.platform] if isinstance(self.config.platform, str) else self.config.platform
            for pl in platforms:
                pl_upper = pl.upper()
                if 'SENTINEL' in pl_upper:
                    return (props.get('pathNumber'), props.get('frameNumber'))
                if 'ALOS' in pl_upper:
                    return (props.get('pathNumber'), props.get('frameNumber'))
                if 'NISAR' in pl_upper:
                    return (props.get('pathNumber'), props.get('frameID'))
        # last resort — group everything under the platform name
        return (props.get('pathNumber'), props.get('frameNumber'))
    
    def _get_property_keys(self) -> dict:
        """Return the correct result.properties key mapping based on config.
        
        Returns:
            dict: Mapping of property names to their corresponding keys in search results.
        """
        if self.config.dataset:
            datasets = [self.config.dataset] if isinstance(self.config.dataset, str) else self.config.dataset
            for ds in datasets:
                ds_upper = ds.upper()
                if ds_upper in self._DATASET_PROPERTY_KEYS:
                    return self._DATASET_PROPERTY_KEYS[ds_upper]

        if self.config.platform:
            platforms = [self.config.platform] if isinstance(self.config.platform, str) else self.config.platform
            for pl in platforms:
                if 'SENTINEL' in pl.upper():
                    return self._DATASET_PROPERTY_KEYS['SENTINEL-1']
                if 'ALOS' in pl.upper():
                    return self._DATASET_PROPERTY_KEYS['ALOS']
                if 'NISAR' in pl.upper():
                    return self._DATASET_PROPERTY_KEYS['NISAR']

        # Default to Sentinel-1 keys as they are most common
        return self._DATASET_PROPERTY_KEYS['SENTINEL-1']
                
    @property
    def session(self):
        """Get or create an authenticated ASF session.
        
        Returns:
            ASFSession: Authenticated session for ASF downloads.
        """
        if not hasattr(self, '_session') or self._session is None:
            self._asf_authorize()
        return self._session
    
    @property
    def active_results(self):
        """Get the currently active results (filtered or full search results).
        
        Returns the subset of results if a filter/pick is active, 
        otherwise returns the full search results.
        
        Returns:
            dict: Dictionary of active search results grouped by (path, frame).
            
        Raises:
            ValueError: If no search results are available.
        """
        if not hasattr(self, 'results'):
             raise ValueError(f"{Fore.RED}No search results found. Please run search() first.")
        return self._subset if self._subset is not None else self.results
                
    def search(self) -> dict:
        """Search for data using the ASF Search API with the provided parameters.

        When ``config.granule_names`` is set the search is performed by granule
        name instead of the normal parameter search.  ``granule_names`` may be:

        * A ``list[str]`` of scene/granule names (with or without extensions).
        * A ``str`` containing a single name, a comma-separated list of names,
          or a path to a CSV / XLSX / TXT file on disk.

        Returns:
            dict: Dictionary of search results grouped by (path, frame) tuples.

        Raises:
            ValueError: If search returns no results.
            Exception: If search fails after 10 retry attempts.
        """
        self._subset = None

        granule_names = getattr(self.config, 'granule_names', None)
        if granule_names:
            print(f"{Fore.GREEN}Granule_names provided, Performing search by granule name(s) from {self.config.granule_names}...{Fore.RESET}")
            from insarhub.utils.tool import parse_scene_names_from_file
            raw_inputs = granule_names if isinstance(granule_names, list) else [n.strip() for n in granule_names.split(',') if n.strip()]
            names: list[str] = []
            for item in raw_inputs:
                p = Path(item)
                if p.exists():
                    names.extend(parse_scene_names_from_file(str(p)))
                else:
                    names.append(item)
            return self._search_by_name(names)

        print(f"Searching for SLCs....")
        search_opts = {k: v for k, v in asdict(self.config).items()
                       if v is not None and k not in ['workdir', 'name', 'bbox', 'granule_names']}

        for attempt in range(1, 11):
            try:
                self.results = asf.search(**search_opts)
                break
            except Exception as e:
                print(f"{Fore.RED}Search failed: {e}")
                if attempt == 10:
                    raise
                time.sleep(2 ** attempt)

        if not self.results:
            raise ValueError(f'{Fore.RED}Search does not return any result, please check input parameters or Internet connection')
        else:
            print(f"{Fore.GREEN} -- A total of {len(self.results)} results found. \n")

        grouped = defaultdict(list)
        for result in self.results:
            key = self._get_group_key(result)
            grouped[key].append(result)
        self.results = grouped
        if len(grouped) > 1:
            print(f"{Fore.YELLOW}The AOI crosses {len(grouped)} stacks")
        return grouped

    def _search_by_name(self, scene_names: list[str]) -> dict:
        """Populate results from a list of scene/granule names or filenames.

        Accepts names with or without file extensions (e.g. ``.zip``).
        Uses ``asf_search.granule_search()`` so no config parameters are needed
        and works for any ASF-supported dataset (S1 SLC, S1 Burst, ALOS, etc.).

        Args:
            scene_names: Scene or filename strings, e.g.
                ``["S1A_IW_SLC__1SDV_20201227T133500_..._5DB4",
                   "S1A_IW_SLC__1SDV_20201227T133500_..._5DB4.zip"]``

        Returns:
            Grouped results dict keyed by ``(relativeOrbit, frame)``.
        """
        # Strip common file extensions so granule_search can find them
        clean = [Path(n).stem if '.' in n else n for n in scene_names]
        raw = asf.granule_search(clean)
        if not raw:
            raise ValueError(f"No ASF results found for the {len(clean)} provided scene name(s).")

        # granule_search returns all product types per granule (SLC + METADATA_SLC, etc.).
        # Exclude metadata-only products, then deduplicate by sceneName.
        _EXCLUDE_LEVELS = {'METADATA_SLC', 'METADATA'}
        seen: set[str] = set()
        deduped = []
        for result in raw:
            if result.properties.get('processingLevel', '') in _EXCLUDE_LEVELS:
                continue
            sname = result.properties.get('sceneName', '')
            if sname not in seen:
                seen.add(sname)
                deduped.append(result)

        grouped: dict = defaultdict(list)
        for result in deduped:
            key = self._get_group_key(result)
            grouped[key].append(result)
        self.results = grouped
        print(f"{Fore.GREEN} -- Found {len(deduped)} scenes across {len(grouped)} stack(s).\n")
        if len(deduped) < len(clean):
            missing = len(clean) - len(deduped)
            print(f"{Fore.YELLOW} -- {missing} scene(s) not found on ASF.\n")
        return grouped

    def reset(self):
        """Reset the view to include all search results.
        
        Clears any active filters and restores the full result set.
        """
        self._subset = None
        print(f"{Fore.GREEN}Selection reset. Now viewing all {len(self.results)} stacks.")
 
    def summary(self, ls=False):
        """Summarize the active results, separated by flight direction.
        
        Args:
            ls (bool, optional): If True, list individual scene names and dates. 
                Defaults to False.
        """
        if not hasattr(self, 'results'):
            self.search()

        active_results = self.active_results

        if not active_results:
            print(f"{Fore.YELLOW}No results to summarize.")
            return
        
        ascending_stacks = {}
        descending_stacks = {}

        for key, items in active_results.items():
            if not items: continue
            direction = items[0].properties.get('flightDirection', 'UNKNOWN').upper()

            if direction == 'ASCENDING':
                ascending_stacks[key] = items
            elif direction == 'DESCENDING':
                descending_stacks[key] = items

        def _print_group(label, data_dict, color_code):
            if not data_dict:
                return
            print(f"\n{color_code}=== {label} ORBITS ({len(data_dict)} Stacks) ==={Fore.RESET}")
            sorted_keys = sorted(data_dict.keys())

            for key in sorted_keys:
                    items = data_dict[key]
                    count = len(items)
                    
                    # Calculate time range
                    dates = [isoparse(i.properties['startTime']) for i in items]
                    start_date = min(dates).date()
                    end_date = max(dates).date()
                    
                    print(f"relativeOrbit {key[0]} frame {key[1]} | Count: {count} | {start_date} --> {end_date}")
                    
                    if ls:
                        # Sort scenes by date
                        items_sorted = sorted(items, key=lambda x: isoparse(x.properties['startTime']))
                        for scene in items_sorted:
                            scene_date = isoparse(scene.properties['startTime']).date()
                            print(f"    {Fore.LIGHTBLACK_EX}{scene.properties['sceneName']} ({scene_date}){Fore.RESET}")
        if ascending_stacks:
            _print_group("ASCENDING", ascending_stacks, Fore.MAGENTA)

        if descending_stacks:
            _print_group("DESCENDING", descending_stacks, Fore.CYAN)

        print("") # Final newline


    def footprint(self, save_path: str | None = None):
        """Display or save the search result footprints and AOI using matplotlib.
        
        Args:
            save_path (str, optional): Path to save the figure. If None, displays interactively.
                Defaults to None.
        """
        results_to_plot = self.active_results
        if not results_to_plot:
            print(f"{Fore.RED}No results to plot.")
            return
        
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        N = len(results_to_plot)
        cmap = plt.cm.get_cmap('hsv', N+1)

        fig, ax = plt.subplots(1, 1, figsize=(10,10), dpi=150)

        geom_aoi = transform(transformer.transform, wkt.loads(self.config.intersectsWith))
        global_minx, global_miny, global_maxx, global_maxy = geom_aoi.bounds
        plotting.plot_polygon(geom_aoi, ax=ax, edgecolor='red', facecolor='none', linewidth=2, linestyle='--')

        label_x_aoi = global_maxx - 0.01 * (global_maxx - global_minx)
        label_y_aoi = global_maxy - 0.01 * (global_maxy - global_miny)
        plt.text(label_x_aoi, label_y_aoi,
             f"AOI",
             horizontalalignment='right', verticalalignment='top',
             fontsize=12, color='red', fontweight='bold',
             bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', boxstyle='round,pad=0.3'))
        
        for i, (key, results) in enumerate(results_to_plot.items()):
            geom = transform(transformer.transform, shape(results[0].geometry))
            minx, miny, maxx, maxy = geom.bounds

            global_minx = min(global_minx, minx)
            global_miny = min(global_miny, miny)
            global_maxx = max(global_maxx, maxx)
            global_maxy = max(global_maxy, maxy)

            label_x = maxx - 0.01 * (maxx - minx)
            label_y = maxy - 0.01 * (maxy - miny)

            plt.text(label_x, label_y,
             f"Path: {key[0]}\nFrame: {key[1]}\nStack: {len(results)}",
             horizontalalignment='right', verticalalignment='top',
             fontsize=12, color=cmap(i), fontweight='bold',
             bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', boxstyle='round,pad=0.3'))
            
            for result in results:
                geom = transform(transformer.transform, shape(result.geometry))
                x, y = geom.exterior.xy
                ax.plot(x, y, color=cmap(i))
        
        ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik)

        ax.set_xlim(global_minx, global_maxx)
        ax.set_ylim(global_miny, global_maxy)

        ax.set_axis_off()
        if save_path is not None:
            save_path = Path(save_path).expanduser().resolve()
            plt.savefig(save_path.as_posix(), dpi=300, bbox_inches='tight')
            print(f"Footprint figure saved to {save_path}")
        else:
            plt.subplots_adjust(top = 1, bottom = 0, right = 1, left = 0, hspace = 0, wspace = 0)
            plt.show()
        
    def filter(self, 
                path_frame : tuple | list[tuple] | None = None,
                start: str | None = None,
                end: str | None = None,
                frame: int | list[int] | None = None, 
                asfFrame: int | list[int] | None = None, 
                flightDirection: str | None = None,
                relativeOrbit: int | list[int] | None = None,
                absoluteOrbit: int | list[int] | None = None,
                lookDirection: str | None = None,
                polarization: str | list[str] | None = None,
                processingLevel: str | None = None,
                beamMode: str | None = None,
                season: list[int] | None = None,
                min_coverage: float | None = None,
                min_count: int | None = None,
                max_count: int | None = None,
                latest_n: int | None = None,
                earliest_n: int | None = None
               ) -> dict:
        """Filter active results by various properties after search.

        Args:
            path_frame (tuple | list[tuple], optional): A single (path, frame) tuple or list of tuples.
                Defaults to None.
            start (str, optional): Start date string, e.g. '2021-01-01'. Defaults to None.
            end (str, optional): End date string, e.g. '2023-12-31'. Defaults to None.
            frame (int | list[int], optional): Sensor native frame number(s), e.g. 50. Defaults to None.
            asfFrame (int | list[int], optional): ASF internal frame number(s), e.g. 50. Defaults to None.
            flightDirection (str, optional): 'ASCENDING' or 'DESCENDING'. Defaults to None.
            relativeOrbit (int | list[int], optional): Relative orbit number(s) to keep. Defaults to None.
            absoluteOrbit (int | list[int], optional): Absolute orbit number(s) to keep. Defaults to None.
            lookDirection (str, optional): 'LEFT' or 'RIGHT'. Defaults to None.
            polarization (str | list[str], optional): Polarization(s) to keep, e.g. 'VV' or ['VV', 'VH']. 
                Defaults to None.
            processingLevel (str, optional): Processing level to keep, e.g. 'SLC'. Defaults to None.
            beamMode (str, optional): Beam mode to keep, e.g. 'IW'. Defaults to None.
            season (list[int], optional): List of months (1-12) to keep, e.g. [6, 7, 8] for summer. 
                Defaults to None.
            min_coverage (float, optional): Minimum fractional overlap (0-1) between scene and AOI. 
                Defaults to None.
            min_count (int, optional): Drop stacks with fewer than this many scenes after filtering. 
                Defaults to None.
            max_count (int, optional): Keep at most this many scenes per stack (from earliest). 
                Defaults to None.
            latest_n (int, optional): Keep the N most recent scenes per stack. Defaults to None.
            earliest_n (int, optional): Keep the N earliest scenes per stack. Defaults to None.
            
        Returns:
            dict: Filtered results grouped by (path, frame).
            
        Raises:
            ValueError: If no search results are available.
        """
        
        if not hasattr(self, 'results'):
            raise ValueError(f"{Fore.RED}No search results found. Please run search() first.")
        
        source = self.active_results
        filtered = defaultdict(list)
        prop_keys = self._get_property_keys()

        # --- Pre-process filter values ---
        if path_frame is not None:
            targets = {path_frame} if isinstance(path_frame, tuple) else set(path_frame)
        else:
            targets = None

        start_dt = isoparse(start).replace(tzinfo=None) if start else None
        end_dt   = isoparse(end).replace(tzinfo=None)   if end   else None
        frames     = {frame}    if isinstance(frame, int)    else set(frame)    if frame    else None
        asf_frames = {asfFrame} if isinstance(asfFrame, int) else set(asfFrame) if asfFrame else None
        relative_orbits  = {relativeOrbit}  if isinstance(relativeOrbit, int)  else set(relativeOrbit)  if relativeOrbit  else None
        absolute_orbits  = {absoluteOrbit}  if isinstance(absoluteOrbit, int)  else set(absoluteOrbit)  if absoluteOrbit  else None
        polarizations    = {polarization}   if isinstance(polarization, str)   else set(polarization)   if polarization   else None
        season_months    = set(season) if season else None

        if min_coverage is not None:
            aoi_geom = wkt.loads(self.config.intersectsWith)
        
        for key, items in source.items():
            if targets is not None and key not in targets:
                continue

            if flightDirection:
                stack_dir = items[0].properties.get('flightDirection', '').upper()
                if stack_dir != flightDirection.upper():
                    continue
            
            if lookDirection:
                stack_look = items[0].properties.get('lookDirection', '').upper()
                if stack_look != lookDirection.upper():
                    continue
            
            if beamMode:
                stack_beam = items[0].properties.get('beamMode', '').upper()
                if stack_beam != beamMode.upper():
                    continue

            if processingLevel:
                stack_proc = items[0].properties.get('processingLevel', '').upper()
                if stack_proc != processingLevel.upper():
                    continue
        # --- Scene-level filters ---
            filtered_items = []
            for item in items:
                props = item.properties

                scene_dt = isoparse(props['startTime']).replace(tzinfo=None)
                # Date range
                if start_dt and scene_dt < start_dt:
                    continue
                if end_dt and scene_dt > end_dt:
                    continue

                # Native frame filter
                if frames is not None:
                    if props.get('frameNumber') not in frames:
                        continue

                # ASF frame filter
                if asf_frames is not None:
                    if props.get('asfFrame') not in asf_frames:
                        continue
                # Season (month filter)
                if season_months and scene_dt.month not in season_months:
                    continue
                
                # Relative orbit
                if relative_orbits and props.get(prop_keys['relativeOrbit']) not in relative_orbits:
                    continue

                # Absolute orbit
                if absolute_orbits and props.get(prop_keys['absoluteOrbit']) not in absolute_orbits:
                    continue

                # Polarization — props value may be a string like 'VV+VH'
                if polarizations:
                    scene_pols = set(props.get(prop_keys['polarization'], '').replace('+', ' ').split())
                    if not polarizations.intersection(scene_pols):
                        continue

                if min_coverage is not None:
                    scene_geom = shape(item.geometry)
                    intersection = aoi_geom.intersection(scene_geom)
                    coverage = intersection.area / aoi_geom.area
                    if coverage < min_coverage:
                        continue
                
                filtered_items.append(item)
            if not filtered_items:
                continue
            

            filtered_items = sorted(filtered_items, key=lambda x: isoparse(x.properties['startTime']))

            if earliest_n is not None:
                filtered_items = filtered_items[:earliest_n]
            elif latest_n is not None:
                filtered_items = filtered_items[-latest_n:]
            elif max_count is not None:
                filtered_items = filtered_items[:max_count]
            
            if min_count is not None and len(filtered_items) < min_count:
                print(f"{Fore.YELLOW}Stack Path {key[0]} Frame {key[1]} dropped: only {len(filtered_items)} scenes (min_count={min_count}).")
                continue

            filtered[key] = filtered_items

        if not filtered:
            print(f"{Fore.YELLOW}Warning: No results matched the given filters.")
        else:
            self._subset = filtered
            total_scenes = sum(len(v) for v in filtered.values())
            print(f"{Fore.GREEN}Filter applied. {len(filtered)} stacks, {total_scenes} total scenes remaining.")

        return filtered
    
    def dem(self, save_path: str | None = None):
        """Download DEM for co-registration uses.
        
        Args:
            save_path (str, optional): Directory to save DEM files. If None, uses config.workdir.
                Defaults to None.
                
        Returns:
            tuple: (X, p) where X is the DEM array and p is the rasterio profile.
        """
        output_dir = Path(save_path).expanduser().resolve() if save_path else self.config.workdir

        for key, results in self.active_results.items():
            download_path = output_dir.joinpath(f'dem',f'p{key[0]}_f{key[1]}')
            download_path.mkdir(exist_ok=True, parents=True)
            geom = shape(results[0].geometry)
            west_lon, south_lat, east_lon, north_lat =  geom.bounds
            bbox = [ west_lon, south_lat, east_lon, north_lat]
            X, p = dem_stitcher.stitch_dem(
                bbox, 
                dem_name='glo_30',
                dst_area_or_point='Point',
                dst_ellipsoidal_height=True
            )
            
            with rio.open(download_path.joinpath(f'dem_p{key[0]}_f{key[1]}.tif'), 'w', **p) as ds:
                    ds.write(X,1)
                    ds.update_tags(AREA_OR_POINT='Point')
        return X, p
    
    def select_pairs(
        self,
        dt_targets: tuple = (6, 12, 24, 36, 48, 72, 96),
        dt_tol: int = 3,
        dt_max: int = 120,
        pb_max: float = 150.0,
        min_degree: int = 3,
        max_degree: int = 5,
        force_connect: bool = True,
        max_workers: int = 4,
    ) -> tuple:
        """Compute interferogram pairs for all active stacks.

        Args:
            dt_targets (tuple, optional): Target temporal spacings in days. Defaults to (6, 12, 24, 36, 48, 72, 96).
            dt_tol (int, optional): Tolerance in days around each target spacing. Defaults to 3.
            dt_max (int, optional): Maximum temporal baseline in days. Defaults to 120.
            pb_max (float, optional): Maximum perpendicular baseline in meters. Defaults to 150.0.
            min_degree (int, optional): Minimum number of connections per scene. Defaults to 3.
            max_degree (int, optional): Maximum number of connections per scene. Defaults to 5.
            force_connect (bool, optional): Force connectivity for isolated scenes. Defaults to True.
            max_workers (int, optional): Threads for API baseline fallback. Defaults to 4.

        Returns:
            tuple: (pairs, baselines, scene_bperp)
                - pairs: dict keyed by (path, frame) for multi-stack, or list for single stack
                - baselines: temporal baselines
                - scene_bperp: perpendicular baselines per scene
        """
        from insarhub.utils.tool import select_pairs as _select_pairs

        if not hasattr(self, 'results'):
            raise ValueError("No search results found. Please run search() first.")

        _sp_result = _select_pairs(
            self.active_results,
            dt_targets=dt_targets,
            dt_tol=dt_tol,
            dt_max=dt_max,
            pb_max=pb_max,
            min_degree=min_degree,
            max_degree=max_degree,
            force_connect=force_connect,
            max_workers=max_workers,
        )
        pairs, baselines = _sp_result[0], _sp_result[1]
        scene_bperp: dict = _sp_result[2] if len(_sp_result) > 2 else {}
        return pairs, baselines, scene_bperp

    def download(self, save_path: str | None = None, max_workers: int = 3, stop_event=None, on_progress=None):
        """Download the search results to the specified output directory.

        Args:
            save_path (str, optional): Download path. If None, uses config.workdir.
                Defaults to None.
            max_workers (int, optional): Number of concurrent downloads. 3-5 recommended
                for ASF. Set to 1 to disable multithreading. Defaults to 3.

        Raises:
            ValueError: If no search results are available.
        """
        import json as _json
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from insarhub.utils.tool import write_workflow_marker
        output_dir = Path(save_path).expanduser().resolve() if save_path else self.config.workdir
        output_dir.mkdir(exist_ok=True, parents=True)

        self.download_dir = output_dir

        if not hasattr(self, 'results'):
            raise ValueError(f"{Fore.RED}No search results found. Please run search() first.")

        if stop_event is None:
            stop_event = threading.Event()
        _cfg_base = {k: v for k, v in asdict(self.config).items() if k != 'workdir'}

        jobs = []
        for key, results in self.active_results.items():
            download_path = self.download_dir / f'p{key[0]}_f{key[1]}'
            download_path.mkdir(parents=True, exist_ok=True)
            write_workflow_marker(download_path, downloader=type(self).name)
            _cfg = {**_cfg_base, 'relativeOrbit': key[0], 'frame': key[1]}
            (download_path / "downloader_config.json").write_text(_json.dumps(_cfg, indent=2, default=str))
            for result in results:
                jobs.append((key, result, download_path))
        
        total_jobs   = len(jobs)
        success_count = 0
        failure_count = 0
        failed_files  = []

        active_files: dict[int, Path] = {}
        active_files_lock = threading.Lock()

        print(f"Downloading {total_jobs} scenes across "
          f"{len(self.active_results)} stacks "
          f"({max_workers} concurrent)...\n")
        
        def _stream_download_interruptible(url, file_path, expected_bytes, 
                                        pbar_position, scene_name):
            """Stream download that checks stop_event on every chunk."""
            from tqdm import tqdm
            from asf_search.download.download import _try_get_response

            thread_session = asf.ASFSession()
            thread_session.cookies.update(self.session.cookies)
            thread_session.headers.update(self.session.headers)

            for attempt in range(1, 4):
                if stop_event.is_set():
                    raise InterruptedError("Download cancelled by user.")
                try:
                    response = _try_get_response(session=thread_session, url=url)
                    total_bytes = int(response.headers.get('content-length', expected_bytes))

                    with tqdm(
                        total=total_bytes,
                        unit='B',
                        unit_scale=True,
                        unit_divisor=1024,
                        desc=f"[Worker {pbar_position+1}] {scene_name}",
                        bar_format='{desc:<60}{percentage:3.0f}%|{bar:25}{r_bar}',
                        colour='green',
                        position=pbar_position,
                        leave=True,
                    ) as pbar:
                        with open(file_path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=65536):
                                # Check stop event on EVERY chunk — this is the key
                                if stop_event.is_set():
                                    response.close()  # abort the connection immediately
                                    raise InterruptedError("Download cancelled by user.")
                                if chunk:
                                    f.write(chunk)
                                    pbar.update(len(chunk))
                    return  # success

                except InterruptedError:
                    raise  # propagate immediately, don't retry
                except Exception as e:
                    if file_path.exists():
                        file_path.unlink()
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
        
        def _download_job(args):
            key, result, download_path, position = args
            file_id   = result.properties['fileID']
            size_b    = result.properties['bytes']
            size_mb   = size_b / (1024 * 1024)
            filename  = result.properties.get('fileName', f"{file_id}.zip")
            file_path = download_path / filename

            scene_name = result.properties.get('sceneName', file_id)

            if stop_event.is_set():
                return file_id, 'cancelled', 0, None

            # Skip if already complete
            if file_path.exists() and file_path.stat().st_size == size_b:
                return file_id, 'skipped', size_mb, None

            # Remove incomplete file
            if file_path.exists():
                file_path.unlink()

            with active_files_lock:
                active_files[position] = file_path

            try:
                start_time = time.time()
                _stream_download_interruptible(
                    url=result.properties['url'],
                    file_path=file_path,
                    expected_bytes=size_b,
                    pbar_position=position,
                    scene_name=scene_name,
                )

                actual_size = file_path.stat().st_size
                if actual_size != size_b:
                    raise IOError(f"Size mismatch: expected {size_b}, got {actual_size} bytes.")

                elapsed = time.time() - start_time
                speed   = size_mb / elapsed if elapsed > 0 else 0
                return file_id, 'success', speed, None
            
            except InterruptedError:
                return file_id, 'cancelled', 0, None

            except Exception as e:
                if file_path.exists():
                    file_path.unlink()
                return file_id, 'failed', 0, str(e)
            finally:
                with active_files_lock:
                    active_files.pop(position, None)
        job_args = [
            (key, result, download_path, i % max_workers) 
            for i, (key, result, download_path) in enumerate(jobs)
        ]

        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures  = {executor.submit(_download_job, args): args for args in job_args}

        completed_count = 0
        try:
            for future in as_completed(futures):
                file_id, status, value, error = future.result()
                completed_count += 1
                pct = int(completed_count / total_jobs * 100) if total_jobs else 100

                if status == 'success':
                    print(f"  {Fore.GREEN}✔ {file_id} ({value:.1f} MB/s)")
                    success_count += 1
                    if on_progress:
                        on_progress(f"[{completed_count}/{total_jobs}] ✔ {file_id}", pct)
                elif status == 'skipped':
                    print(f"  {Fore.YELLOW}⏭ {file_id} ({value:.1f} MB, already exists)")
                    success_count += 1
                    if on_progress:
                        on_progress(f"[{completed_count}/{total_jobs}] ⏭ {file_id} (exists)", pct)
                elif status == 'cancelled':
                    pass  # silently skip cancelled jobs
                else:
                    print(f"  {Fore.RED}✘ {file_id} — {error}")
                    failure_count += 1
                    failed_files.append(file_id)
                    if on_progress:
                        on_progress(f"[{completed_count}/{total_jobs}] ✘ {file_id}", pct)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}⚠ Download interrupted by user. Cancelling pending jobs...")
            stop_event.set()
            # Cancel all pending futures that haven't started yet
            for future in futures:
                future.cancel()

            # Shut down without waiting for running threads to finish
            executor.shutdown(wait=False, cancel_futures=True)

            # Clean up any partial files being actively written
            with active_files_lock:
                for position, file_path in active_files.items():
                    if file_path.exists():
                        print(f"  {Fore.RED}Removing partial file: {file_path.name}")
                        file_path.unlink()

            print(f"{Fore.YELLOW}Download cancelled. "
                    f"{success_count} scenes completed before interrupt.")
            return

        else:
            executor.shutdown(wait=True)

        # Final summary
        print("\n" + "─" * 60)
        print(f"Download complete: {Fore.GREEN}{success_count}/{total_jobs} succeeded{Fore.RESET}", end="")
        if failure_count:
            print(f", {Fore.RED}{failure_count}/{total_jobs} failed{Fore.RESET}")
            print(f"\nFailed files:")
            for f in failed_files:
                print(f"  {Fore.RED}- {f}")
        print(f"\nFiles saved to: {self.download_dir}")