# -*- coding: utf-8 -*-
import getpass
import os
import threading
import time
from dataclasses import asdict
from dateutil.parser import isoparse
from collections import defaultdict
from pathlib import Path


import asf_search as asf
import contextily as ctx
import dem_stitcher
import matplotlib
import matplotlib.pyplot as plt
import rasterio as rio
from asf_search.exceptions import ASFAuthenticationError
from colorama import Fore
from pyproj import Transformer
from shapely import wkt, plotting
from shapely.ops import transform
from shapely.geometry import shape
from tqdm import tqdm

from insarhub._version import __version__
from insarhub.core.base import BaseDownloader
from insarhub.config import ASF_Base_Config
from insarhub.config.paths import StackPaths
from insarhub.utils.tool import _to_wkt

# OpenStreetMap's tile servers are volunteer-run and require a valid,
# identifying User-Agent (their tile usage policy); a bare request with
# the default urllib UA is rejected with "Access blocked". Identify the app.
_OSM_TILE_HEADERS = {"User-Agent": f"InSARHub/{__version__} (https://github.com/anomalyco/InSARHub)"}

def _parse_scene_filter(scenes) -> set[str] | None:
    """Return a set of scene name strings, or None meaning 'no filter'.

    Accepts:
    - None                              → no filter
    - set / list of scene name strings  → use as-is
    - list[[ref, sec], ...]             → single-stack select_pairs output
    - dict{(path,frame): [[ref,sec]]}   → multi-stack select_pairs output
    """
    if scenes is None:
        return None
    if isinstance(scenes, dict):
        # multi-stack pairs dict
        return {str(s) for v in scenes.values() for pair in v for s in pair}
    if isinstance(scenes, (set, frozenset)):
        return set(scenes)
    if isinstance(scenes, (list, tuple)):
        if not scenes:
            return set()
        first = scenes[0]
        if isinstance(first, str):
            # plain list of scene names
            return set(scenes)
        if isinstance(first, (list, tuple)):
            # single-stack pairs list
            return {str(s) for pair in scenes for s in pair}
    return set(str(s) for s in scenes)


def _product_byte_size(props: dict) -> int | None:
    """Byte size of the main product file (the one ``download()`` fetches).

    S1/ALOS expose ``bytes`` as an int. NISAR exposes a dict mapping every file
    in the granule to ``{'bytes': N, 'format': ...}``; return the entry for the
    file we actually download (``fileName`` == the ``url`` basename), falling
    back to the sum of all entries.
    """
    raw = props.get('bytes')
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, dict):
        filename = props.get('fileName')
        if filename:
            entry = raw.get(filename)
            if isinstance(entry, dict) and isinstance(entry.get('bytes'), (int, float)):
                return int(entry['bytes'])
        total = 0
        for v in raw.values():
            if isinstance(v, dict) and isinstance(v.get('bytes'), (int, float)):
                total += int(v['bytes'])
        return total or None
    return None


def _end_of_day(value: str) -> str:
    """Normalize a bare 'YYYY-MM-DD' end-date string to the end of that day.

    Both the GUI (an HTML <input type="date">) and typical CLI usage pass a
    date-only string for `end`. Handed to asf_search as-is, that gets parsed
    as midnight (00:00:00) of that day, which silently excludes every scene
    acquired later that same day — a scene dated exactly on the requested
    end date would be dropped even though the user meant "through this day".
    Strings that already carry a time component (contain 'T' or ':') are
    left untouched.
    """
    if not value or 'T' in value or ':' in value:
        return value
    return f"{value}T23:59:59"


class ASF_Base_Downloader(BaseDownloader):
    """
    Simplify searching and downloading satellite data using ASF Search API.
    """
    description = "Generic ASF Search API downloader. Supports Sentinel-1, ALOS, NISAR, and more."
    default_config = ASF_Base_Config

    # Config fields that are NOT asf.search() keywords. search() builds its query
    # from asdict(config), so any subclass field that configures post-search
    # behaviour instead of the query itself must be listed here -- otherwise it
    # is forwarded to asf.search() as an unknown kwarg, the call raises, and the
    # retry loop below burns ~17 minutes of exponential backoff before surfacing
    # it. Subclasses extend this set rather than overriding search().
    _NON_SEARCH_FIELDS: frozenset = frozenset(
        {"workdir", "name", "bbox", "granule_names", "ssl_verify", "max_workers"})
    _DATASET_GROUP_KEYS = {
        'SENTINEL-1': ('pathNumber', 'frameNumber'),
        'ALOS':       ('pathNumber', 'frameNumber'),
        'NISAR':      ('pathNumber', 'frameNumber'),  # NISAR carries frameNumber, not frameID
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
            'relativeOrbit': 'pathNumber',   # NISAR exposes track as pathNumber, like S1
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
                    self._session.verify = self.config.ssl_verify
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
            self._session.verify = self.config.ssl_verify
       
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
                    return (props.get('pathNumber'), props.get('frameNumber'))
        # last resort — group everything under the platform name
        return (props.get('pathNumber'), props.get('frameNumber'))
    
    # Platforms whose user-facing frame is the ASF frame (CMR FRAME_NUMBER == the
    # 'frameNumber' property we group by), rather than the ESA frame. See
    # _uses_asf_frame() / the asf_search 13.0.0 compatibility note in search().
    _ASF_FRAME_TOKENS = ('SENTINEL-1', 'SENTINEL1', 'ALOS', 'NISAR', 'SEASAT')

    def _uses_asf_frame(self) -> bool:
        """True when this dataset/platform numbers frames by the ASF frame.

        For Sentinel-1 / ALOS / NISAR / SEASAT the frame a user specifies is the ASF
        frame (the ``frameNumber`` property InSARHub groups by), which asf_search
        queries via ``asfFrame`` (CMR ``FRAME_NUMBER``) -- not ``frame`` (CMR
        ``CENTER_ESA_FRAME``). See :meth:`_apply_asf_frame_compat`.
        """
        vals: list = []
        for attr in ('dataset', 'platform'):
            v = getattr(self.config, attr, None)
            if v is None:
                continue
            vals.extend(v if isinstance(v, (list, tuple)) else [v])
        blob = ' '.join(str(x).upper() for x in vals)
        return any(tok in blob for tok in self._ASF_FRAME_TOKENS)

    def _apply_asf_frame_compat(self, search_opts: dict) -> dict:
        """Route ``frame`` -> ``asfFrame`` for ASF-frame platforms (asf_search 13.0.0).

        For Sentinel-1 / ALOS / NISAR the frame a user gives is the ASF frame (== the
        ``frameNumber`` InSARHub groups by). asf_search maps ``frame`` to CMR
        ``CENTER_ESA_FRAME`` and only rewrote it to ``FRAME_NUMBER`` for these platforms
        via ``should_use_asf_frame()``. asf_search **13.0.0** broke that check for a
        generic ``platform=SENTINEL-1`` query: it now tests for a ``shortName[]`` CMR key,
        but the query emits ``shortName`` (no brackets), and its ``platform[]`` fallback
        only lists the per-satellite names (``SENTINEL-1A/-1B/-1C/-1D``), not generic
        ``SENTINEL-1`` -- so neither branch matches, the ``CENTER_ESA_FRAME`` ->
        ``FRAME_NUMBER`` rewrite never fires, and ``frame=`` silently matches nothing.
        ``asfFrame`` maps straight to ``FRAME_NUMBER`` (bypassing that broken check) and
        works on both 12.x and 13.x, so route the frame filter there. We keep this
        workaround inside InSARHub rather than depend on an upstream asf_search fix.
        Mutates and returns ``search_opts``.
        """
        if search_opts.get('frame') is not None and self._uses_asf_frame():
            if search_opts.get('asfFrame') is None:
                search_opts['asfFrame'] = search_opts.pop('frame')
                print(f"{Fore.YELLOW}Note: querying ASF frame via 'asfFrame' for "
                      f"asf_search {getattr(asf, '__version__', '?')} compatibility "
                      f"('frame' targets the ESA frame and no longer matches for this "
                      f"platform).{Fore.RESET}")
            else:
                # Both set: asfFrame is authoritative for these platforms; drop the
                # ambiguous ESA-frame filter so it can't zero out the query.
                search_opts.pop('frame')
        return search_opts

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
                       if v is not None and k not in self._NON_SEARCH_FIELDS}
        if 'end' in search_opts and isinstance(search_opts['end'], str):
            search_opts['end'] = _end_of_day(search_opts['end'])

        search_opts = self._apply_asf_frame_compat(search_opts)

        if os.environ.get("INSARHUB_DEBUG_SEARCH"):
            print(f"[debug] asf.search opts: {search_opts}")

        for attempt in range(1, 11):
            try:
                self.results = asf.search(**search_opts)
                break
            except Exception as e:
                print(f"{Fore.RED}Search failed: {e}")
                if os.environ.get("INSARHUB_DEBUG_SEARCH"):
                    import traceback; traceback.print_exc()
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
        cmap = matplotlib.colormaps['hsv'].resampled(N+1)

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
        
        ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik, headers=_OSM_TILE_HEADERS)

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

        start_dt = isoparse(start).replace(tzinfo=None)             if start else None
        end_dt   = isoparse(_end_of_day(end)).replace(tzinfo=None)  if end   else None
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
        _dem_is_stack = (output_dir / "insarhub_config.json").exists()

        for key, results in self.active_results.items():
            _dem_sub = Path() if _dem_is_stack else Path(f'p{key[0]}_f{key[1]}')
            download_path = output_dir.joinpath('dem', _dem_sub)
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
        dt_targets: tuple        = None,
        dt_tol: int              = None,
        dt_max: int              = None,
        pb_max: float            = None,
        min_degree: int          = None,
        max_degree: int          = None,
        force_connect: bool      = None,
        max_workers: int         = None,
        avoid_low_quality_days: bool = None,
        snow_threshold: float    = None,
        precip_mm_threshold: float = None,
        aoi_wkt: str | None = None,
        merge: bool = False,
        burst: bool = False,
        safe_dir: str | None = None,
        eof_dir: str | None = None,
        poeorb_cache: str | None = None,
        quality_check: bool = True,
        plot_network: bool = True,
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
            avoid_low_quality_days (bool, optional): Skip scenes with heavy snow or rain. Defaults to True.
            snow_threshold (float, optional): MODIS snow fraction threshold to exclude a scene. Defaults to 0.5.
            precip_mm_threshold (float, optional): 3-day precipitation threshold in mm to exclude a scene. Defaults to 25.0.
            aoi_wkt (str, optional): AOI geometry in WKT for quality scoring. Defaults to search AOI.
            merge (bool, optional): When True, stacks sharing the same relative
                orbit (path) are combined into one pairing network before
                temporal/baseline selection — matching how ISCE2's stackSentinel
                treats multiple frames of one track/pass as a single continuous
                acquisition. Stacks on different paths are never combined
                (cross-track pairs have no physical baseline). Use together with
                ``download(merge=True)``, which puts all scenes in one
                ``merged/slc/`` directory. Defaults to False.
            burst (bool, optional): Select pairs for an SLC-BURST stack. Nodes
                become acquisition dates and baselines are computed from the
                bursts' own startTime/orbits — no parent-SLC lookup. Defaults
                to False.
            safe_dir (str, optional): Burst mode: directory of assembled
                ``.SAFE`` dirs whose annotation orbits supply bperp (offline).
            eof_dir (str, optional): Burst mode: directory of precise-orbit
                ``.EOF`` files used for bperp (offline).
            poeorb_cache (str, optional): Burst mode: directory for POEORB
                downloads keyed by date + mission (online fallback).
            quality_check (bool, optional): After pairing, write the stack
                file(s), seed the weather/snow cache, and precompute the
                PairQualityDB coherence scores for all possible pairs (the
                slow, network-heavy step). ``False`` skips scoring. Defaults
                to True.
            plot_network (bool, optional): Save ``network_*.png`` with the
                pair network, coloured by quality when ``quality_check`` is
                True (falls back to temporal-baseline colouring otherwise).
                Defaults to True.

        Returns:
            tuple: ``(pairs, baselines, scene_bperp, prefetch_cache,
            quality_scores, quality_factors)``
                - pairs: dict keyed by (path, frame) for multi-stack — or
                  (path, "merged") per distinct path when merge=True — or a
                  flat list for a single stack.
                - baselines: temporal baselines
                - scene_bperp: perpendicular baselines per scene
                - prefetch_cache: coherence/weather cache dict for downstream use
                - quality_scores: ``{pair_key: score}`` (list case) or
                  ``{(path, frame): {pair_key: score}}`` (dict case); ``None``
                  when ``quality_check`` is False or scoring failed.
                - quality_factors: factor breakdown, same keying as scores.
        """
        # None → pull from the single source of truth
        from insarhub.utils.defaults import SELECT_PAIRS_DEFAULTS as _SP
        if dt_targets             is None: dt_targets             = _SP["dt_targets"]
        if dt_tol                 is None: dt_tol                 = _SP["dt_tol"]
        if dt_max                 is None: dt_max                 = _SP["dt_max"]
        if pb_max                 is None: pb_max                 = _SP["pb_max"]
        if min_degree             is None: min_degree             = _SP["min_degree"]
        if max_degree             is None: max_degree             = _SP["max_degree"]
        if force_connect          is None: force_connect          = _SP["force_connect"]
        if max_workers            is None: max_workers            = _SP["max_workers"]
        if avoid_low_quality_days is None: avoid_low_quality_days = _SP["avoid_low_quality_days"]
        if snow_threshold         is None: snow_threshold         = _SP["snow_threshold"]
        if precip_mm_threshold    is None: precip_mm_threshold    = _SP["precip_mm_threshold"]
        from insarhub.utils.tool import select_pairs as _select_pairs

        if not hasattr(self, 'results'):
            raise ValueError("No search results found. Please run search() first.")

        search_input = self.active_results
        if merge and isinstance(search_input, dict) and len(search_input) > 1:
            # Group stacks by relative orbit (path) — frames of the same
            # path/pass overlap and can be validly combined; different paths
            # never share a baseline and must never be merged together.
            paths = {path for (path, _frame) in search_input.keys()}
            if len(paths) > 1:
                raise ValueError(
                    f"merge=True requires all stacks to share one relative orbit "
                    f"(path), got {sorted(paths)}. Different tracks have unrelated "
                    f"viewing geometry and cannot be interferometrically paired — "
                    f"narrow the search to a single path before merging."
                )
            path = next(iter(paths))
            frames = [frame for (_path, frame) in search_input.keys()]
            merged_prods = [p for prods in search_input.values() for p in prods]
            tag = StackPaths.merge_tag(frames)
            search_input = {(path, tag): merged_prods}
            print(f"{Fore.CYAN}merge=True: combined frame(s) {sorted(frames)} "
                  f"of path {path} ({len(merged_prods)} scenes) into one stack "
                  f"({tag}).\n")

        _aoi_wkt = aoi_wkt or getattr(self.config, "intersectsWith", None)
        _sp_result = _select_pairs(
            search_input,
            dt_targets=dt_targets,
            dt_tol=dt_tol,
            dt_max=dt_max,
            pb_max=pb_max,
            min_degree=min_degree,
            max_degree=max_degree,
            force_connect=force_connect,
            max_workers=max_workers,
            avoid_low_quality_days=avoid_low_quality_days,
            snow_threshold=snow_threshold,
            precip_mm_threshold=precip_mm_threshold,
            aoi_wkt=_aoi_wkt,
            burst=burst,
            safe_dir=safe_dir,
            eof_dir=eof_dir,
            poeorb_cache=poeorb_cache,
        )
        pairs      = _sp_result[0]
        baselines  = _sp_result[1]
        scene_bperp: dict = _sp_result[2] if len(_sp_result) > 2 else {}
        prefetch:   dict  = _sp_result[3] if len(_sp_result) > 3 else {}

        if not (quality_check or plot_network):
            return pairs, baselines, scene_bperp, prefetch, None, None

        # ── Finalize: write stack files, score pairs, plot network ────────
        from dataclasses import asdict
        from insarhub.utils.config_io import write_insarhub_config
        from insarhub.utils.stack_io import finalize_stack
        from insarhub.utils.tool import group_scenes_by_stack, write_workflow_marker

        scenes_by_stack = group_scenes_by_stack(self.active_results, merge=merge)
        workdir = Path(self.config.workdir).expanduser()
        workdir.mkdir(parents=True, exist_ok=True)
        _sp = StackPaths(workdir)
        _dl_is_stack = (workdir / "insarhub_config.json").exists()

        quality_scores: dict | None
        quality_factors: dict | None
        if isinstance(pairs, dict):
            quality_scores = {}
            quality_factors = {}
            for (path, frame), group_pairs in pairs.items():
                is_merged = StackPaths.is_merge_key(frame)
                label = f"P{path} ({frame})" if is_merged else f"P{path}/F{frame}"
                tag = _sp.dir_for(path, frame).name
                subdir = workdir if _dl_is_stack else workdir / tag
                subdir.mkdir(parents=True, exist_ok=True)
                write_workflow_marker(subdir, downloader=type(self).name)
                cfg = {k: v for k, v in asdict(self.config).items() if k != "workdir"}
                cfg["relativeOrbit"] = path
                if not is_merged:
                    cfg["frame"] = frame
                write_insarhub_config(subdir, {"downloader": {"type": type(self).name, "config": cfg}})
                sp = scene_bperp.get((path, frame)) or {}
                stack_scenes = scenes_by_stack.get((path, frame), [])
                stack_path = subdir / _sp.stack_file_for(path, frame).name
                qs, qf = finalize_stack(
                    subdir, stack_path, group_pairs, sp, stack_scenes,
                    prefetch.get((path, frame), {}),
                    key=(path, frame),
                    baselines=baselines[(path, frame)],
                    title=f"Interferogram Network — {label}",
                    save_path=subdir / f"network_{tag}.png",
                    quality_check=quality_check,
                    plot_network=plot_network,
                )
                if qs is not None:
                    quality_scores[(path, frame)] = qs
                    quality_factors[(path, frame)] = qf
        else:
            sp = scene_bperp if isinstance(scene_bperp, dict) else {}
            stack_scenes = scenes_by_stack.get((0, 0), [])
            stack_path = workdir / _sp.stack_file(0, 0).name
            quality_scores, quality_factors = finalize_stack(
                workdir, stack_path, pairs, sp, stack_scenes, prefetch,
                key=(0, 0),
                baselines=baselines,
                title="Interferogram Network",
                save_path=workdir / "network.png",
                quality_check=quality_check,
                plot_network=plot_network,
            )

        return pairs, baselines, scene_bperp, prefetch, quality_scores, quality_factors

    def _mark_stack_dir(self, stack_dir, extra_cfg: dict | None = None) -> None:
        """Write the two files that make a directory a recognised stack.

        ``insarhub_config.json`` + the workflow marker are what the GUI, the CLI
        and every processor use to identify a stack folder and recover which
        downloader produced it. Both ``download()`` implementations need this,
        and S1_Burst cannot reuse the base's copy because it overrides
        ``download()`` wholesale (burst2safe owns the transfer), so it lives
        here rather than being written out twice.
        """
        from dataclasses import asdict as _asdict
        from pathlib import Path as _Path

        from insarhub.utils.config_io import write_insarhub_config as _wic
        from insarhub.utils.tool import write_workflow_marker

        stack_dir = _Path(stack_dir)
        try:
            stack_dir.mkdir(parents=True, exist_ok=True)
            write_workflow_marker(stack_dir, downloader=type(self).name)
            cfg = {k: v for k, v in _asdict(self.config).items() if k != "workdir"}
            cfg.update(extra_cfg or {})
            _wic(stack_dir, {"downloader": {"type": type(self).name, "config": cfg}})
        except Exception as exc:                                    # noqa: BLE001
            logger.error("%s: could not write stack config to %s: %s",
                         type(self).name, stack_dir, exc)

    def download(self, save_path: str | None = None, max_workers: int = None,
                 stop_event=None, on_progress=None,
                 scenes=None, merge: bool = False):
        """Download search results to the specified output directory.

        Args:
            save_path (str, optional): Download path. Defaults to config.workdir.
            max_workers (int, optional): Concurrent downloads. When None, falls
                back to ``config.max_workers`` (the GUI's per-downloader
                "Download" setting), then to 3. Resolving it here rather than in
                each caller means the config field takes effect from every entry
                point; previously only the S1_Burst routes passed it explicitly,
                so a GUI-set value was silently ignored for S1_SLC.
            scenes: Restrict download to a subset of scenes. Accepts any of:
                - ``list | set`` of scene name strings
                - The direct output of ``select_pairs()`` — either a
                  ``list[[ref, sec], ...]`` (single-stack) or a
                  ``dict{(path, frame): [[ref, sec], ...]}`` (multi-stack).
                  Unique scene names are extracted automatically.
                  When ``None`` (default) all search results are downloaded.
            merge (bool): When True, all stacks are downloaded into a single
                ``merged/slc/`` subdirectory instead of per-stack ``p{path}_f{frame}/``
                subdirs. Useful when combining multiple overlapping stacks for ISCE/MintPy.

        Raises:
            ValueError: If no search results are available.
        """
        from insarhub.utils.defaults import DOWNLOAD_DEFAULTS as _DL
        if max_workers is None:
            max_workers = getattr(self.config, "max_workers", None) or _DL["max_workers"]
        max_workers = max(1, int(max_workers))
        import json as _json
        from concurrent.futures import ThreadPoolExecutor, as_completed
        output_dir = Path(save_path).expanduser().resolve() if save_path else self.config.workdir
        output_dir.mkdir(exist_ok=True, parents=True)

        self.download_dir = output_dir

        if not hasattr(self, 'results'):
            raise ValueError(f"{Fore.RED}No search results found. Please run search() first.")

        if stop_event is None:
            stop_event = threading.Event()

        scene_filter = _parse_scene_filter(scenes)

        # merge=True: all stacks of one path land in output_dir/p{path}_{tag}/slc/,
        # where tag encodes every constituent frame number — this must match
        # select_pairs(merge=True)'s own directory naming so the stack file
        # and the downloaded SLCs end up co-located. active_results is always
        # a dict[(path, frame), list[ASFProduct]] (see its docstring/contract).
        _sp = StackPaths(output_dir)
        # A single stack is never a merge: merge naming (p{path}_merged_f...) is
        # only meaningful for 2+ frames sharing one path. This mirrors
        # select_pairs(merge=...)'s own `len > 1` gate, so a single-frame
        # --merge download lands in p{path}_f{frame}/slc alongside the plain
        # stack file rather than in a mismatched p{path}_merged_f{frame}/.
        do_merge = (merge and isinstance(self.active_results, dict)
                    and len(self.active_results) > 1)
        if do_merge:
            paths = {path for (path, _frame) in self.active_results.keys()}
            if len(paths) > 1:
                raise ValueError(
                    f"merge=True requires all stacks to share one relative orbit "
                    f"(path), got {sorted(paths)}. Different tracks have unrelated "
                    f"viewing geometry and cannot be combined into one stack — "
                    f"narrow the search to a single path before merging."
                )
            path = next(iter(paths))
            frames = [frame for (_path, frame) in self.active_results.keys()]
            merged_dir = _sp.merge_dir(path, frames)
            merged_dir.mkdir(parents=True, exist_ok=True)
            self._mark_stack_dir(merged_dir)

        jobs = []
        stack_paths: dict = {}
        _dir_is_stack = (self.download_dir / "insarhub_config.json").exists()
        for key, results in self.active_results.items():
            if do_merge:
                stack_path    = merged_dir
                download_path = stack_path / "slc"
            elif _dir_is_stack:
                stack_path    = self.download_dir
                download_path = stack_path / "slc"
            else:
                stack_path    = StackPaths(self.download_dir).stack_dir(key[0], key[1])
                download_path = stack_path / "slc"
            download_path.mkdir(parents=True, exist_ok=True)
            stack_paths[key] = download_path
            if not merge:
                # key[1] is only a FRAME NUMBER for frame-based datasets. Burst
                # stacks key on fullBurstID, so writing it as 'frame' puts a
                # string into an int-range search field and the folder's next
                # search dies in asf_search's validator. Persist it only when it
                # really is an integer frame.
                extra = {'relativeOrbit': key[0]}
                try:
                    extra['frame'] = int(key[1])
                except (TypeError, ValueError):
                    pass
                self._mark_stack_dir(stack_path, extra)
            for result in results:
                if scene_filter is None or result.properties['sceneName'] in scene_filter:
                    jobs.append((key, result, download_path))
        
        total_jobs   = len(jobs)
        success_count = 0
        failure_count = 0
        failed_files  = []

        active_files: dict[int, Path] = {}
        active_files_lock = threading.Lock()

        total_scenes = sum(len(v) for v in self.active_results.values())
        filter_note  = (f" (filtered to {total_jobs} of {total_scenes})"
                        if scene_filter is not None and total_jobs != total_scenes else "")
        print(f"Downloading {total_jobs} scene(s) across "
              f"{len(self.active_results)} stack(s)"
              f"{filter_note} ({max_workers} concurrent)...\n")
        
        def _stream_download_interruptible(url, file_path, expected_bytes, 
                                        pbar_position, scene_name):
            """Stream download that checks stop_event on every chunk."""
            from tqdm import tqdm
            from asf_search.download.download import _try_get_response

            thread_session = asf.ASFSession()
            thread_session.cookies.update(self.session.cookies)
            thread_session.headers.update(self.session.headers)
            thread_session.verify = self.config.ssl_verify

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
            size_b    = _product_byte_size(result.properties)
            size_mb   = (size_b / (1024 * 1024)) if size_b else 0
            filename  = result.properties.get('fileName', f"{file_id}.zip")
            file_path = download_path / filename

            scene_name = result.properties.get('sceneName', file_id)

            if stop_event.is_set():
                return file_id, 'cancelled', 0, None

            # Skip if already complete
            if size_b and file_path.exists() and file_path.stat().st_size == size_b:
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
                    expected_bytes=size_b or 0,
                    pbar_position=position,
                    scene_name=scene_name,
                )

                actual_size = file_path.stat().st_size
                if size_b and actual_size != size_b:
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
        if len(stack_paths) == 1:
            print(f"\nFiles saved to: {next(iter(stack_paths.values()))}")
        else:
            print(f"\nFiles saved to:")
            for key, p in stack_paths.items():
                print(f"  path={key[0]} frame={key[1]}: {p}")