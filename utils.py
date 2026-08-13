"""
Utility functions for InSAR processing workflow.

Provides data I/O, array stitching, cross-multiplication, multilooking,
coherence estimation, phase filtering, unwrapping, and visualisation
for OPERA CSLC-based Sentinel-1 TOPS InSAR processing.

Authors: Zhenli Tang, Zhang Yunjun, July 2026.
Based on topsApp.py processing concepts.
"""


import datetime
import gc
import glob
import json
import os
import re
import subprocess
import sys
import time
import warnings
import zipfile
from collections import Counter
from multiprocessing import Pool
from netrc import netrc
from pathlib import Path

import h5py
import numpy as np
import requests
import yaml
from lxml import etree
from matplotlib import pyplot as plt, ticker, colors
from osgeo import gdal, osr
from pyproj import Transformer
from scipy import ndimage
from skimage.transform import resize
from tqdm import tqdm

import asf_search
import isce3
import s1reader
import snaphu

# Ensure ! commands find the conda environment's executables
os.environ['PATH'] = os.pathsep.join([os.path.dirname(sys.executable), os.environ.get('PATH', '')])

for _var, _sub in [('PROJ_DATA', 'share/proj'), ('GDAL_DATA', 'share/gdal')]:
    if _var not in os.environ:
        _path = os.path.join(sys.prefix, _sub)
        if os.path.isdir(_path):
            os.environ[_var] = _path

# ---------------------------------------------------------------------------
# ===========================================================================
# ===========================================================================
# 1. Constants
# ===========================================================================

# ---------------------------------------------------------------------------
# Sentinel-1 IW constants
# ---------------------------------------------------------------------------
# Pixel conversion factors (from COMPASS correction_luts defaults)
RANGE_SPACING_PX    = 120    # metres  per pixel  (range  direction)
AZIMUTH_SPACING_PX  = 0.028  # seconds per pixel  (azimuth direction)


_DATASET_META = {
    # ---- Physical (geophysical effects → pixel offset) ----
    'los_ionospheric_delay':         {'group': 'physical', 'unit': 'm', 'grid': 'lut',
                                      'desc': 'Ionospheric path delay (LOS)'},
    'los_static_tropospheric_delay': {'group': 'physical', 'unit': 'm', 'grid': 'lut',
                                      'desc': 'Static tropospheric path delay (LOS)'},
    'los_solid_earth_tides':         {'group': 'physical', 'unit': 'm', 'grid': 'lut',
                                       'desc': 'Solid Earth tide LOS displacement'},
    'azimuth_solid_earth_tides':     {'group': 'physical', 'unit': 's', 'grid': 'lut',
                                       'desc': 'Solid Earth tide azimuth displacement'},

    # ---- Focus (geometry / instrument → pixel offset) ----
    'geometry_steering_doppler':  {'group': 'focus', 'unit': 'm', 'grid': 'lut',
                                    'desc': 'Doppler steering range shift'},
    'bistatic_delay':             {'group': 'focus', 'unit': 's', 'grid': 'lut',
                                    'desc': 'Bistatic-to-monostatic azimuth offset'},
    'azimuth_fm_rate_mismatch':   {'group': 'focus', 'unit': 's', 'grid': 'lut',
                                    'desc': 'TOPS azimuth FM rate mismatch'},
    'slant_range':                {'group': 'focus', 'unit': 'm', 'grid': 'lut',
                                    'desc': 'Slant-range LUT', 'broadcast': 'azimuth'},
    'zero_doppler_time':          {'group': 'focus', 'unit': 's', 'grid': 'lut',
                                    'desc': 'Zero-Doppler time LUT', 'broadcast': 'range'},
}



__all__ = [
    "clear_large_arrays",
    "load_orbit_from_h5", "compute_isce3_incidence_angle",
    "extract_burst_slc",
    "compute_static_troposphere_delay",
    "read_cslc_array", "align_cslc_pair", "get_cslc_extent",
    "compute_union_grid", "blit_into_stitched",
    "stitch_arrays", "multilook_ifg",
    "stitch_bursts",
    "goldstein_filter",
    "estimate_phsig_correlation",
    "save_tiff",
    "write_geo_runconfig", "run_s1_cslc_parallel",
    "write_static_layers_config", "generate_static_layers",
    "find_burst_input_files", "find_burst_ids",
    "compute_los_angles", "multilook_nearest", "stitch_burst_los_angles",
    "read_aux_dataset", "compute_static_troposphere_correction",
    "plot_timing",
    "load_water_mask", "download_nasadem_water_mask",
    "extent_utm", "extent_latlon", "extent_pixel",
    "set_ax_utm", "set_ax_pixel",
    "plot_data", "plot_phase", "plot_amplitude", "plot_coherence",
    "plot_los", "plot_phase_over_hillshade", "show_and_close",
    "plot_coregistration",
    "plot_phase_triple", "plot_unwrap_results",
    "download_opera_static_layers",
    "get_file_geo_metadata", "plot_pair",
    "crop_slc_single", "generate_ifgram_pairs",
    "form_single_ifgram",
    "ifgram_and_coherence",
    "stitch_ifgrams",
    "multilook_tif",
    "filter_tif",
    "generate_phsig_coh_tif",
    "unwrap_single_ifgram",
    "compute_baselines_for_bursts", "merge_baselines",
]

# ===========================================================================
# 2. Memory Management
# ===========================================================================

def clear_large_arrays():
    """Delete known large arrays from the caller's global scope + garbage collect.

    Covers all sections of the notebook: 4.2.3 single-burst inspection,
    4.3 corrections, 5.1 demo, 5.2 per-burst stitching, 5.3 multilook,
    5.4 phsig, 5.5 unwrap, 6 save, 7 ionosphere.
    """
    _KNOWN = [
        # ---- 4.2.3 single-burst inspection ----
        'rdr_slc', 'rdr_amp', 'rdr_amp_db',
        'geo_slc', 'geo_amp', 'geo_amp_db', 'geo_phase',
        # ---- 4.3 correction arrays ----
        'tide_los', 'tide_az', 'iono',
        'wet_tropo', 'dry_tropo',
        'slant_range_arr', 'azt_arr', 'fm_mismatch', 'az_carrier',
        'inc_deg', 'hgt_arr', 'tropo_disp', 'static_tropo',
        # ---- 5.1 demo interferogram ----
        'ifg', 'coh', 'ifg_sum',
        'ref_pow', 'sec_pow',
        'ref_sum', 'sec_sum',
        # ---- 5.2 per-burst processing ----
        'extents_list', 'out_arr',
        'ifg_stitched', 'coh_stitched',
        'ifg_list', 'coh_list',
        # ---- 5.3 multilook + filter ----
        'ifg_ml', 'ifg_filt', 'ph_ml', 'ph_filt', 'nodata',
        # ---- 5.4 phase-sigma ----
        'phsig',
        # ---- 5.5 unwrap ----
        'ph', 'unw', 'conncomp', 'water_mask',
        'unw_m', 'wrapped',
        'dem', 'hillshade',
        'dx_grad', 'dy_grad', 'slope', 'aspect',
        # ---- 6 / 7 LOS arrays ----
        'inc_arr', 'az_arr',
        # ---- per-burst temporaries (in case loop was interrupted) ----
        'ref_arr', 'sec_arr',
        'ref_pow', 'sec_pow',
        'ifg_sum', 'ref_sum', 'sec_sum',
        'ifg_burst', 'coh_burst',
        's1_slc', 'stitched',
    ]
    frame = sys._getframe(1)
    deleted = [n for n in _KNOWN if n in frame.f_globals]
    for n in deleted:
        del frame.f_globals[n]
    gc.collect()
    if deleted:
        names = ', '.join(deleted[:10])
        if len(deleted) > 10:
            names += f' ... ({len(deleted) - 10} more)'
        print(f'Cleared {len(deleted)} array(s): {names}')

# ===========================================================================
# 3. Orbit & Geometry
# ===========================================================================

def load_orbit_from_h5(h5_path):
    """Load an ISCE3 Orbit from an OPERA CSLC HDF5 file.

    Parameters
    ----------
    h5_path : str or Path
        Path to the OPERA CSLC H5 file containing ``/metadata/orbit/``.

    Returns
    -------
    orbit : isce3.core.Orbit
    t0 : float
        Time offset (seconds) of the first state vector relative to the
        H5 orbit reference epoch.  Use to convert ``azt`` values to
        ISCE3 orbit-relative seconds.
    """
    with h5py.File(h5_path, 'r') as f:
        orb_grp = f['/metadata/orbit']
        pos_x = orb_grp['position_x'][:]
        pos_y = orb_grp['position_y'][:]
        pos_z = orb_grp['position_z'][:]
        vel_x = orb_grp['velocity_x'][:]
        vel_y = orb_grp['velocity_y'][:]
        vel_z = orb_grp['velocity_z'][:]
        times = orb_grp['time'][:]
        ref_epoch_str = orb_grp['reference_epoch'][()].decode()

    ref_epoch = isce3.core.DateTime(ref_epoch_str)
    statevecs = []
    for i in range(len(times)):
        pos = np.array([pos_x[i], pos_y[i], pos_z[i]])
        vel = np.array([vel_x[i], vel_y[i], vel_z[i]])
        sv = isce3.core.StateVector(
            ref_epoch + isce3.core.TimeDelta(times[i]), pos, vel)
        statevecs.append(sv)

    return isce3.core.Orbit(statevecs), times[0]

def compute_isce3_incidence_angle(h5_path):
    """Compute per-pixel incidence angle on the radar LUT grid using ISCE3.

    Uses ``isce3.geometry.look_inc_ang_from_slant_range()`` with the
    WGS-84 ellipsoid (h=0) — consistent with the physical model employed
    by COMPASS's ``Rdr2Geo``, but without terrain-height adjustment.

    Parameters
    ----------
    h5_path : str or Path
        Path to the OPERA CSLC H5 file.

    Returns
    -------
    inc_deg : np.ndarray (float64)
        2-D incidence angle in degrees, shape ``(n_az, n_rg)`` matching
        the ``timing_corrections`` LUT grid.
    """
    corr_grp = '/metadata/processing_information/timing_corrections'

    with h5py.File(h5_path, 'r') as f:
        sr = f[f'{corr_grp}/slant_range'][:]
        azt = f[f'{corr_grp}/zero_doppler_time'][:]

    orbit, t0 = load_orbit_from_h5(h5_path)

    # Convert azt to ISCE3 orbit-relative seconds
    azt_orbit = azt - t0
    dem_interp = isce3.geometry.DEMInterpolator(0.0)

    n_az, n_rg = len(azt), len(sr)
    inc_deg = np.zeros((n_az, n_rg), dtype=np.float64)
    for i in range(n_az):
        _, inc_row = isce3.geometry.look_inc_ang_from_slant_range(
            sr, orbit, az_time=azt_orbit[i], dem_interp=dem_interp)
        inc_deg[i, :] = np.rad2deg(inc_row)

    return inc_deg

# ===========================================================================
# 4. SAFE Burst I/O
# ===========================================================================

def get_date_list(safe_dir):
    """Search and return the list of dates available in the given SAFE file directory.
    """
    date_list = []

    # for files in compressed zip format
    for f in sorted(safe_dir.glob('S1[ABCDE]_IW_SLC__*.zip')):
        m = re.match(r'S1[ABCDE]_IW_SLC__1S.._(\d{8})T.*', f.name)
        if m:
            date_list.append(m.group(1))

    # for files in uncompressed/unzipped
    for d in sorted(safe_dir.glob('S1[ABCDE]_IW_SLC__*.SAFE')):
        m = re.match(r'S1[ABCDE]_IW_SLC__1S.._(\d{8})T.*', d.name)
        if m:
            date_list.append(m.group(1))

    date_list = sorted(list(set(date_list)))

    return date_list


def extract_burst_slc(safe_path, burst_id):
    """Extract a single burst's SLC from a Sentinel-1 SAFE measurement TIFF.

    Sentinel-1 SAFE products store multiple bursts concatenated along azimuth
    in a single TIFF.  This function parses the subswath annotation XML to
    find *burst_id*'s line range and reads only that burst.

    Parameters
    ----------
    safe_path : str or Path
        Path to the SAFE directory.
    burst_id : str
        Burst identifier, e.g. ``t124_264305_iw2`` or just ``264305``.
        If the full ``tRRR_BBBBBB_iwN`` form is given the numeric burst
        index is extracted automatically.

    Returns
    -------
    slc : np.ndarray (complex64)
        2-D complex SLC array ``[azimuth_lines, range_samples]``.
    """
    safe_path = Path(safe_path)

    # Find the measurement TIFF
    tiff_files = sorted(glob.glob(str(safe_path / 'measurement' / '*.tiff')))
    if not tiff_files:
        raise FileNotFoundError(
            f'No SLC TIFF found in {safe_path}/measurement/')

    # Parse numeric burst index from burst_id (e.g. 264305)
    burst_idx = int(str(burst_id).split('_')[1]) if '_' in str(burst_id) else int(burst_id)

    # Find the matching IW annotation file
    iw_num = str(burst_id).split('_')[-1][-1]  # 'iw2' → '2'
    ann_pattern = str(safe_path / 'annotation' / f'*-iw{iw_num}*slc*vv*.xml')
    
    # If burst_id does not contain iw info, try to infer from filenames
    candidates = sorted(glob.glob(ann_pattern))
    if not candidates:
        # Try without iw filter
        candidates = sorted(glob.glob(
            str(safe_path / 'annotation' / '*-slc-vv-*.xml')))
    
    ann_file = candidates[0]
    tree = etree.parse(ann_file)
    root = tree.getroot()

    _REL_ORBIT_OFFSET = {'S1A': 73, 'S1B': 27, 'S1C': 27, 'S1D': 27}
    mission_id = root.find('.//{*}missionId').text
    abs_orbit = int(root.find('.//{*}absoluteOrbitNumber').text)
    offset = _REL_ORBIT_OFFSET.get(mission_id, 73)
    rel_orbit = (abs_orbit - offset) % 175 + 1

    iw_name = root.find('.//{*}swath').text
    iw_num = iw_name[-1]

    lines_per_burst = int(root.find('.//{*}linesPerBurst').text)
    burst_list = root.find('.//{*}burstList')
    burst_id_str = str(burst_id)

    burst_index_in_list = None
    for bi, b_elem in enumerate(burst_list):
        b_id_elem = b_elem.find('{*}burstId')
        if b_id_elem is not None:
            if b_id_elem.text == str(burst_idx):
                burst_index_in_list = bi
                break
        else:
            azt_time = b_elem.find('.{*}azimuthTime')
            azt_anx = b_elem.find('.{*}azimuthAnxTime')
            if azt_time is None or azt_anx is None:
                continue
            computed = _compute_burst_id(
                azt_time.text,
                float(azt_anx.text),
                rel_orbit,
                iw_name.upper(),
            )
            if computed == burst_id_str:
                burst_index_in_list = bi
                break

    if burst_index_in_list is None:
        raise ValueError(
            f'Burst {burst_id} not found in annotation {ann_file}')

    line_start = burst_index_in_list * lines_per_burst

    ds = gdal.Open(tiff_files[0])
    slc = ds.GetRasterBand(1).ReadAsArray(0, line_start,
                                          ds.RasterXSize, lines_per_burst)
    ds = None

    return slc.astype(np.complex64)

def find_burst_input_files(ymd, burst_id, safe_base, orbit_dir, tec_dir):
    """Locate the SAFE, orbit (EOF) and TEC files for one acquisition date.

    When *burst_id* is provided and multiple SAFE files exist for the same
    date (multi-IW scenario), the correct SAFE is selected by matching the
    IW swath number from *burst_id* against the annotation filenames inside
    each SAFE directory.

    Parameters
    ----------
    ymd : str
        Acquisition date as `YYYYMMDD` or `YYYY_MM_DD`.
    safe_base : str or Path
        Directory containing the SAFE products.
    orbit_dir : str or Path
        Directory containing the orbit (EOF) files.
    tec_dir : str or Path
        Directory containing the IONEX TEC files.
    burst_id : str, optional
        Burst identifier (e.g. ``t124_264305_iw2``) used to disambiguate
        SAFE files when multiple exist for the same date.

    Returns
    -------
    safe_path : str or None
        Matching SAFE path (or None if not found).
    orbit_path : str or None
        Orbit file covering the date, preferring POEORB over RESORB.
    tec_path : str or None
        First available IONEX file (or None).
    """
    safe_base = Path(safe_base)
    orbit_dir = Path(orbit_dir)
    tec_dir = Path(tec_dir)

    ymd = ymd.replace('_', '')
    safe_hits = sorted(str(p) for p in safe_base.glob(f'S1*{ymd}*.SAFE'))
    safe_path = None

    if len(safe_hits) == 1:
        safe_path = safe_hits[0]
    elif burst_id is not None:
        iw_num = str(burst_id).split('_')[-1][-1]  # 'iw2' → '2'
        for safe_candidate in safe_hits:
            ann_pattern = str(Path(safe_candidate) / 'annotation' /
                              f'*iw{iw_num}*slc*vv*.xml')
            ann_files = glob.glob(ann_pattern)
            for ann_file in ann_files:
                try:
                    tree = etree.parse(ann_file)
                    root = tree.getroot()
                    burst_list = root.find('.//{*}burstList')
                    if burst_list is not None and len(burst_list) > 0:
                        safe_path = safe_candidate
                        break
                except Exception:
                    continue
            if safe_path is not None:
                break
    if safe_path is None and safe_hits:
        safe_path = safe_hits[0]

    # Prefer an orbit whose validity window covers the date (POEORB), else RESORB.
    orbit_path = None
    for eof in sorted(str(p) for p in orbit_dir.glob('*.EOF')):
        m = re.search(r'V(\d{8}T\d{6})_(\d{8}T\d{6})', eof)
        if m and m.group(1) <= ymd <= m.group(2):
            orbit_path = eof
            break
    if orbit_path is None:
        res_hits = sorted(str(p) for p in orbit_dir.glob(f'S1*RESORB*{ymd}*.EOF'))
        orbit_path = res_hits[0] if res_hits else None

    gim_hits = list(tec_dir.glob('*GIM.INX'))
    if gim_hits:
        tec_hits = sorted(str(p) for p in gim_hits)
    else:
        tec_hits = sorted(str(p) for p in tec_dir.glob('jplg*'))
    tec_path = tec_hits[0] if tec_hits else None

    return safe_path, orbit_path, tec_path

def _compute_burst_id(azimuth_time_str, azimuth_anx_time, rel_orbit, subswath):
    """Compute Sentinel-1 burst ID string using the s1reader library.

    Delegates to ``s1reader.s1_burst_id.S1BurstId.from_burst_params()``
    which implements ESA Sentinel-1 Level 1 Detailed Algorithm Definition
    §9 equations 9-89/9-91 with proper IW subswath timing offsets
    and equator-crossing handling.

    Parameters
    ----------
    azimuth_time_str : str
        ISO-8601 azimuth time from the SAFE annotation ``<azimuthTime>`` tag
        (e.g. ``2020-01-22T03:25:14.262507``).
    azimuth_anx_time : float
        Mid-burst time w.r.t. ascending node crossing (seconds), from
        ``<azimuthAnxTime>`` in the burst annotation XML.
    rel_orbit : int
        Relative orbit (track) number, 1–175.
    subswath : str
        Subswath name, e.g. ``'IW2'``.

    Returns
    -------
    str
        Full burst ID string, e.g. ``'t124_264304_iw2'``.
    """
    azimuth_time = datetime.datetime.fromisoformat(azimuth_time_str)
    ascending_node_dt = azimuth_time - datetime.timedelta(seconds=azimuth_anx_time)

    burst_id = s1reader.s1_burst_id.S1BurstId.from_burst_params(
        sensing_time=azimuth_time,
        ascending_node_dt=ascending_node_dt,
        start_track=rel_orbit,
        end_track=rel_orbit,
        subswath=subswath,
    )
    return str(burst_id)

def find_burst_ids(safe_base, orbit_dir=None, verbose=False):
    """Find burst IDs and acquisition dates from downloaded SAFE directories
    using ``s1reader.load_bursts()``.

    If *orbit_dir* is provided, the best POEORB/RESORB covering each date
    is used for ascending-node-time computation.  When *orbit_dir* is
    ``None`` (default), ``load_bursts()`` falls back to annotation time
    (which is slightly less accurate).

    Parameters
    ----------
    safe_base : str or Path
        Directory containing the downloaded SAFE products.
    orbit_dir : str or Path, optional
        Directory containing the orbit (EOF) files.

    Returns
    -------
    burst_id_list : list of str
        Deduplicated burst IDs (e.g. ``['t124_264305_iw2', ...]``).
    date_list : list of str
        Corresponding dates as ``YYYYMMDD`` for each burst ID.
        Length matches *burst_id_list*.
    """
    safe_base = Path(safe_base)
    orbit_dir = Path(orbit_dir) if orbit_dir else None

    safe_dirs = sorted(str(p) for p in safe_base.glob('S1*[0-9]*.SAFE'))
    if not safe_dirs:
        raise FileNotFoundError(f'No SAFE directories found under {safe_base}')

    # Helper: find orbit file covering date, preferring POEORB over RESORB
    def _find_orbit(ymd):
        if orbit_dir is None:
            return ""
        for eof in sorted(str(p) for p in orbit_dir.glob('*.EOF')):
            m = re.search(r'V(\d{8}T\d{6})_(\d{8}T\d{6})', eof)
            if m and m.group(1) <= ymd <= m.group(2):
                return eof
        res_hits = sorted(str(p) for p in orbit_dir.glob(f'S1*RESORB*{ymd}*.EOF'))
        return res_hits[0] if res_hits else ""

    date_burst_map = {}
    burst_date_map = {}

    for safe_path in safe_dirs:
        safe_p = Path(safe_path)
        m = re.search(r'(\d{8})', safe_p.name)
        if m is None:
            print(f'WARNING: cannot extract date from {safe_p.name}, skipping')
            continue
        ymd = m.group(1)

        orbit_path = _find_orbit(ymd)
        date_bursts = set()

        for iw in [1, 2, 3]:
            try:
                bursts = s1reader.load_bursts(safe_path, orbit_path, iw, pol='vv')
                for b in bursts:
                    bid = str(b.burst_id)
                    date_bursts.add(bid)
                    burst_date_map.setdefault(bid, []).append(ymd)
            except (ValueError, FileNotFoundError, OSError):
                continue

        if date_bursts:
            date_burst_map.setdefault(ymd, set()).update(date_bursts)

    if not date_burst_map:
        raise RuntimeError('No valid SAFE annotation XML files found')

    sorted_dates = sorted(date_burst_map.keys())
    all_bursts = set()
    for bset in date_burst_map.values():
        all_bursts.update(bset)

    for ymd in sorted_dates[1:]:
        this_bursts = date_burst_map[ymd]
        if this_bursts != all_bursts:
            only_in_first = all_bursts - this_bursts
            only_in_this = this_bursts - all_bursts
            msg = f'WARNING: burst IDs differ between {sorted_dates[0]} and {ymd}!'
            if only_in_first:
                msg += f'\n  Missing in {ymd}: {sorted(only_in_first)}'
            if only_in_this:
                msg += f'\n  Extra in {ymd}: {sorted(only_in_this)}'
            print(msg)

    burst_id_list = sorted(all_bursts)
    date_list = []
    for bid in burst_id_list:
        for ymd in sorted_dates:
            if bid in date_burst_map[ymd]:
                date_list.append(ymd)

    # re-order burst ID by IW (iw2, iw3), burst_index asc within each IW,
    # so that the later-on stitching follows the correct along-track laydown order.
    sorted(burst_id_list, key=lambda b: (b.split('_')[-1], int(b.split('_')[1])))
    if verbose:
        print('Found burst info:')
        print(f'Dates ({len(sorted_dates)}): {sorted_dates}')
        print(f'Burst ID ({len(burst_id_list)}): {burst_id_list}')

    return burst_id_list, date_list

# ===========================================================================
# 5. CSLC Data I/O (OPERA HDF5)
# ===========================================================================

def read_cslc_array(h5_path):
    """Read an OPERA CSLC HDF5 file into a numpy complex64 array.

    Parameters
    ----------
    h5_path : str or Path
        Path to the OPERA-format HDF5 CSLC file.

    Returns
    -------
    data_vv : np.ndarray (complex64) or None
        2-D complex SLC array ``[rows, cols]``, or ``None`` if
        ``/data/VV`` is missing from the file.
    geo_transform : tuple or None
        GDAL-style geotransform ``(x0, dx, 0, y0, 0, dy)``, or
        ``None`` if coordinate arrays are unavailable.
    epsg : int or None
        EPSG code of the UTM projection, or ``None`` if
        ``/data/projection`` is missing.
    proj_wkt : str or None
        Projection definition in WKT format, or ``None`` if EPSG
        is unavailable.
    """
    with h5py.File(h5_path, 'r') as f:
        data_vv = f['/data/VV'][:] if '/data/VV' in f else None
        x = f['/data/x_coordinates'][:] if '/data/x_coordinates' in f else None
        y = f['/data/y_coordinates'][:] if '/data/y_coordinates' in f else None
        epsg = int(f['/data/projection'][()]) if '/data/projection' in f else None

    if x is not None and y is not None and len(x) >= 2 and len(y) >= 2:
        dx = x[1] - x[0]
        dy = y[1] - y[0]
        geo_transform = (x[0], dx, 0, y[0], 0, dy)
    else:
        geo_transform = None

    if epsg is not None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(epsg)
        proj_wkt = srs.ExportToWkt()
    else:
        proj_wkt = None

    return data_vv, geo_transform, epsg, proj_wkt

def get_cslc_extent(h5_path):
    """Read coordinate vectors from an OPERA CSLC HDF5 file (cheap metadata).

    Parameters
    ----------
    h5_path : str or Path

    Returns
    -------
    x0, dx, y0, dy, nrows, ncols, epsg, proj_wkt
    """
    with h5py.File(h5_path, 'r') as f:
        x = f['/data/x_coordinates'][:]
        y = f['/data/y_coordinates'][:]
        epsg = int(f['/data/projection'][()])
    dx = x[1] - x[0]
    dy = y[1] - y[0]
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    proj_wkt = srs.ExportToWkt()
    return x[0], dx, y[0], dy, len(y), len(x), epsg, proj_wkt

# ===========================================================================
# 6. Stitching & Multilooking
# ===========================================================================

def align_cslc_pair(ref_arr, ref_gt, sec_arr, sec_gt):
    """Align sec array to ref grid by intersecting their geographic extents.

    OPERA CSLC arrays share the same posting (``dx``, ``dy``) and only
    differ in their starting offsets (``x0``, ``y0``).  This function
    computes the integer pixel shift between the two grids and slices
    both arrays to their common overlap, guaranteeing pixel-to-pixel
    geographic correspondence.

    Parameters
    ----------
    ref_arr, sec_arr : np.ndarray (complex64)
        Full-size reference and secondary CSLC arrays.
    ref_gt, sec_gt : tuple
        GDAL geotransforms ``(x0, dx, 0, y0, 0, dy)``.

    Returns
    -------
    ref_aligned, sec_aligned : np.ndarray (complex64)
        Arrays cropped to the common overlapping extent.
    common_gt : tuple
        Geotransform of the overlapping region.
    """
    x0_r, dx, _, y0_r, _, dy = ref_gt
    x0_s, _, _, y0_s, _, _ = sec_gt

    nr_r, nc_r = ref_arr.shape
    nr_s, nc_s = sec_arr.shape

    # Integer pixel offset from ref grid to sec grid
    off_c = int(round((x0_s - x0_r) / dx))
    off_r = int(round((y0_s - y0_r) / dy))

    # Compute overlap region
    if off_c >= 0:
        rc0, sc0 = off_c, 0
        nc = min(nc_r - off_c, nc_s)
    else:
        rc0, sc0 = 0, -off_c
        nc = min(nc_r, nc_s + off_c)

    if off_r >= 0:
        rr0, sr0 = off_r, 0
        nr = min(nr_r - off_r, nr_s)
    else:
        rr0, sr0 = 0, -off_r
        nr = min(nr_r, nr_s + off_r)

    ref_aligned = ref_arr[rr0:rr0 + nr, rc0:rc0 + nc]
    sec_aligned = sec_arr[sr0:sr0 + nr, sc0:sc0 + nc]

    common_gt = (x0_r + rc0 * dx, dx, 0, y0_r + rr0 * dy, 0, dy)
    return ref_aligned, sec_aligned, common_gt

def compute_union_grid(extents, bbox_wsen, epsg_utm):
    if not extents:
        raise ValueError('extents list is empty')

    # extents: (x0, dx, y0, dy, nrows, ncols, epsg, proj_wkt)
    _, dx, _, dy, _, _, _, proj_wkt = extents[0]

    # Union extent in UTM
    ulx = min(e[0] for e in extents)                        # west
    lrx = max(e[0] + e[5] * e[1] for e in extents)          # east  (e[5]=ncols)
    uly = max(e[2] for e in extents)                        # north
    lry = min(e[2] + e[3] * e[4] for e in extents)          # south (e[3]=dy<0, e[4]=nrows)

    # Clip to geographic bbox in UTM
    tf = Transformer.from_crs('EPSG:4326', f'EPSG:{epsg_utm}', always_xy=True)
    xs, ys = tf.transform(
        [bbox_wsen[0], bbox_wsen[2], bbox_wsen[2], bbox_wsen[0]],
        [bbox_wsen[1], bbox_wsen[1], bbox_wsen[3], bbox_wsen[3]],
    )
    bbox_xmin, bbox_ymin = min(xs), min(ys)
    bbox_xmax, bbox_ymax = max(xs), max(ys)

    ulx = max(ulx, bbox_xmin)
    lrx = min(lrx, bbox_xmax)
    uly = min(uly, bbox_ymax)       # uly is north, bbox_ymax is north
    lry = max(lry, bbox_ymin)       # lry is south, bbox_ymin is south

    out_cols = int((lrx - ulx) / abs(dx) + 0.5)             # lrx > ulx → positive
    out_rows = int(abs(uly - lry) / abs(dy) + 0.5)          # abs(uly-lry) > 0
    out_gt = (ulx, dx, 0, uly, 0, dy)

    return out_gt, out_rows, out_cols, proj_wkt

def blit_into_stitched(dst, dst_gt, src, src_gt, nodata_thresh=1e-6):
    """Copy valid source pixels into a pre-allocated destination array.

    Computes the integer pixel offset of *src* within *dst* using their
    geotransforms, then copies pixels where ``|src| > nodata_thresh``.
    Overlap regions are overwritten (last-wins).

    Parameters
    ----------
    dst : np.ndarray  Pre-allocated destination (stitched) array.
    dst_gt : tuple (x0, dx, 0, y0, 0, dy)
    src : np.ndarray  Source (per-burst) array.
    src_gt : tuple (x0, dx, 0, y0, 0, dy)
    nodata_thresh : float  Pixels with |src| <= nodata_thresh are skipped.
    """
    xoff = int(round((src_gt[0] - dst_gt[0]) / dst_gt[1]))
    yoff = int(round((src_gt[3] - dst_gt[3]) / dst_gt[5]))

    sh, sw = src.shape
    dh, dw = dst.shape

    src_r0 = max(0, -yoff)
    src_r1 = min(sh, dh - yoff)
    src_c0 = max(0, -xoff)
    src_c1 = min(sw, dw - xoff)
    dst_r0 = max(0, yoff)
    dst_r1 = min(dh, yoff + sh)
    dst_c0 = max(0, xoff)
    dst_c1 = min(dw, xoff + sw)

    h = min(src_r1 - src_r0, dst_r1 - dst_r0)
    w = min(src_c1 - src_c0, dst_c1 - dst_c0)
    if h <= 0 or w <= 0:
        return

    src_chunk = src[src_r0:src_r0 + h, src_c0:src_c0 + w]
    valid = np.isfinite(src_chunk) & (np.abs(src_chunk) > nodata_thresh)
    dst[dst_r0:dst_r0 + h, dst_c0:dst_c0 + w][valid] = src_chunk[valid]

def stitch_arrays(arrays_list, bbox_wsen, dx=5.0, dy=-10.0, epsg_utm=32605,
                  method='last'):
    """Stitch geocoded arrays via gdal_merge-style pixel-offset copy.

    Arrays with different geotransforms are filtered: only the group
    with the most common geotransform is stitched; the rest are skipped
    with a warning.

    Parameters
    ----------
    arrays_list : list of (arr, geotransform, proj_wkt) tuples
    bbox_wsen : tuple  ``(west, south, east, north)`` EPSG:4326.
    dx, dy : float  pixel sizes (metres). Ignored — derived from kept GT.
    epsg_utm : int  UTM EPSG code.
    method : {'first', 'last'}
        ``'last'`` — later sources overwrite earlier (default).
        ``'first'`` — earlier sources take precedence.

    Returns
    -------
    stitched : np.ndarray  ``[rows, cols]``
    out_gt : tuple  GDAL geotransform
    proj_wkt : str
    """

    if not arrays_list:
        raise ValueError("arrays_list is empty")

    # --- Filter: keep only the most common pixel-spacing group ---
    # Arrays with same (dx, dy) can be stitched (different x0/y0 is expected).
    # Arrays with different (dx, dy) are on incompatible grids and are dropped.
    spacing_to_indices = {}
    for i, (arr, gt, _) in enumerate(arrays_list):
        key = (round(gt[1], 6), round(gt[5], 6))
        spacing_to_indices.setdefault(key, []).append(i)

    if len(spacing_to_indices) > 1:
        best_key = max(spacing_to_indices, key=lambda k: len(spacing_to_indices[k]))
        best_count = len(spacing_to_indices[best_key])
        dropped = sum(len(v) for k, v in spacing_to_indices.items() if k != best_key)
        warnings.warn(
            f'{len(spacing_to_indices)} different pixel spacings found. '
            f'Keeping {best_count} arrays with dx,dy={best_key}, '
            f'dropping {dropped} array(s) with mismatched spacing.')
        arrays_list = [arrays_list[i] for i in spacing_to_indices[best_key]]

    sample, _, proj_wkt = arrays_list[0]
    is_complex = np.issubdtype(sample.dtype, np.complexfloating)

    # --- union extent: proper min/max for both dy signs ---
    pieces = []
    for arr, gt, _ in arrays_list:
        x0, px_dx, _, y0, _, py_dy = gt
        x1 = x0 + arr.shape[1] * px_dx
        y1 = y0 + arr.shape[0] * py_dy
        pieces.append({
            'arr': arr, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
            'x_min': min(x0, x1), 'x_max': max(x0, x1),
            'y_min': min(y0, y1), 'y_max': max(y0, y1),
            'dx': px_dx, 'dy': py_dy,
        })

    if not pieces:
        raise ValueError("No valid pieces")

    # Use dx/dy from first piece (all same after filtering)
    dx = pieces[0]['dx']
    dy = pieces[0]['dy']

    # Proper geographic extent (north=max_y, south=min_y, east=max_x, west=min_x)
    ulx = min(p['x_min'] for p in pieces)
    lrx = max(p['x_max'] for p in pieces)
    uly = max(p['y_max'] for p in pieces)
    lry = min(p['y_min'] for p in pieces)

    # Clip to bbox_wsen
    tf = Transformer.from_crs('EPSG:4326', f'EPSG:{epsg_utm}', always_xy=True)
    xs, ys = tf.transform(
        [bbox_wsen[0], bbox_wsen[2], bbox_wsen[2], bbox_wsen[0]],
        [bbox_wsen[1], bbox_wsen[1], bbox_wsen[3], bbox_wsen[3]],
    )
    bbox_xmin, bbox_ymin = min(xs), min(ys)
    bbox_xmax, bbox_ymax = max(xs), max(ys)

    ulx = max(ulx, bbox_xmin)
    lrx = min(lrx, bbox_xmax)
    uly = min(uly, bbox_ymax)
    lry = max(lry, bbox_ymin)

    # Output grid (gdal_merge style: int((extent / ps) + 0.5))
    out_cols = int((lrx - ulx) / abs(dx) + 0.5)
    out_rows = int((uly - lry) / abs(dy) + 0.5)
    out_dy = -abs(dy)
    out_dx = abs(dx)
    out_gt = (ulx, out_dx, 0, uly, 0, out_dy)

    stitched = np.zeros((out_rows, out_cols), dtype=sample.dtype)

    items = pieces
    if method == 'first':
        items = list(reversed(items))

    for p in items:
        arr = p['arr']
        src_x0, src_y0 = p['x0'], p['y0']

        xoff = int((src_x0 - ulx) / dx)
        yoff = int((src_y0 - uly) / dy)

        src_r0 = max(0, -yoff)
        src_r1 = min(arr.shape[0], out_rows - yoff)
        src_c0 = max(0, -xoff)
        src_c1 = min(arr.shape[1], out_cols - xoff)
        dst_r0 = max(0, yoff)
        dst_r1 = min(out_rows, yoff + arr.shape[0])
        dst_c0 = max(0, xoff)
        dst_c1 = min(out_cols, xoff + arr.shape[1])

        h = min(src_r1 - src_r0, dst_r1 - dst_r0)
        w = min(src_c1 - src_c0, dst_c1 - dst_c0)
        if h <= 0 or w <= 0:
            continue

        src = arr[src_r0:src_r0 + h, src_c0:src_c0 + w]
        if is_complex:
            valid = np.isfinite(src) & (np.abs(src) > 1e-6)
        else:
            valid = np.isfinite(src) & (np.abs(src) > 1e-6)

        if method == 'last':
            stitched[dst_r0:dst_r0 + h, dst_c0:dst_c0 + w][valid] = src[valid]
        else:
            dst = stitched[dst_r0:dst_r0 + h, dst_c0:dst_c0 + w]
            empty = ~np.isfinite(dst) | (np.abs(dst) < 1e-6) if is_complex else (dst == 0)
            write = valid & empty
            stitched[dst_r0:dst_r0 + h, dst_c0:dst_c0 + w][write] = src[write]

    return stitched, out_gt, proj_wkt

def stitch_bursts(file_path_list, bbox_wsen, epsg_utm=None,
                  verbose=False):
    """Stitch geocoded GeoTIFF files by pixel-offset copy in one pass.

    Opens each GeoTIFF one at a time, copies valid pixels into a
    pre-allocated union-grid array via :func:`blit_into_stitched`, and
    discards the source data immediately.  This keeps memory usage
    proportional to the union-grid size, not the number of input files.

    Typical usage (after per-burst interferogram/coherence generation)::

        burst_ifg_list = [...]    # per-burst .int.tif paths
        burst_coh_list = [...]    # per-burst .coh.tif paths

        # Stitch interferograms (complex) and coherence (float) separately
        ifg, ifg_gt, proj_wkt = stitch_bursts(burst_ifg_list, wsen)
        coh, _,       _        = stitch_bursts(burst_coh_list, wsen)

    Parameters
    ----------
    file_path_list : list of str or Path
        Paths to the per-burst (or per-tile) GeoTIFF files to be
        stitched.
    bbox_wsen : tuple
        Geographic bounding box ``(west, south, east, north)`` in
        EPSG:4326 that clips the output extent.
    epsg_utm : int, optional
        UTM EPSG code of the output grid.  When ``None`` (default) the
        EPSG is auto-detected from the source files.  If files span
        multiple UTM zones, the majority-zone is used with a warning.
    verbose : bool
        When ``True`` (default), print a one-line summary per file
        showing shape and value range.

    Returns
    -------
    stitched : np.ndarray
        Stitched array ``(rows, cols)``.  The dtype matches the first
        input file (e.g. ``complex64`` for Cf32 rasters, ``float32``
        for F32 rasters).
    out_gt : tuple
        GDAL geotransform ``(x0, dx, 0, y0, 0, dy)`` of the union grid.
    proj_wkt : str
        Projection definition string (WKT) of the output.

    Notes
    -----
    -  Pixels with ``|value| < 1e-6`` or ``NaN`` are treated as nodata
       and are overwritten by valid data from later files (last-wins).
    -  If all output pixels are zero after stitching, ensure that the
       source and output geotransforms share the same ``dx``/``dy``
       posting.
    """

    if not file_path_list:
        raise ValueError("file_path_list is empty")

    # ------------------------------------------------------------------
    # Phase 1: collect extents and auto-detect EPSG (metadata only — cheap)
    # ------------------------------------------------------------------
    extents = []
    epsgs = []
    dtypes = []

    for i, path in enumerate(file_path_list):
        ds = gdal.Open(str(path))
        if ds is None:
            raise FileNotFoundError(f"Cannot open {path}")

        gt = ds.GetGeoTransform()
        nrows = ds.RasterYSize
        ncols = ds.RasterXSize
        proj_wkt_f = ds.GetProjection()
        gdal_dtype = ds.GetRasterBand(1).DataType
        ds = None

        # Derive EPSG from projection WKT
        srs = osr.SpatialReference()
        srs.ImportFromWkt(proj_wkt_f)
        epsg = srs.GetAttrValue('AUTHORITY', 1)
        epsg = int(epsg) if epsg else -1

        extents.append((gt[0], gt[1], gt[3], gt[5],
                        nrows, ncols, epsg, proj_wkt_f))
        epsgs.append(epsg)
        dtypes.append(gdal_dtype)

    # --- Resolve EPSG ---
    if epsg_utm is None:
        unique = [e for e in epsgs if e > 0]
        if not unique:
            raise RuntimeError(
                "Cannot determine EPSG — none of the input files have "
                "a valid projection EPSG code.")
        if len(set(unique)) > 1:
            epsg_utm = Counter(unique).most_common(1)[0][0]
            warnings.warn(
                f"Files span multiple EPSG zones {set(unique)}. "
                f"Using majority zone EPSG:{epsg_utm}.")
        else:
            epsg_utm = unique[0]

    # Sanitise extents: replace unknown EPSG codes with the resolved EPSG
    for i, e in enumerate(extents):
        if e[6] <= 0:
            extents[i] = (e[0], e[1], e[2], e[3], e[4], e[5],
                          epsg_utm, e[7])

    # ------------------------------------------------------------------
    # Phase 2: compute union grid (no data loaded yet)
    # ------------------------------------------------------------------
    out_gt, out_rows, out_cols, proj_wkt = compute_union_grid(
        extents, bbox_wsen, epsg_utm=epsg_utm)

    if verbose:
        print(f"Union grid: {out_rows} rows x {out_cols} cols"
              f"  (EPSG:{epsg_utm})")

    # ------------------------------------------------------------------
    # Phase 3: auto-detect output dtype from the first file
    # ------------------------------------------------------------------
    gdal_dtype = dtypes[0] if dtypes else gdal.GDT_Float32
    _GDAL_TO_NUMPY = {
        gdal.GDT_CFloat32: np.complex64,
        gdal.GDT_Float32: np.float32,
        gdal.GDT_Float64: np.float64,
        gdal.GDT_Int16: np.int16,
        gdal.GDT_Int32: np.int32,
        gdal.GDT_UInt16: np.uint16,
        gdal.GDT_Byte: np.uint8,
        gdal.GDT_CFloat64: np.complex128,
    }
    out_dtype = _GDAL_TO_NUMPY.get(gdal_dtype, np.float32)

    # ------------------------------------------------------------------
    # Phase 4: pre-allocate + blit each file (memory-efficient)
    # ------------------------------------------------------------------
    stitched = np.zeros((out_rows, out_cols), dtype=out_dtype)

    for i, path in enumerate(file_path_list):
        ds = gdal.Open(str(path))
        arr = ds.GetRasterBand(1).ReadAsArray()
        src_gt = ds.GetGeoTransform()
        ds = None

        # Detect complex vs real for value-range reporting
        is_complex = np.issubdtype(arr.dtype, np.complexfloating)

        # Blit into stitched array
        blit_into_stitched(stitched, out_gt, arr, src_gt)

        # Report
        if verbose:
            valid = np.isfinite(arr)
            if is_complex:
                v_range = np.abs(arr[valid])
            else:
                v_range = arr[valid]
            lo = float(v_range.min()) if valid.any() else 0.0
            hi = float(v_range.max()) if valid.any() else 0.0
            fname = Path(path).name
            print(f"  {fname}: {arr.shape}  "
                  f"range [{lo:.3f}, {hi:.3f}]")

        # Immediately release memory
        del arr

    return stitched, out_gt, proj_wkt, epsg_utm

def multilook_ifg(arr, az_looks, rg_looks, ifg_gt=None):
    """Multilook a complex or real array by non-overlapping block averaging.

    Parameters
    ----------
    arr : np.ndarray
        Input array ``[rows, cols]``.
    az_looks : int
        Number of looks in the azimuth (row) direction.
    rg_looks : int
        Number of looks in the range (column) direction.
    ifg_gt : tuple, optional
        GDAL geotransform of the original interferogram ``(x0, dx, 0, y0, 0, dy)``.
        If provided, returns the multilooked geotransform ``gt_ml`` as well.

    Returns
    -------
    ml : np.ndarray
        Multilooked array ``[rows//az_looks, cols//rg_looks]``.
    gt_ml : tuple, optional
        Multilooked geotransform, returned only if *ifg_gt* is provided.
    """
    nr, nc = arr.shape
    nr = nr - nr % az_looks
    nc = nc - nc % rg_looks
    ml = arr[:nr, :nc].reshape(
        nr // az_looks, az_looks, nc // rg_looks, rg_looks).mean(axis=(1, 3))
    if ifg_gt is not None:
        gt_ml = (ifg_gt[0], ifg_gt[1] * rg_looks, 0.0,
                 ifg_gt[3], 0.0, ifg_gt[5] * az_looks)
        return ml, gt_ml
    return ml

def multilook_nearest(arr, az_looks, rg_looks):
    """Decimate by nearest-neighbour (every N-th row and column).

    Suitable for non-continuous data such as incidence and azimuth angles
    where averaging would distort the meaning.

    Parameters
    ----------
    arr : np.ndarray
        Input array, shape ``[rows, cols]`` or ``[bands, rows, cols]``.
    az_looks : int
        Decimation factor in the rows (azimuth) direction.
    rg_looks : int
        Decimation factor in the columns (range) direction.

    Returns
    -------
    ml : np.ndarray
        Downsampled array.
    """
    if arr.ndim == 2:
        return arr[::az_looks, ::rg_looks]
    else:
        return arr[:, ::az_looks, ::rg_looks]

# ===========================================================================
# 7. Goldstein Adaptive Phase Filter
# ===========================================================================

def goldstein_filter(complex_arr, alpha=0.5, psize=32, no_data_value=None,
                     nodata_mask=None):
    """Goldstein adaptive phase filter with overlapping patches.

    Parameters
    ----------
    complex_arr : np.ndarray (complex64)
        Input complex interferogram ``[rows, cols]``.
    alpha : float
        Filter exponent in [0, 1].
    psize : int
        FFT patch size (power of 2 recommended).
    no_data_value : float or None
        Assigning extra no-data-value besides the traditional
        NaN and zero values. Ignored when ``nodata_mask`` is given.
    nodata_mask : np.ndarray (bool) or None
        Explicit no-data mask, same shape as ``complex_arr``. Preferred over
        ``no_data_value``: ``filter_tif`` uses ``np.abs(arr) < 1e-6``, which
        catches near-zero complex values that an exact ``== 0`` test misses.

    Returns
    -------
    filtered : np.ndarray (complex64)
        Filtered complex array, same shape as input.
    """
    orig_rows, orig_cols = complex_arr.shape
    pad = psize // 2
    step = pad
    half = pad

    wx = (1.0 - np.abs(np.arange(half) - (psize / 2.0 - 1.0))
          / (psize / 2.0 - 1.0))
    wy = (1.0 - np.abs(np.arange(half) - (psize / 2.0 - 1.0))
          / (psize / 2.0 - 1.0))
    q = np.outer(wy, wx)
    wf = np.block([[q, np.flip(q, 1)],
                   [np.flip(q, 0), np.flip(np.flip(q, 0), 1)]])

    # Accept EITHER an explicit boolean mask or a scalar sentinel.
    # filter_tif() passes nodata_mask=np.abs(arr) < 1e-6, which previously
    # raised "unexpected keyword argument 'nodata_mask'" and made section 3.6
    # crash on the first file. Simply renaming the keyword would have been
    # worse than the crash: `complex_arr == <bool array>` broadcasts to an
    # all-False mask, silently disabling no-data handling.
    if nodata_mask is not None:
        nodata_mask = np.asarray(nodata_mask, dtype=bool)
        if nodata_mask.shape != complex_arr.shape:
            raise ValueError(
                f"nodata_mask shape {nodata_mask.shape} != input {complex_arr.shape}")
    elif no_data_value is not None:
        nodata_mask = (complex_arr == no_data_value)
    else:
        nodata_mask = np.zeros(complex_arr.shape, dtype=bool)

    padded = np.pad(complex_arr, ((pad, pad), (pad, pad)), mode='constant')
    p_rows, p_cols = padded.shape

    nodata = np.pad(nodata_mask, ((pad, pad), (pad, pad)),
                    mode='constant', constant_values=True)

    filtered = np.zeros((p_rows, p_cols), dtype=np.complex64)
    norm = np.zeros((p_rows, p_cols), dtype=np.float32)

    for i in range(0, p_rows - psize + 1, step):
        for j in range(0, p_cols - psize + 1, step):
            ri, rj = slice(i, i + psize), slice(j, j + psize)
            patch = padded[ri, rj].copy()

            if np.all(nodata[ri, rj]):
                continue

            patch[nodata[ri, rj]] = 0
            S = np.fft.fft2(patch, s=(psize, psize))
            H = np.power(np.abs(S), alpha)
            S = H * S
            pf = np.fft.ifft2(S, s=(psize, psize))

            w = wf[:patch.shape[0], :patch.shape[1]]
            filtered[ri, rj] += pf * w
            norm[ri, rj] += w

    valid = norm > 0
    filtered[valid] /= norm[valid]
    filtered = filtered[pad:pad + orig_rows, pad:pad + orig_cols]
    filtered[nodata_mask] = 0 + 0j

    return filtered

# ===========================================================================
# 8. Phase-Sigma Coherence Estimation
# ===========================================================================

def _gaussian_kernel(size):
    """Generate a normalized 2-D Gaussian weighting kernel.

    Matches ISCE2 Fortran ph_slope.F / ph_sigma.F: sigma^2 = size/2.0.
    """
    half = size // 2
    s1 = 0.0
    kernel = np.zeros((size, size), dtype=np.float64)
    for k in range(size):
        for j in range(size):
            w1 = (k - half) ** 2 + (j - half) ** 2
            kernel[k, j] = np.exp(-w1 / (size / 2.0))
            s1 += kernel[k, j]
    return (kernel / s1).astype(np.float32)

def estimate_phsig_correlation(ifg_arr, ps_win=5, grad_win=5, nlks=3.0):
    """Estimate phase-sigma correlation from a complex interferogram.

    Matches ISCE2 Fortran ``ph_slope.F`` + ``ph_sigma.F`` algorithm:
    Gaussian-weighted phase gradient estimation, local window
    deramping with unweighted circular-mean phase reference, weighted
    phase variance, and NLKS-based correlation conversion.

    Parameters
    ----------
    ifg_arr : np.ndarray (complex64)
        Complex interferogram ``[rows, cols]``.
    ps_win : int
        Phase-sigma estimation window size (odd).
    grad_win : int
        Gradient estimation window size (odd).
    nlks : float
        Number of looks parameter. ISCE2 default is 3.0.

    Returns
    -------
    coh_phsig : np.ndarray (float32)
        Phase-sigma correlation array, clipped to [0, 1].
    """

    rows, cols = ifg_arr.shape

    if ps_win % 2 == 0:
        ps_win += 1
    if grad_win % 2 == 0:
        grad_win += 1
    ps_half = ps_win // 2
    grad_half = grad_win // 2

    padded = np.pad(ifg_arr,
                    ((grad_half, grad_half), (grad_half, grad_half)),
                    mode='constant')

    rg_diff = (
        padded[grad_half:grad_half + rows,
               grad_half:grad_half + cols] *
        np.conj(padded[grad_half:grad_half + rows,
                       grad_half - 1:grad_half + cols - 1])
    )
    az_diff = (
        padded[grad_half:grad_half + rows,
               grad_half:grad_half + cols] *
        np.conj(padded[grad_half - 1:grad_half + rows - 1,
                       grad_half:grad_half + cols])
    )

    gk = _gaussian_kernel(grad_win)
    rg_smooth = ndimage.correlate(rg_diff, gk)
    az_smooth = ndimage.correlate(az_diff, gk)

    rg_slope = np.arctan2(rg_smooth.imag, rg_smooth.real)
    az_slope = np.arctan2(az_smooth.imag, az_smooth.real)
    rg_slope[np.abs(rg_smooth) == 0] = 0.0
    az_slope[np.abs(az_smooth) == 0] = 0.0

    # Match Fortran ph_slope.F valid range: [half+1, size-half-1]
    # Fortran computes slopes for i from half+1 to nline-half-1
    # (inclusive). Rows [0..half] and [nline-half..nline-1] are zero.
    # Same for columns.
    if grad_half > 0:
        rg_slope[:grad_half + 1, :] = 0.0
        rg_slope[-(grad_half):, :] = 0.0
        rg_slope[:, :grad_half + 1] = 0.0
        rg_slope[:, -(grad_half):] = 0.0
        az_slope[:grad_half + 1, :] = 0.0
        az_slope[-(grad_half):, :] = 0.0
        az_slope[:, :grad_half + 1] = 0.0
        az_slope[:, -(grad_half):] = 0.0

    offsets = np.arange(-ps_half, ps_half + 1)
    di_mesh, dj_mesh = np.meshgrid(offsets, offsets, indexing='ij')
    ps_weights = _gaussian_kernel(ps_win)

    coh = np.zeros((rows, cols), dtype=np.float32)

    i_idx = np.arange(ps_half, rows - ps_half)
    j_idx = np.arange(ps_half, cols - ps_half)
    I, J = np.meshgrid(i_idx, j_idx, indexing='ij')
    i_flat = I.ravel()
    j_flat = J.ravel()

    n_total = len(i_flat)
    batch_size = 500

    for b_start in range(0, n_total, batch_size):
        b_end = min(b_start + batch_size, n_total)
        bi = i_flat[b_start:b_end]
        bj = j_flat[b_start:b_end]

        row_idx = bi[:, None, None] + di_mesh[None, :, :]
        col_idx = bj[:, None, None] + dj_mesh[None, :, :]
        windows = ifg_arr[row_idx, col_idx]

        rg_s = rg_slope[bi, bj]
        az_s = az_slope[bi, bj]
        ramp = (di_mesh[None, :, :] * az_s[:, None, None] +
                dj_mesh[None, :, :] * rg_s[:, None, None])

        exp_ramp = np.cos(ramp) - 1j * np.sin(ramp)
        comp = windows * exp_ramp

        wsum = np.sum(comp, axis=(1, 2))
        mag = np.abs(wsum)

        valid = mag > 1e-10
        if not np.any(valid):
            continue
        vidx = np.flatnonzero(valid)

        norm_sum = wsum[vidx] / mag[vidx]
        deramped = comp[vidx] * np.conj(norm_sum[:, None, None])

        phases = np.arctan2(deramped.imag, deramped.real)
        wt = ps_weights[None, :, :]
        mean_ph = np.sum(wt * phases, axis=(1, 2))
        mean_ph2 = np.sum(wt * phases * phases, axis=(1, 2))
        var = mean_ph2 - mean_ph * mean_ph

        var_pos = var > 0
        if np.any(var_pos):
            gidx = vidx[var_pos]
            coh[bi[gidx], bj[gidx]] = (
                1.0 / np.sqrt(2.0 * nlks * var[var_pos] + 1.0)
            )
        if np.any(~var_pos):
            gidx = vidx[~var_pos]
            coh[bi[gidx], bj[gidx]] = 1.0

    return np.clip(coh, 0.0, 1.0)

# ===========================================================================
# 9. GeoTIFF I/O
# ===========================================================================

def save_tiff(out_path, data, gt, proj_wkt, dtype=None):
    """Save a numpy array as a GeoTIFF (single or multi-band).

    2-D ``[rows, cols]`` → single-band.
    3-D ``[bands, rows, cols]`` → multi-band.

    Parameters
    ----------
    out_path : str or Path
    data : np.ndarray  2-D or 3-D.
    gt : tuple  GDAL geotransform.
    proj_wkt : str  Projection WKT.
    dtype : int, optional  GDAL type. Auto-detected when None.
    """
    drv = gdal.GetDriverByName('GTiff')

    if data.ndim == 2:
        bands, rows, cols = 1, *data.shape
    elif data.ndim == 3:
        bands, rows, cols = data.shape[0], data.shape[1], data.shape[2]
    else:
        raise ValueError(f'Expected 2-D or 3-D array, got {data.ndim}-D')

    if dtype is None:
        dtype_map = {
            np.float32: gdal.GDT_Float32, np.float64: gdal.GDT_Float64,
            np.int32: gdal.GDT_Int32, np.int16: gdal.GDT_Int16,
            np.uint8: gdal.GDT_Byte, np.uint16: gdal.GDT_UInt16,
            np.complex64: gdal.GDT_CFloat32,
        }
        dtype = dtype_map.get(data.dtype.type, gdal.GDT_Float32)

    ds = drv.Create(str(out_path), cols, rows, bands, dtype)
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj_wkt)

    if data.ndim == 2:
        ds.GetRasterBand(1).WriteArray(data)
    else:
        for b in range(bands):
            ds.GetRasterBand(b + 1).WriteArray(data[b])

    ds = None

# ===========================================================================
# 10. Troposphere & Auxiliary Datasets
# ===========================================================================

def compute_static_troposphere_delay(incidence_angle_arr, hgt_arr):
    """Compute troposphere delay using static model.

    Identical to ``compass.utils.lut::compute_static_troposphere_delay()``
    (COMPASS v0.5.7+).

    Parameters
    ----------
    incidence_angle_arr : np.ndarray
        Incidence angle raster in degrees, on the radar grid.
    hgt_arr : np.ndarray
        Surface height raster in metres, on the radar grid (same shape).

    Returns
    -------
    tropo : np.ndarray
        Troposphere delay in slant range (m), same shape as inputs.
    """
    ZPD = 2.3
    H = 6000.0
    tropo = ZPD / np.cos(np.deg2rad(incidence_angle_arr)) * np.exp(-1 * hgt_arr / H)
    return tropo

def read_aux_dataset(h5_path, dem_path):
    """Read all auxiliary datasets from an OPERA CSLC H5 file.

    Returns a dict where each key is the dataset name and the value
    is either the numpy array (for datasets found) or the shape
    tuple for reference grids.

    Parameters
    ----------
    h5_path : str or Path
    dem_path : str or Path

    Returns
    -------
    aux : dict
        Contains all timing_corrections datasets, x/y/projection metadata,
        azimuth_carrier_phase, flattening_phase, plus the LUT grid shapes.
    """
    corr_grp = '/metadata/processing_information/timing_corrections'
    aux = {}

    with h5py.File(h5_path, 'r') as f:
        # 2-D correction arrays on the LUT grid
        for name in ['slant_range', 'zero_doppler_time', 'bistatic_delay',
                     'geometry_steering_doppler', 'los_solid_earth_tides',
                     'azimuth_solid_earth_tides', 'los_ionospheric_delay',
                     'azimuth_fm_rate_mismatch']:
            ds_path = f'{corr_grp}/{name}'
            if ds_path in f:
                aux[name] = f[ds_path][:]

        # 1-D ray profiles (will be broadcast to 2-D)
        for name in ['slant_range_spacing', 'zero_doppler_time_spacing']:
            ds_path = f'{corr_grp}/{name}'
            if ds_path in f:
                aux[name] = f[ds_path][()]

        # No UTM-grid datasets needed for pixel-offset display

        aux['x_coordinates'] = f['/data/x_coordinates'][:]
        aux['y_coordinates'] = f['/data/y_coordinates'][:]
        aux['epsg'] = int(f['/data/projection'][()])
        aux['x_spacing'] = float(f['/data/x_spacing'][()])
        aux['y_spacing'] = float(f['/data/y_spacing'][()])

    # LUT grid sizes
    aux['n_az'] = len(aux['zero_doppler_time']) if 'zero_doppler_time' in aux else 0
    aux['n_rg'] = len(aux['slant_range']) if 'slant_range' in aux else 0

    # calc static tropospheric delay in pixel
    tropo = compute_static_troposphere_correction(aux, h5_path, dem_path)
    aux['los_static_tropospheric_delay'] = tropo / RANGE_SPACING_PX

    return aux

def compute_static_troposphere_correction(aux, h5_path, dem_path):
    """Compute static troposphere delay on the CSLC radar LUT grid.

    Parameters
    ----------
    h5_path : str or Path
    dem_path : str or Path

    Returns
    -------
    tropo_disp : np.ndarray (float32)
        Shape ``(n_az, n_rg)`` on the LUT grid, in metres.
    """

    sr = aux['slant_range']           # 1-D
    azt = aux['zero_doppler_time']    # 1-D
    x = aux['x_coordinates']
    y = aux['y_coordinates']
    epsg = aux['epsg']
    n_az, n_rg = len(azt), len(sr)

    dem_ds = gdal.Open(str(dem_path))
    dem_gt = dem_ds.GetGeoTransform()
    tf_utm2ll = Transformer.from_crs(f'EPSG:{epsg}', 'EPSG:4326', always_xy=True)

    dx_geo = x[1] - x[0]
    dy_geo = y[1] - y[0]
    x_c = [x[0] - dx_geo / 2, x[-1] + dx_geo / 2, x[-1] + dx_geo / 2, x[0] - dx_geo / 2]
    y_c = [y[0] - dy_geo / 2, y[0] - dy_geo / 2, y[-1] + dy_geo / 2, y[-1] + dy_geo / 2]
    lon_c, lat_c = tf_utm2ll.transform(x_c, y_c)

    margin_deg = 0.1
    lon_margin = margin_deg / abs(dem_gt[1])
    lat_margin = margin_deg / abs(dem_gt[5])
    col0 = max(0, int(np.floor((min(lon_c) - dem_gt[0]) / dem_gt[1] - lon_margin)))
    col1 = min(dem_ds.RasterXSize,
               int(np.ceil((max(lon_c) - dem_gt[0]) / dem_gt[1] + lon_margin)) + 1)
    row0 = max(0, int(np.floor((max(lat_c) - dem_gt[3]) / dem_gt[5] - lat_margin)))
    row1 = min(dem_ds.RasterYSize,
               int(np.ceil((min(lat_c) - dem_gt[3]) / dem_gt[5] + lat_margin)) + 1)
    h_dem = dem_ds.GetRasterBand(1).ReadAsArray(col0, row0, col1 - col0, row1 - row0)
    dem_ds = None
    h_rg = resize(h_dem.astype(np.float32), (n_az, n_rg),
                  order=1, mode='edge', anti_aliasing=False)
    h_rg = np.maximum(h_rg, 0.0)

    inc_deg = compute_isce3_incidence_angle(h5_path)
    return compute_static_troposphere_delay(inc_deg, h_rg)

# ===========================================================================
# 11. Dataset Display Helpers & Timing Plot
# ===========================================================================

def _to_pixels(data, unit, grid):
    """Convert physical-unit data to pixel units.

    Parameters
    ----------
    data : np.ndarray
    unit : str  One of ``'m'``, ``'s'``, ``'rad'``.
    grid : str  ``'lut'`` or ``'utm'``.

    Returns
    -------
    data_px : np.ndarray  Same shape as *data*.
    """
    if unit == 'm':
        return data / RANGE_SPACING_PX
    elif unit == 's':
        return data / AZIMUTH_SPACING_PX
    else:  # rad -- no pixel conversion
        return data

def _broadcast_1d_to_2d(data, broadcast_direction, n_az, n_rg):
    """Broadcast a 1-D array to 2-D on the LUT grid.

    Parameters
    ----------
    data : np.ndarray  1-D array.
    broadcast_direction : str
        ``'azimuth'``  → tile along rows    (output shape ``(n_az, len(data))``).
        ``'range'``    → tile along columns (output shape ``(n_az, n_rg)``).
    n_az, n_rg : int  LUT grid dimensions.

    Returns
    -------
    arr : np.ndarray  2-D ``(n_az, n_rg)``.
    """
    if broadcast_direction == 'azimuth':
        # data shape (n_rg,) → repeat along rows
        return np.tile(data[np.newaxis, :], (n_az, 1))
    elif broadcast_direction == 'range':
        # data shape (n_az,) → repeat along cols
        return np.tile(data[:, np.newaxis], (1, n_rg))
    elif broadcast_direction == 'none':
        return data
    else:
        raise ValueError(f'Unknown broadcast_direction: {broadcast_direction}')

def _get_data(h5_path, name, aux=None):
    """Get a named dataset (2-D or 1-D, possibly broadcast).

    Special names:
      ``'troposphere'`` — computed static troposphere delay (requires *dem_path*
      to be passed through *kwargs*; *aux* is ignored).

    Returns
    -------
    data_2d : np.ndarray  Square pixels ``(n_az, n_rg)`` in physical units.
    unit : str            ``'m'``, ``'s'``, or ``'rad'``.
    grid : str            ``'lut'`` or ``'utm'``.
    meta : dict           Metadata entry from ``_DATASET_META``.
    """
    if name == 'troposphere':
        raise RuntimeError('troposphere must be handled specially by caller')

    if aux is None:
        aux = read_aux_dataset(h5_path)

    meta = _DATASET_META[name]
    grid = meta['grid']
    unit = meta['unit']
    broadcast_dir = meta.get('broadcast', 'none')
    n_az, n_rg = aux.get('n_az', 0), aux.get('n_rg', 0)

    if name not in aux:
        raise KeyError(f'Dataset "{name}" not found in {h5_path}')

    data = aux[name]

    # Broadcast 1-D arrays to 2-D
    data = _broadcast_1d_to_2d(data, broadcast_dir, n_az, n_rg)

    return data, unit, grid, meta

def plot_timing(h5_path, ds_name, aux=None, burst_id='burst', date_str=''):
    """Read/compute, print statistics, and plot a single dataset in pixel units.

    Parameters
    ----------
    h5_path : str or Path
    ds_name : str
        One of ``_DATASET_META`` keys or ``'troposphere'``.
    dem_path : str or Path, optional
        Required only when ``ds_name='troposphere'``.
    aux : dict, optional
        Pre-loaded result of :func:`read_aux_dataset` (avoids re-reading).
    burst_id, date_str : str
        Labels for print output.
    """

    if aux is None:
        raise KeyError(f'aux not set')

    # ---------- UTM-grid datasets ----------
    if ds_name not in aux:
        raise KeyError(f'Dataset "{ds_name}" not found in H5')

    meta = _DATASET_META[ds_name]
    grid = meta['grid']
    unit = meta['unit']
    broadcast_dir = meta.get('broadcast', 'none')
    n_az, n_rg = aux.get('n_az', 0), aux.get('n_rg', 0)

    raw = aux[ds_name]
    raw_2d = _broadcast_1d_to_2d(raw, broadcast_dir, n_az, n_rg)
    data_px = _to_pixels(raw_2d, unit, grid)
    _print_stats(ds_name, raw_2d, unit)

    # ---------- LUT grid (all datasets are radar coordinates) ----------
    sr = aux['slant_range']
    azt = aux['zero_doppler_time']
    extent = [sr[0], sr[-1], azt[-1], azt[0]]
    label = f"{burst_id}_{date_str.replace('-','')}: {ds_name}"

    _plot_dataset(
        data_px, extent=extent, grid='lut',
        title=label, cbar_units='pixels',
        xlabel='Slant range (m)', ylabel='Azimuth time (s)',
        figsize=(9, 3),
    )
    return

def _print_stats(name, data, unit):
    """Print mean/std statistics for a dataset."""
    print(f'  {name:35s}  mean={np.nanmean(data): .4e} {unit:4s}  '
          f'std={np.nanstd(data): .4e} {unit}')

def _plot_dataset(data, extent, grid, title, cbar_units,
                  xlabel='', ylabel='', cmap='RdBu_r', figsize=(8,5)):
    """Plot a single 2-D array with proper axes.

    Parameters
    ----------
    data : np.ndarray  2-D ``(rows, cols)``.
    extent : list  ``[xmin, xmax, ymin, ymax]``.
    grid : str  ``'lut'`` or ``'utm'``.
    title : str  Figure title.
    cbar_units : str  Colorbar label suffix.
    xlabel, ylabel : str
    cmap : str
    """

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    im = ax.imshow(data, extent=extent, aspect='auto', cmap=cmap,
                   interpolation='none', origin='upper')
    cbar = plt.colorbar(im, ax=ax, shrink=0.85)
    cbar.set_label(cbar_units)

    ax.set_title(title, fontsize=12)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)

    if grid == 'lut':
        ax.xaxis.set_major_locator(ticker.MultipleLocator(20000))
    else:
        ax.xaxis.set_major_formatter(ticker.FormatStrFormatter('%.0f'))

    fig.tight_layout()
    show_and_close()

# ===========================================================================
# 12. Water Mask
# ===========================================================================

def load_water_mask(gt_ml, ml_shape, epsg_utm, wbd_path=None):
    """Load water mask resampled to a target UTM grid via GDAL nearest-neighbour reprojection.

    Reads the pre-downloaded ``swbd_nasadem.wbd`` binary raster and warps it
    onto the target multilooked grid using :func:`gdal.ReprojectImage` with
    ``GRA_NearestNeighbour``.  Returns a uint8 array where **1 = water, 0 = land**.

    Parameters
    ----------
    gt_ml : tuple
        GDAL geotransform ``(x0, dx, 0, y0, 0, dy)`` of the target grid.
    ml_shape : tuple (rows, cols)
        Shape of the target grid.
    epsg_utm : int
        UTM EPSG code of the target grid.
    wbd_path : str, optional
        Full path to the ``.wbd`` binary raster file. A ``.json`` file with
        the same basename is expected alongside it.

    Returns
    -------
    mask : np.ndarray (uint8)
        ``[rows, cols]`` with 1 = water, 0 = land.
    """

    wbd_path = str(wbd_path)

    # --- read WBD metadata and binary raster ---
    with open(f'{os.path.splitext(wbd_path)[0]}.json') as _fj:
        meta = json.load(_fj)
    raw = np.fromfile(wbd_path, dtype=np.uint8)
    wbd = raw.reshape(meta['height'], meta['width'])

    # --- wrap WBD as in-memory GDAL dataset (WGS84) ---
    mem_drv = gdal.GetDriverByName('MEM')

    srs_wgs84 = osr.SpatialReference()
    srs_wgs84.ImportFromEPSG(4326)
    wbd_gt = (meta['lon0'], meta['dlon'], 0, meta['lat0'], 0, meta['dlat'])

    src_ds = mem_drv.Create('', meta['width'], meta['height'], 1, gdal.GDT_Byte)
    src_ds.SetGeoTransform(wbd_gt)
    src_ds.SetProjection(srs_wgs84.ExportToWkt())
    src_ds.GetRasterBand(1).WriteArray(wbd)

    # --- create target UTM grid ---
    srs_utm = osr.SpatialReference()
    srs_utm.ImportFromEPSG(epsg_utm)
    rows, cols = ml_shape

    dst_ds = mem_drv.Create('', cols, rows, 1, gdal.GDT_Byte)
    dst_ds.SetGeoTransform(gt_ml)
    dst_ds.SetProjection(srs_utm.ExportToWkt())

    # --- warp: WGS84 → UTM, nearest-neighbour ---
    gdal.ReprojectImage(
        src_ds, dst_ds,
        srs_wgs84.ExportToWkt(), srs_utm.ExportToWkt(),
        gdal.GRA_NearestNeighbour,
    )

    warped = dst_ds.GetRasterBand(1).ReadAsArray()

    # close MEM datasets
    src_ds = None
    dst_ds = None

    # 1 = water, 0 = land
    return (warped > 0).astype(np.bool_)

def download_nasadem_water_mask(bbox_wsen, output_dir):
    """Download NASADEM HGT tiles and stitch a water-body mask raster.

    Downloads 1-arcsecond NASADEM tiles covering *bbox_wsen* from the
    NASA Earthdata Cloud, extracts the water mask from bit 15 of each
    int16 pixel, and saves a BYTE raster (255=water, 0=land) as
    ``swbd_nasadem.wbd`` in *output_dir*.

    Authentication uses ``~/.netrc``.  Already-downloaded tiles are
    cached in ``~/.cache/sardem/``.

    Parameters
    ----------
    bbox_wsen : tuple
        WGS84 bounding box ``(west, south, east, north)`` in degrees.
    output_dir : str or Path
        Directory where ``swbd_nasadem.wbd`` is written.
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / 'swbd_nasadem.wbd'

    # Earthdata credentials
    nrc = netrc(Path.home() / '.netrc')
    auth = nrc.authenticators('urs.earthdata.nasa.gov')
    if auth is None:
        auth = nrc.authenticators('e4ftl01.cr.usgs.gov')
    user, _, pwd = auth if auth else (None, None, None)
    if not user or not pwd:
        raise RuntimeError('No Earthdata credentials in ~/.netrc')

    # Determine tile range
    west, south, east, north = bbox_wsen
    lon_start = int(np.floor(west))
    lon_end = int(np.floor(east))
    lat_start = int(np.floor(south))
    lat_end = int(np.floor(north))

    cache_dir = Path.home() / '.cache' / 'sardem'
    cache_dir.mkdir(parents=True, exist_ok=True)
    base_url = ('https://data.lpdaac.earthdatacloud.nasa.gov/'
                'lp-prod-protected/NASADEM_HGT.001')

    # Full raster: 1 arcsec, coverage per tile = 3601×3601 (1°×1° + edge overlap)
    stride = 3600
    total_lat = (lat_end - lat_start + 1) * stride
    total_lon = (lon_end - lon_start + 1) * stride
    full = np.full((total_lat, total_lon), 255, dtype=np.uint8)

    for lat_idx in range(lat_start, lat_end + 1):
        for lon_idx in range(lon_start, lon_end + 1):
            lat_pfx = 's' if lat_idx < 0 else 'n'
            lon_pfx = 'w' if lon_idx < 0 else 'e'
            tile = f'{lat_pfx}{abs(lat_idx):02d}{lon_pfx}{abs(lon_idx):03d}'
            filename = f'NASADEM_HGT_{tile}'
            zip_path = cache_dir / f'{filename}.zip'
            hgt_path = cache_dir / f'{tile}.hgt'

            # Download if not cached
            if not hgt_path.exists():
                url = f'{base_url}/{filename}/{filename}.zip'
                print(f'  Downloading {tile} ...', end=' ', flush=True)
                r = requests.get(url, auth=(user, pwd), timeout=60)
                if r.status_code == 200:
                    with open(zip_path, 'wb') as f:
                        f.write(r.content)
                    with zipfile.ZipFile(zip_path) as zf:
                        for member in zf.namelist():
                            if member.endswith('.hgt'):
                                zf.extract(member, cache_dir)
                                extracted = Path(cache_dir) / member
                                extracted.rename(hgt_path)
                                break
                    print('OK')
                elif r.status_code == 404:
                    print('not found (ocean tile)')
                    continue
                else:
                    print(f'HTTP {r.status_code}')
                    continue
            else:
                print(f'  {tile}: using cached {hgt_path}')

            # Read tile, extract water mask
            h = np.fromfile(hgt_path, dtype='>i2').reshape(3601, 3601)
            # Remove 1-pixel overlap edge: 3601→3600
            h = h[:stride, :stride]
            water = ((h >> 15) & 1) | (h == -32768) | (h <= 0)
            water = water.astype(np.uint8) * 255

            # Place in full raster (row 0 = north = highest lat)
            row = (lat_end - lat_idx) * stride
            col = (lon_idx - lon_start) * stride
            full[row:row + stride, col:col + stride] = water

    full.tofile(str(out_path))

    # Save geo-metadata alongside the .wbd file
    _dlon = 1.0 / stride
    _dlat = -1.0 / stride
    meta = {
        'width': full.shape[1],
        'height': full.shape[0],
        'lon0': float(lon_start),
        'lat0': float(lat_end + 1),
        'dlon': _dlon,
        'dlat': _dlat,
    }
    with open(str(out_path).replace('.wbd', '.json'), 'w') as _f:
        json.dump(meta, _f)

    water_pct = 100.0 * (full == 255).sum() / full.size
    land_pct = 100.0 * (full == 0).sum() / full.size
    print(f'Saved {out_path} ({full.shape[1]}×{full.shape[0]}, '
          f'water={water_pct:.1f}%, land={land_pct:.1f}%)')

# ===========================================================================
# 13. LOS Angle Computation
# ===========================================================================

def compute_los_angles(static_h5_path):
    """Compute ISCE2-style incidence and azimuth angles from ISCE3 static_layer HDF5.

    Reads ``los_east`` and ``los_north`` (ground-to-satellite unit vector
    components in ENU) from an OPERA-format static_layers HDF5 file and
    converts them to the incidence and azimuth angle convention used by
    ISCE2's ``los.rdr`` / ``los.rdr.geo`` products.

    * Band 1 — incidence angle: angle between satellite→target LOS and the
      local vertical at the target, in degrees (always positive).
    * Band 2 — azimuth angle: direction of the ground→satellite LOS measured
      anti-clockwise from North, in degrees [0°, 360°).

    Parameters
    ----------
    static_h5_path : str or Path
        Path to a ``static_layers_<burst_id>.h5`` HDF5 file produced by
        COMPASS / OPERA CSLC-S1-STATIC.

    Returns
    -------
    incidence : np.ndarray (float32)
        2-D incidence angle (degrees), same shape as the static layer grid.
    azimuth : np.ndarray (float32)
        2-D azimuth angle (degrees, anti-clockwise from North).
    gt : tuple
        GDAL geotransform ``(x0, dx, 0, y0, 0, dy)``.
    epsg : int
        EPSG code of the output projection (UTM).
    """
    with h5py.File(static_h5_path, 'r') as f:
        los_east = f['/data/los_east'][:]
        los_north = f['/data/los_north'][:]
        x0 = float(f['/data/x_coordinates'][0])
        dx = float(f['/data/x_spacing'][()])
        y0 = float(f['/data/y_coordinates'][0])
        dy = float(f['/data/y_spacing'][()])
        epsg = int(f['/data/projection'][()])

    up_sq = np.maximum(0, 1 - los_east**2 - los_north**2)
    up = np.sqrt(up_sq)

    incidence = np.arccos(up, out=np.full_like(up, np.nan), where=up > 0) * 180.0 / np.pi
    azimuth = (np.arctan2(los_north, los_east) - np.pi / 2) * 180.0 / np.pi
    azimuth = azimuth % 360.0
    azimuth[up == 0] = np.nan

    gt = (x0, dx, 0, y0, 0, dy)
    return incidence.astype(np.float32), azimuth.astype(np.float32), gt, epsg

def stitch_burst_los_angles(static_layer_path_list,
                    out_gt=None, out_shape=None,
                    az_looks=None, rg_looks=None, input_gt=None):
    """Stitch LOS angles from multiple static-layer bursts into two arrays.

    **Memory-efficient**: Phase 1 reads only HDF5 metadata (x/y/spacing,
    shape) to compute the union grid.  Phase 2 processes one file at a
    time — reads the LOS vectors, converts to ISCE2-format incidence and
    azimuth angles, multilooks, blits into the pre-allocated output, then
    deletes the source arrays immediately.

    .. code-block:: python

        # auto-detect multilook factors from geotransform ratio
        static_layer_path_list = [...]

        # read input GT from one static layer file
        with h5py.File(static_layer_path_list[0]) as f:
            in_gt = (float(f['/data/x_coordinates'][0]),
                     float(f['/data/x_spacing'][()]),
                     0.0,
                     float(f['/data/y_coordinates'][0]),
                     0.0,
                     float(f['/data/y_spacing'][()]))

        inc, az, los_gt, epsg = stitch_burst_los_angles(
            static_layer_path_list,
            input_gt=in_gt,
            out_gt=gt_ml, out_shape=ifg_filt.shape,
        )

    Parameters
    ----------
    static_layer_path_list : list of str or Path
        Paths to per-burst ``static_layers_<burst_id>.h5`` HDF5 files.
    out_gt : tuple, optional
        GDAL geotransform ``(x0, dx, 0, y0, 0, dy)`` of the target output
        grid.  When provided, *out_shape* is required.  The union grid
        computation is skipped and data is cropped/padded to this grid.
    out_shape : tuple (rows, cols), optional
        Shape of the target output grid.  Required when *out_gt* is given.
    az_looks : int, optional
        Number of azimuth looks.  If ``None`` and both *input_gt* and
        *out_gt* are given, computed as ``round(|out_gt.dy| / |input_gt.dy|)``.
        Defaults to 1 when neither is available.
    rg_looks : int, optional
        Number of range looks.  If ``None`` and both *input_gt* and
        *out_gt* are given, computed as ``round(|out_gt.dx| / |input_gt.dx|)``.
        Defaults to 1 when neither is available.
    input_gt : tuple, optional
        Geotransform of the *original* (un-multilooked) static layer grid
        ``(x0, dx, 0, y0, 0, dy)``.  Used together with *out_gt* to
        auto-derive *az_looks* and *rg_looks*.  Ignored if *az_looks*
        and *rg_looks* are explicit.

    Returns
    -------
    inc_stitched : np.ndarray (float32)
        Stitched incidence angle array (degrees), shape ``(rows, cols)``.
    az_stitched : np.ndarray (float32)
        Stitched azimuth angle array (degrees), shape ``(rows, cols)``.
    final_gt : tuple
        Geotransform of the output arrays.
    epsg : int
        EPSG code of the projection.
    """
    # --- Auto-detect multilook factors from geotransform ratio ---
    if az_looks is None:
        if input_gt is not None and out_gt is not None:
            az_looks = int(round(abs(out_gt[5]) / abs(input_gt[5])))
        else:
            az_looks = 1
    if rg_looks is None:
        if input_gt is not None and out_gt is not None:
            rg_looks = int(round(abs(out_gt[1]) / abs(input_gt[1])))
        else:
            rg_looks = 1
    # ================================================================
    # Phase 1: collect extents + EPSG from HDF5 (metadata only — fast)
    # ================================================================
    epsg = None
    extents = []          # (x0, dx, y0, dy, ml_rows, ml_cols)
    ml_dx = ml_dy = 0.0

    for path in static_layer_path_list:
        path = Path(path)
        if not path.is_file():
            continue

        with h5py.File(path, 'r') as f:
            if epsg is None:
                epsg = int(f['/data/projection'][()])
            x0 = float(f['/data/x_coordinates'][0])
            y0 = float(f['/data/y_coordinates'][0])
            dx = float(f['/data/x_spacing'][()])
            dy = float(f['/data/y_spacing'][()])
            nrows, ncols = f['/data/los_east'].shape

        # Dimensions after nearest-neighbour multilook
        ml_rows = nrows // az_looks
        ml_cols = ncols // rg_looks
        ml_dx = dx * rg_looks
        ml_dy = dy * az_looks

        extents.append((x0, ml_dx, y0, ml_dy, ml_rows, ml_cols))

    if epsg is None:
        raise FileNotFoundError(
            "Cannot determine EPSG: no valid static layer HDF5 files found.")

    # ================================================================
    # Phase 2: determine output grid
    # ================================================================
    if out_gt is not None and out_shape is not None:
        out_rows, out_cols = out_shape
    else:
        # Compute union grid from all burst extents
        ulx = min(e[0] for e in extents)
        lrx = max(e[0] + e[5] * e[1] for e in extents)
        uly = max(e[2] for e in extents)
        lry = min(e[2] + e[4] * e[3] for e in extents)
        out_rows = int((uly - lry) / abs(ml_dy) + 0.5)
        out_cols = int((lrx - ulx) / abs(ml_dx) + 0.5)
        out_gt = (ulx, ml_dx, 0.0, uly, 0.0, ml_dy)

    # ================================================================
    # Phase 3: pre-allocate output arrays
    # ================================================================
    inc_stitched = np.full((out_rows, out_cols), np.nan, dtype=np.float32)
    az_stitched  = np.full((out_rows, out_cols), np.nan, dtype=np.float32)

    # ================================================================
    # Phase 4: process each file — compute LOS → multilook → blit → del
    # ================================================================
    for path in static_layer_path_list:
        path = Path(path)
        if not path.is_file():
            continue

        # Compute ISCE2-format LOS angles from ISCE3 static layer HDF5
        inc, az, gt, _ = compute_los_angles(path)

        # Nearest-neighbour multilook (angle data should not be averaged)
        if az_looks > 1 or rg_looks > 1:
            inc = multilook_nearest(inc, az_looks, rg_looks)
            az  = multilook_nearest(az, az_looks, rg_looks)
            x0, dx, _, y0, _, dy = gt
            gt = (x0, dx * rg_looks, 0.0, y0, 0.0, dy * az_looks)

        # Blit valid pixels into pre-allocated output
        validate = np.isfinite(inc)
        inc_valid = np.zeros_like(inc)
        inc_valid[validate] = inc[validate]
        blit_into_stitched(inc_stitched, out_gt, inc_valid, gt)

        az_valid = np.zeros_like(az)
        az_valid[validate] = az[validate]
        blit_into_stitched(az_stitched, out_gt, az_valid, gt)

        del inc, az, inc_valid, az_valid, validate

    return inc_stitched, az_stitched, out_gt, epsg

# ===========================================================================
# 14. COMPASS/OPERA Run Configuration & Processing
# ===========================================================================

def write_geo_runconfig(out_path, safe_path, orbit_path, burst_id,
                        dem_path, burst_database_path, tec_path=None,
                        product_path=".",
                        x_posting=5, y_posting=10):
    """Write a complete COMPASS geocoded-CSLC run-configuration YAML.

    The template mirrors the COMPASS defaults (``s1_cslc_geo.yaml``) and
    conforms to the validation schema (``s1_cslc_geo_schemas.yaml``): every
    group is written out explicitly so the full initial state is reproducible.

    Parameters
    ----------
    out_path : str or Path
        Output YAML config file path.
    safe_file : str
        Path to the SAFE directory (or zip).
    orbit_file : str
        Path to the orbit (EOF) file.
    burst_id : str
        Burst identifier, e.g. ``t124_264305_iw2``.
    dem_path : str
        Path to the DEM GeoTIFF.
    burst_database_file : str
        Path to the burst-db SQLite3 file.
    tec_file : str or None, optional
        Path to the IONEX TEC file. Omitted from the YAML when None.
    product_path : str, optional
        Output directory for CSLC products. Default ``"."``.
    x_posting, y_posting : float, optional
        Geocoding grid spacing (metres) along X and Y.
    """
    dynamic_ancillary = {
        'dem_file': str(dem_path),
        'dem_description': 'DEM description was not provided.',
    }
    if tec_path:
        dynamic_ancillary['tec_file'] = str(tec_path)

    cfg = {
        'runconfig': {
            'name': 'cslc_s1_workflow_default',
            'groups': {
                'pge_name_group': {'pge_name': 'CSLC_S1_PGE'},
                'input_file_group': {
                    'safe_file_path': [str(safe_path)],
                    'orbit_file_path': [str(orbit_path) if orbit_path else ''],
                    'burst_id': [burst_id],
                },
                'dynamic_ancillary_file_group': dynamic_ancillary,
                'static_ancillary_file_group': {
                    'burst_database_file': str(burst_database_path),
                },
                'product_path_group': {
                    'product_path': str(product_path),
                    'scratch_path': './scratch',
                    'sas_output_file': '',
                    'product_version': '0.2',
                    'product_specification_version': '0.1',
                },
                'primary_executable': {'product_type': 'CSLC_S1'},
                'processing': {
                    'polarization': 'co-pol',
                    'geocoding': {
                        'flatten': True,
                        'x_posting': x_posting,
                        'y_posting': y_posting,
                    },
                    'geo2rdr': {
                        'lines_per_block': 1000,
                        'threshold': 1.0e-8,
                        'numiter': 25,
                    },
                    'correction_luts': {
                        'enabled': True,
                        'range_spacing': 120,
                        'azimuth_spacing': 0.028,
                        'troposphere': {'delay_type': 'wet_dry'},
                    },
                    'rdr2geo': {
                        'threshold': 1.0e-8,
                        'numiter': 25,
                        'lines_per_block': 1000,
                        'extraiter': 10,
                        'compute_latitude': True,
                        'compute_longitude': True,
                        'compute_height': True,
                        'compute_layover_shadow_mask': True,
                        'compute_local_incidence_angle': True,
                        'compute_ground_to_sat_east': True,
                        'compute_ground_to_sat_north': True,
                    },
                },
                'worker': {
                    'internet_access': False,
                    'gpu_enabled': False,
                    'gpu_id': 0,
                },
                'quality_assurance': {
                    'browse_image': {
                        'enabled': True,
                        'complex_to_real': 'amplitude',
                        'percent_low': 0,
                        'percent_high': 95,
                        'gamma': 0.5,
                        'equalize': False,
                    },
                    'perform_qa': True,
                    'output_to_json': False,
                },
                'output': {
                    'cslc_data_type': 'complex64_zero_mantissa',
                    'compression_enabled': True,
                    'compression_level': 4,
                    'chunk_size': [128, 128],
                    'shuffle': True,
                },
            },
        },
    }
    with open(out_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

def _s1_cslc_worker(args):
    """Module-level worker for multiprocessing s1_cslc.py calls.

    args : tuple  ``(cfg_path, log_path)``.
    """
    cfg_path, log_path = args
    with open(log_path, 'w') as lf:
        return subprocess.run(
            ['s1_cslc.py', '--grid', 'geo', str(cfg_path)],
            stdout=lf, stderr=subprocess.STDOUT,
        ).returncode

def run_s1_cslc_parallel(cfg_list, n_workers=2):
    """Run multiple s1_cslc.py jobs in parallel.

    Parameters
    ----------
    cfg_list : list of Path
        List of config YAML file paths.
    n_workers : int
        Maximum number of parallel workers (default 2).

    Returns
    -------
    ok : int
        Number of successful jobs (exit code 0).
    """
    start_time = time.time()

    # 1. prepare tasks as inputs to multiprocessing
    tasks = []
    for i, cfg_path in enumerate(cfg_list):
        log_path = os.path.splitext(cfg_path)[0] + '.log'
        tasks.append((cfg_path, log_path))

    # 2. run parallel processing via multiprocessing
    n_workers = min(os.cpu_count(), n_workers, len(tasks))
    print(f'generating {len(cfg_list)} coregistered SLC using {n_workers} workers...')

    # Use a context manager to automatically close the pool
    results = []
    with Pool(processes=n_workers) as pool:
        # pool.imap_unordered yields results as soon as they finish
        # You must provide the 'total' parameter to calculate the percentage
        for result in tqdm(pool.imap_unordered(_s1_cslc_worker, tasks), total=len(tasks)):
            results.append(result)
    ok = sum(r == 0 for r in results)
    print(f'CSLC processing complete ({ok}/{len(tasks)}) bursts successfully.')

    # time info
    m, s = divmod(time.time() - start_time, 60)
    print(f'time used: {m:02.0f} mins {s:02.1f} secs.')

    return ok

def write_static_layers_config(src_cfg_path, dst_cfg_path):
    """Create a static-layers runconfig by copying a CSLC config and
    changing the product_type to trigger s1_static_layers processing.

    Parameters
    ----------
    src_cfg_path : str or Path
        Path to an existing CSLC geo runconfig YAML.
    dst_cfg_path : str or Path
        Output path for the static-layers runconfig.
    """
    with open(src_cfg_path) as f:
        cfg = yaml.safe_load(f)
    cfg['runconfig']['groups']['primary_executable']['product_type'] = 'CSLC_S1_STATIC'
    with open(dst_cfg_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

def generate_static_layers(burst_id_list, config_dir, process_dir,
                           template_ymd):
    """Generate static layers (layover_shadow_mask, local_incidence_angle,
    LOS vectors) for each burst ID.

    Static layers are date-independent — one run per burst covers all
    dates.  Produces ``static_layers_<burst_id>.h5`` files.

    Parameters
    ----------
    burst_id_list : list of str
        Burst identifiers.
    config_dir : Path
        Directory containing CSLC geo runconfig YAML files.
    process_dir : Path
        Process directory (parent of logs/ and CSLC/).
    template_ymd : str
        Any valid YMD for which a config exists; used as template.

    Returns
    -------
    ok : int
        Number of bursts successfully generated.
    """
    logs_dir = process_dir / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    process_dir.joinpath('CSLC').mkdir(parents=True, exist_ok=True)

    tasks = []
    for burst_id in burst_id_list:
        src = config_dir / f'geo_runconfig_{template_ymd}_{burst_id}.yaml'
        dst = config_dir / f'static_layers_{burst_id}.yaml'
        write_static_layers_config(str(src), str(dst))
        tasks.append((burst_id, dst, logs_dir / f'static_layers_{burst_id}.log'))

    return run_s1_cslc_parallel(tasks, n_workers=2)

def download_opera_static_layers(burst_id_list, process_dir, ref_ymd,
                           bbox_wsen=None):
    """Download OPERA CSLC-S1-STATIC products from ASF (fast, recommended).

    Searches ASF's OPERA-S1 catalog and downloads pre-computed
    CSLC-STATIC .h5 granules.  Much faster than generating static layers
    locally via :func:`generate_static_layers`.

    Parameters
    ----------
    burst_id_list : list of str
        Burst identifiers, e.g. ``['t124_264305_iw2', ...]``.
    process_dir : Path
        Process directory root.
    ref_ymd : str
        Reference date ``YYYYMMDD`` used for directory naming only
        (STATIC layers are date-independent).
    bbox_wsen : tuple, optional
        ``(west, south, east, north)`` bounding box used for the ASF
        spatial filter.

    Returns
    -------
    ok : int
        Number of bursts successfully downloaded.
    """

    cslc_dir = Path(process_dir) / 'CSLC'
    cslc_dir.mkdir(parents=True, exist_ok=True)

    wkt = None
    if bbox_wsen is not None:
        w, s, e, n = bbox_wsen
        wkt = f'POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))'

    print(f'Searching ASF for CSLC-STATIC products ({len(burst_id_list)} bursts)...')
    results = asf_search.search(
        dataset=asf_search.constants.DATASET.OPERA_S1,
        processingLevel='CSLC-STATIC',
        intersectsWith=wkt,
        maxResults=200,
    )

    # Map OPERA burst ID part -> ASFProduct
    # sceneName: OPERA_L2_CSLC-S1-STATIC_T124-264303-IW2_20140403_S1A_v1.0
    by_burst = {}
    for r in results:
        parts = r.properties['sceneName'].split('_')
        if len(parts) >= 4:
            by_burst.setdefault(parts[3], r)  # "T124-264303-IW2"

    print(f'  Found {len(by_burst)} unique STATIC granules')

    ok = 0
    exist_ids = []
    missing_ids = []
    for burst_id in burst_id_list:
        opera_bid = _esa_to_opera(burst_id)
        r = by_burst.get(opera_bid)
        if r is None:
            print(f'  {burst_id}: not on ASF, skip')
            missing_ids.append(burst_id)
            continue

        dst_dir = cslc_dir / burst_id / ref_ymd
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_path = dst_dir / f'static_layers_{burst_id}.h5'

        if dst_path.exists():
            print(f'  {burst_id}: exists, skip')
            ok += 1
            continue

        print(f'  {burst_id}: downloading ... ', end='', flush=True)
        try:
            r.download(str(dst_dir))
            # Rename to expected filename
            downloads = sorted(dst_dir.glob('OPERA_L2_CSLC-S1-STATIC*.h5'))
            if downloads:
                dl = downloads[-1]
                if dl != dst_path:
                    dl.rename(dst_path)
            ok += 1
            exist_ids.append(burst_id)
            print('done')
        except Exception as e:
            print(f'FAILED ({e})')

    print(f'\nStatic layers: {ok}/{len(burst_id_list)} bursts ready')
    return exist_ids, missing_ids

def _esa_to_opera(bid):
    """t124_264305_iw2 -> T124-264305-IW2"""
    p = str(bid).split('_')
    return f'T{p[0][1:]}-{p[1]:0>6}-{p[2].upper()}'

# ===========================================================================
# 15. Plotting: Coordinate Extents & Axis Formatting
# ===========================================================================

def extent_utm(gt, shape):
    """Compute imshow extent in UTM coordinates from GDAL geotransform.

    Parameters
    ----------
    gt : tuple
        GDAL geotransform ``(x0, dx, 0, y0, 0, dy)``.
    shape : tuple
        Array shape ``(rows, cols)``.

    Returns
    -------
    extent : list
        ``[left, right, bottom, top]`` in UTM metres.
    """
    nrows, ncols = shape
    x0, dx, _, y0, _, dy = gt
    left = x0
    right = x0 + ncols * dx
    bottom = y0 + nrows * dy
    top = y0
    return [left, right, bottom, top]

def extent_latlon(gt, shape, src_epsg):
    """Compute imshow extent in EPSG:4326 from a UTM geotransform.

    Transforms the four corners of the image extent from *src_epsg*
    (e.g. 32605) to EPSG:4326 and returns the bounding box for
    ``imshow(..., extent=...)`` with ``origin='upper'``.

    Parameters
    ----------
    gt : tuple
        GDAL geotransform ``(x0, dx, 0, y0, 0, dy)`` in *src_epsg*.
    shape : tuple
        Array shape ``(rows, cols)``.
    src_epsg : int
        Source EPSG code (e.g. 32605 for UTM zone 5N).

    Returns
    -------
    extent : list
        ``[lon_left, lon_right, lat_bottom, lat_top]`` in decimal degrees.
    """
    nrows, ncols = shape
    x0, dx, _, y0, _, dy = gt
    xs = [x0, x0 + ncols * dx, x0 + ncols * dx, x0]
    ys = [y0, y0, y0 + nrows * dy, y0 + nrows * dy]
    tf = Transformer.from_crs(f'EPSG:{src_epsg}', 'EPSG:4326', always_xy=True)
    lons, lats = tf.transform(xs, ys)
    return [min(lons), max(lons), min(lats), max(lats)]

def extent_pixel(shape):
    """Compute imshow extent for pixel-index display.
    
    Returns [-0.5, cols-0.5, rows-0.5, -0.5] so pixel centres
    align with integer indices.
    """
    nrows, ncols = shape
    return [-0.5, ncols - 0.5, nrows - 0.5, -0.5]

def set_ax_utm(ax, fmt='.0f'):
    """Format axis for UTM (easting/northing) display.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    fmt : str
        Tick format string (default ``'%.0f'`` for integer metres).
    """
    ax.set_xlabel(f'Easting (m)')
    ax.set_ylabel(f'Northing (m)')
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f'{v:{fmt}}'))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f'{v:{fmt}}'))
    plt.setp(ax.get_xticklabels(), rotation=30, ha='right')

def set_ax_pixel(ax, axis='both'):
    """Format axis for pixel-index display.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    """
    if axis in ['x', 'both']:
        ax.set_xlabel('Column (px)')
    if axis in ['y', 'both']:
        ax.set_ylabel('Row (px)')
    return

# ===========================================================================
# 16. Plotting: Convenience Functions
# ===========================================================================

def plot_data(ax, data, title=None, cmap='jet', vmin=None, vmax=None,
              extent=None, aspect='auto', cbar_label=None, alpha=None,
              origin='upper', shrink=0.8, discrete=False):
    """Plot a 2-D array on *ax* with imshow, colorbar and title.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    data : np.ndarray   2-D array to display.
    title : str, optional   Title text (no title when None).
    cmap : str   Colormap name (default ``'jet'``).
    vmin, vmax : float, optional   Imshow value range.
    extent : list, optional   ``[left, right, bottom, top]`` for imshow.
    aspect : str   Aspect ratio (default ``'auto'``).
    cbar_label : str, optional   Colorbar label (no bar when None).
    alpha : float, optional   Transparency.
    origin : str   Image origin (default ``'upper'``).
    shrink : float   Colorbar shrink factor.
    discrete : bool   If True, use a discrete colorbar with one tick per
        unique integer value (e.g. for connected-component labels).

    Returns
    -------
    im : matplotlib.image.AxesImage
    """

    kw = dict(
        cmap=cmap, aspect=aspect, origin=origin,
        extent=extent, interpolation='nearest',
    )
    if vmin is not None:
        kw['vmin'] = vmin
    if vmax is not None:
        kw['vmax'] = vmax
    if alpha is not None:
        kw['alpha'] = alpha

    if discrete:
        data_flat = data[np.isfinite(data)]
        unique_vals = np.unique(data_flat)
        if len(unique_vals) <= 1:
            unique_vals = np.array([0, 1])

        n_vals = len(unique_vals)
        boundaries = np.zeros(n_vals + 1)
        boundaries[0] = unique_vals[0] - 0.5
        boundaries[1:] = unique_vals + 0.5
        norm = colors.BoundaryNorm(boundaries, n_vals)

        cmap_obj = plt.get_cmap(cmap, n_vals)
        kw['cmap'] = cmap_obj
        kw['norm'] = norm

    im = ax.imshow(data, **kw)
    if title is not None:
        ax.set_title(title)
    if cbar_label is not None:
        cbar = plt.colorbar(im, ax=ax, label=cbar_label, shrink=shrink)
        if discrete:
            cbar.set_ticks(unique_vals.tolist())
    return im

def plot_phase(ax, phase, title=None, extent=None, **kwargs):
    """Plot wrapped phase with jet colormap (default -pi to pi).

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    phase : np.ndarray   2-D wrapped phase (radians).
    title : str, optional
    extent : list, optional   ``[left, right, bottom, top]``.
    **kwargs   Passed to :func:`plot_data`.
    """
    kwargs.setdefault('cmap', 'jet')
    kwargs.setdefault('vmin', -np.pi)
    kwargs.setdefault('vmax', np.pi)
    kwargs.setdefault('cbar_label', 'Phase (rad)')
    return plot_data(ax, phase, title=title, extent=extent, **kwargs)

def plot_amplitude(ax, amp_db, title=None, extent=None, **kwargs):
    """Plot amplitude in dB with gray colormap.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    amp_db : np.ndarray   2-D amplitude in dB.
    title : str, optional
    extent : list, optional
    **kwargs   Passed to :func:`plot_data`.
    """
    kwargs.setdefault('cmap', 'gray')
    kwargs.setdefault('vmin', 30)
    kwargs.setdefault('vmax', 45)
    kwargs.setdefault('cbar_label', 'Amplitude (dB)')
    return plot_data(ax, amp_db, title=title, extent=extent, **kwargs)

def plot_coherence(ax, coh, title=None, extent=None, **kwargs):
    """Plot coherence 0-1 with gray colormap.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    coh : np.ndarray   2-D coherence array, values in [0, 1].
    title : str, optional
    extent : list, optional
    **kwargs   Passed to :func:`plot_data`.
    """
    kwargs.setdefault('cmap', 'gray')
    kwargs.setdefault('vmin', 0)
    kwargs.setdefault('vmax', 1)
    kwargs.setdefault('cbar_label', 'coherence')
    return plot_data(ax, coh, title=title, extent=extent, **kwargs)

def plot_los(ax, angle, title=None, extent=None, **kwargs):
    """Plot LOS angle or incidence angle with viridis colormap.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    angle : np.ndarray   2-D angle in degrees.
    title : str, optional
    extent : list, optional
    **kwargs   Passed to :func:`plot_data`.
    """
    kwargs.setdefault('cmap', 'viridis')
    kwargs.setdefault('cbar_label', 'deg')
    return plot_data(ax, angle, title=title, extent=extent, **kwargs)

def plot_phase_over_hillshade(ax, phase, hillshade, title=None, extent=None,
                               alpha=0.8, **kwargs):
    """Plot phase overlay on a hillshade background.

    Renders the hillshade in gray, then overlays the phase with
    the given *alpha* transparency and jet colormap.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    phase : np.ndarray   2-D phase array (radians).
    hillshade : np.ndarray   2-D hillshade (0-1).
    title : str, optional
    extent : list, optional
    alpha : float   Phase transparency (default 0.8).
    **kwargs   Passed to :func:`plot_data` for the phase layer.
    """
    ax.imshow(hillshade, cmap='gray', extent=extent, origin='upper', vmin=0, vmax=1)
    kwargs.setdefault('cmap', 'jet')
    kwargs.setdefault('vmin', -np.pi)
    kwargs.setdefault('vmax', np.pi)
    kwargs.setdefault('cbar_label', 'Phase (rad)')
    return plot_data(ax, phase, title=title, extent=extent, alpha=alpha, **kwargs)

def show_and_close(fig=None):
    """Display all figures and close them."""
    plt.show()
    plt.close('all')

def plot_phase_triple(ph1, ph2, ph3, title1, title2, title3,
                      ext1=None, figsize=(12, 3)):
    """Plot three phase panels (jet colormap) side by side."""

    fig, axes = plt.subplots(
        1, 3, figsize=figsize, constrained_layout=True,
    )
    plot_phase(axes[0], ph1, title=title1, cbar_label=None, extent=ext1)
    plot_phase(axes[1], ph2, title=title2, cbar_label=None)
    plot_phase(axes[2], ph3, title=title3)
    for ax in axes:
        set_ax_pixel(ax, axis='x')
    set_ax_pixel(axes[0], axis='y')
    return


def plot_unwrap_results(ifg_filt, unw, conncomp, gt_ml, epsg_utm, dem_path, figsize=(12,3)):
    """Plot wrapped and unwrapped phase over a DEM hillshade."""

    nrow, ncol = unw.shape
    extent_deg = extent_latlon(gt_ml, (nrow, ncol), epsg_utm)

    x0 = gt_ml[0]
    x1 = gt_ml[0] + ncol * gt_ml[1]
    y0 = gt_ml[3]
    y1 = gt_ml[3] + nrow * gt_ml[5]
    dem_ds = gdal.Warp('', str(dem_path), format='MEM',
                       dstSRS=f'EPSG:{epsg_utm}',
                       outputBounds=(x0, y1, x1, y0),
                       width=ncol, height=nrow,
                       resampleAlg='bilinear')
    dem = dem_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    dem_ds = None

    dy_grad, dx_grad = np.gradient(dem, np.abs(gt_ml[5]), np.abs(gt_ml[1]))
    slope = np.pi / 2.0 - np.arctan(np.hypot(dx_grad, dy_grad))
    aspect = np.arctan2(-dx_grad, dy_grad)
    az, alt = np.deg2rad(315.0), np.deg2rad(45.0)
    hillshade = (np.sin(alt) * np.sin(slope) +
                 np.cos(alt) * np.cos(slope) * np.cos(az - aspect))

    wrapped = np.where(np.abs(ifg_filt) == 0, np.nan, np.angle(ifg_filt))
    unw_m = np.where(np.abs(ifg_filt) == 0, np.nan, unw)
    vmax = np.nanmax(unw_m)
    vmin = np.nanmin(unw_m)
    vm = (vmax - vmin) / 2

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=figsize, sharex=True, sharey=True)
    plot_phase_over_hillshade(
        ax1, wrapped, hillshade, title='Wrapped phase', extent=extent_deg,
    )
    plot_phase_over_hillshade(
        ax2, unw_m, hillshade, title='Unwrapped phase', extent=extent_deg,
        vmax=vm, vmin=-vm, alpha=0.6,
    )
    plot_data(
        ax3, conncomp, title='Connected components', extent=extent_deg,
        cmap='tab10', cbar_label=' ', discrete=True,
    )

    plt.tight_layout()
    show_and_close()
    del dem, dx_grad, dy_grad, slope, aspect, hillshade, wrapped, unw_m
    return

def plot_pair(data1, data2, *,
              plot1=plot_data, kw1=None,
              plot2=plot_data, kw2=None,
              coord=None, epsg=None,
              xlabel1=None, ylabel1=None,
              xlabel2=None, ylabel2=None,
              figsize=(8, 3), tight_layout=True, suptitle=None):
    """Generic 1x2 panel plot (internal).

    Parameters
    ----------
    data1, data2 : np.ndarray
    plot1, plot2 : callable   Per-axis functions (plot_phase, plot_coherence, ...).
    kw1, kw2 : dict, optional   Keyword arguments for each plotter.
    coord : 'pixel', 'utm' or None   Coordinate axis formatting.
    epsg : int   Required when coord='utm'.
    xlabel1, ylabel1, xlabel2, ylabel2 : str, optional   Per-axis labels.
    figsize : tuple
    tight_layout : bool
    suptitle : str, optional
    """
    if data1.shape == data2.shape:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize, sharey=True)
    else:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize)
    k1 = kw1 if kw1 is not None else {}
    k2 = kw2 if kw2 is not None else {}
    plot1(ax1, data1, **k1)
    plot2(ax2, data2, **k2)
    if xlabel1 is not None:
        ax1.set_xlabel(xlabel1)
    if ylabel1 is not None:
        ax1.set_ylabel(ylabel1)
    if xlabel2 is not None:
        ax2.set_xlabel(xlabel2)
    if ylabel2 is not None:
        ax2.set_ylabel(ylabel2)
    if coord == 'pixel':
        set_ax_pixel(ax1)
        set_ax_pixel(ax2)
    elif coord == 'utm':
        set_ax_utm(ax1)
        set_ax_utm(ax2)
    if suptitle is not None:
        plt.suptitle(suptitle, fontsize=12, fontweight='bold')
    if tight_layout:
        plt.tight_layout(rect=[0, 0, 1, 0.95] if suptitle else None)
    show_and_close()

def plot_coregistration(safe_path, cslc_path, burst_id, date,
                        decimate=1):
    """2×2 diagnostic plot: raw SAFE vs geocoded CSLC amplitude & phase.

    Extracts the burst SLC from the SAFE product in radar coordinates
    and reads the geocoded CSLC from an OPERA HDF5 file, then renders
    four panels side by side so that amplitude structure and phase
    pattern are visually comparable before/after geocoding.

    Parameters
    ----------
    safe_path : str or Path
        Path to the Sentinel-1 SAFE directory or zip.
    cslc_path : str or Path
        Path to the OPERA CSLC HDF5 file.
    burst_id : str
        Burst identifier (e.g. ``'t124_264306_iw2'``).
    date : str
        Acquisition date (e.g. ``'20240623'``) — used in panel titles.
    decimate : int
        Stride for display decimation (default 5).  Set to 1 for
        full-resolution rendering (slow for large bursts).

    Notes
    -----
    -  Calls :func:`extract_burst_slc` (radar SLC) and :func:`h5py`
       (geocoded CSLC) internally; both must be importable.
    -  Amplitude is displayed in dB (20·log10).
    -  All panels use pixel-index axes via :func:`set_ax_pixel`.
    """
    # --- Left column: raw SAFE burst SLC (radar coordinates) ---
    rdr_slc = extract_burst_slc(safe_path, burst_id)
    rdr_amp = np.abs(rdr_slc)
    rdr_amp_db = 20 * np.log10(np.maximum(rdr_amp, 1e-6))
    rdr_phase = np.angle(rdr_slc.astype(np.complex64))
    print(f'  Radar SLC shape: {rdr_slc.shape}')

    # --- Right column: geocoded OPERA CSLC HDF5 amplitude ---
    with h5py.File(cslc_path) as f:
        geo_slc = f['/data/VV'][:]
    geo_amp = np.abs(geo_slc.astype(np.complex64))
    geo_amp_db = 20 * np.log10(np.maximum(geo_amp, 1e-6))
    geo_phase = np.angle(geo_slc.astype(np.complex64))

    # --- 2×2 panel plot ---
    fig, axes = plt.subplots(2, 1, figsize=(12, 6),
                             constrained_layout=True)

    plot_amplitude(axes[0], rdr_amp_db[::decimate, ::decimate],
                   title=f'{date}_{burst_id} amplitude in radar coord',
                   extent=extent_pixel(rdr_amp_db.shape))
    set_ax_pixel(axes[0])

    #plot_phase(axes[0, 1], rdr_phase[::decimate, ::decimate],
    #           title=f'{burst_id} ({date})  radar phase',
    #           extent=extent_pixel(rdr_phase.shape))
    #set_ax_pixel(axes[0, 1])

    plot_amplitude(axes[1], geo_amp_db[::decimate, ::decimate],
                   title=f'{date}_{burst_id} amplitude in geo coord',
                   extent=extent_pixel(geo_amp_db.shape))
    set_ax_pixel(axes[1])

    #plot_phase(axes[1, 1], geo_phase[::decimate, ::decimate],
    #           title=f'{burst_id} ({date})  geocoded phase',
    #           extent=extent_pixel(geo_phase.shape))
    #set_ax_pixel(axes[1, 1])

    #fig.suptitle('Raw SAFE (radar) vs Geocoded CSLC H5 (geo)',
    #             fontweight='bold')
    show_and_close()

# ===========================================================================
# 17. SARForge Pipeline: File-level Operations
# ===========================================================================

def get_file_geo_metadata(tif_path):
    """Read GDAL geotransform, projection, shape and EPSG from a GeoTIFF.

    Parameters
    ----------
    tif_path : str or Path

    Returns
    -------
    gt : tuple  GDAL geotransform (x0, dx, 0, y0, 0, dy).
    proj_wkt : str
    shape : tuple (rows, cols)
    epsg : int or None
    """
    ds = gdal.Open(str(tif_path))
    if ds is None:
        raise FileNotFoundError(f'Cannot open {tif_path}')
    gt = ds.GetGeoTransform()
    proj_wkt = ds.GetProjection()
    shape = (ds.RasterYSize, ds.RasterXSize)
    epsg = None
    if proj_wkt:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(proj_wkt)
        epsg = srs.GetAttrValue('AUTHORITY', 1)
        if epsg is not None:
            epsg = int(epsg)
    ds = None
    return gt, proj_wkt, shape, epsg

def _get_hdf5_geo_metadata(h5_path, subdataset='/data/VV'):
    """Read coordinate vectors and EPSG from an OPERA CSLC HDF5 file.

    Returns (x_coords, y_coords, epsg, shape).
    """
    with h5py.File(h5_path, 'r') as f:
        x = f['/data/x_coordinates'][:]
        y = f['/data/y_coordinates'][:]
        epsg = int(f['/data/projection'][()])
        shape = f[subdataset].shape
    return x, y, epsg, shape

def _crop_hdf5(h5_path, output_path, wsen_buf, subdataset, fill_nan):
    """Crop HDF5 file: read only the overlap region, write GeoTIFF."""
    try:
        x_coords, y_coords, epsg, shape = _get_hdf5_geo_metadata(
            h5_path, subdataset)
    except Exception as e:
        print(f'  ERROR reading HDF5 {h5_path}: {e}')
        return False

    # Transform wsen to native CRS
    if epsg and epsg != 4326:
        src_srs = osr.SpatialReference(); src_srs.ImportFromEPSG(4326)
        dst_srs = osr.SpatialReference(); dst_srs.ImportFromEPSG(int(epsg))
        t = osr.CoordinateTransformation(src_srs, dst_srs)
        corners = [
            t.TransformPoint(wsen_buf[1], wsen_buf[0]),  # SW  (lat,lon)
            t.TransformPoint(wsen_buf[3], wsen_buf[0]),  # NW
            t.TransformPoint(wsen_buf[3], wsen_buf[2]),  # NE
            t.TransformPoint(wsen_buf[1], wsen_buf[2]),  # SE
        ]
        native_W = min(c[0] for c in corners)
        native_E = max(c[0] for c in corners)
        native_S = min(c[1] for c in corners)
        native_N = max(c[1] for c in corners)
    else:
        native_W, native_S, native_E, native_N = wsen_buf

    # Find pixel range in native coordinates
    col_mask = (x_coords >= native_W) & (x_coords <= native_E)
    row_mask = (y_coords >= native_S) & (y_coords <= native_N)
    if not np.any(col_mask) or not np.any(row_mask):
        print(f'  skip (no overlap): {Path(h5_path).name}')
        return True

    cols = np.where(col_mask)[0]
    rows = np.where(row_mask)[0]
    c0, c1 = int(cols[0]), int(cols[-1]) + 1
    r0, r1 = int(rows[0]), int(rows[-1]) + 1

    # 1-pixel margin
    c0 = max(0, c0 - 1)
    c1 = min(len(x_coords), c1 + 1)
    r0 = max(0, r0 - 1)
    r1 = min(len(y_coords), r1 + 1)

    try:
        with h5py.File(h5_path, 'r') as f:
            data = f[subdataset][r0:r1, c0:c1]
    except Exception as e:
        print(f'  ERROR reading HDF5 subset {h5_path}: {e}')
        return False

    if fill_nan:
        if np.iscomplexobj(data):
            data = np.where(np.isnan(data), 0 + 0j, data)
        else:
            data = np.nan_to_num(data, nan=0)

    # Compute geotransform for subset
    sub_x = x_coords[c0:c1]
    sub_y = y_coords[r0:r1]
    x_res = abs(sub_x[1] - sub_x[0]) if len(sub_x) > 1 else 1.0
    dy_use = sub_y[1] - sub_y[0]     # preserve sign from coordinates
    y0_use = float(sub_y[0])

    gt = (float(sub_x[0]), x_res, 0, y0_use, 0, dy_use)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(epsg))
    proj_wkt = srs.ExportToWkt()

    save_tiff(output_path, data, gt, proj_wkt)
    return True

def _crop_geotiff(tif_path, output_path, wsen_buf):
    """Crop GeoTIFF file using GDAL Warp."""
    ds = gdal.Open(tif_path)
    if ds is None:
        print(f'  ERROR opening {tif_path}')
        return False

    # Get file extent in EPSG:4326
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    cols, rows = ds.RasterXSize, ds.RasterYSize

    # Compute file extent in native CRS
    f_W = gt[0]
    f_N = gt[3]
    f_E = f_W + gt[1] * cols
    f_S = f_N + gt[5] * rows

    if proj:
        src_srs = osr.SpatialReference(); src_srs.ImportFromWkt(proj)
        dst_srs = osr.SpatialReference(); dst_srs.ImportFromEPSG(4326)
        if not src_srs.IsSame(dst_srs):
            t = osr.CoordinateTransformation(src_srs, dst_srs)
            sw = t.TransformPoint(f_W, f_S)
            ne = t.TransformPoint(f_E, f_N)
            f_ext_W, f_ext_S = sw[1], sw[0]
            f_ext_E, f_ext_N = ne[1], ne[0]
        else:
            f_ext_W, f_ext_S, f_ext_E, f_ext_N = f_W, f_S, f_E, f_N
    else:
        f_ext_W, f_ext_S, f_ext_E, f_ext_N = f_W, f_S, f_E, f_N

    # Intersect
    inter_W = max(f_ext_W, wsen_buf[0])
    inter_S = max(f_ext_S, wsen_buf[1])
    inter_E = min(f_ext_E, wsen_buf[2])
    inter_N = min(f_ext_N, wsen_buf[3])

    if inter_W >= inter_E or inter_S >= inter_N:
        ds = None
        print(f'  skip (no overlap): {Path(tif_path).name}')
        return True

    warp_opts = gdal.WarpOptions(
        outputBounds=(inter_W, inter_S, inter_E, inter_N),
        outputBoundsSRS='EPSG:4326',
        dstSRS=proj if proj else None,
        format='GTiff',
        creationOptions=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'],
        resampleAlg='lanczos',
    )
    result = gdal.Warp(str(output_path), tif_path, options=warp_opts)
    ds = None
    if result is None:
        print(f'  ERROR warping {tif_path}')
        return False
    result = None
    return True

def crop_slc_single(input_file, output_file, wsen, buffer=0.0,
                    fill_nan=False, subdataset='/data/VV'):
    """Crop a single SLC file (HDF5 or GeoTIFF) to WSEN bounds + buffer.

    Reads only the overlapping region from the input and writes a compact
    GeoTIFF covering the intersection area.

    Parameters
    ----------
    input_file : str or Path
        Input file (HDF5 or GeoTIFF).
    output_file : str or Path
        Output cropped GeoTIFF.
    wsen : tuple
        (west, south, east, north) in EPSG:4326.
    buffer : float
        Buffer to add around wsen in degrees.
    fill_nan : bool
        Fill NaN values with 0.
    subdataset : str
        HDF5 subdataset path (for HDF5 inputs).

    Returns
    -------
    bool
        True on success, False on failure.
    """
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_file.exists():
        print(f'  skip (exists): {output_file.name}')
        return True

    # Apply buffer
    wsen_buf = (wsen[0] - buffer, wsen[1] - buffer,
                wsen[2] + buffer, wsen[3] + buffer)

    input_file_str = str(input_file)

    if input_file_str.endswith('.h5') or input_file_str.endswith('.hdf5'):
        return _crop_hdf5(input_file_str, str(output_file), wsen_buf,
                          subdataset, fill_nan)
    else:
        return _crop_geotiff(input_file_str, str(output_file), wsen_buf)

def generate_ifgram_pairs(slc_dir, output_dir, n_connections=3):
    """Generate sequential interferometric pair list from SLC directories.

    Scans subdirectories under *slc_dir* for YYYYMMDD-formatted names,
    generates sequential nearest-neighbor pairs, and writes
    ``ifgram_list.txt`` to *output_dir*.

    Parameters
    ----------
    slc_dir : str or Path
        Top-level directory containing per-date SLC subdirectories.
    output_dir : str or Path
        Output directory for the pair list file.
    n_connections : int
        Number of nearest-neighbor connections (default 3).

    Returns
    -------
    output_file : Path
        Path to the generated ``ifgram_list.txt``.
    """
    slc_dir = Path(slc_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect dates from directory names
    date_pattern = re.compile(r'^(\d{8})$')
    dates = set()
    for entry in sorted(slc_dir.iterdir()):
        if entry.is_dir():
            for sub in entry.iterdir():
                if sub.is_dir() and date_pattern.match(sub.name):
                    dates.add(sub.name)
        elif entry.is_dir() and date_pattern.match(entry.name):
            dates.add(entry.name)

    # Also check for .slc.tif files with dates
    if not dates:
        for root, dirs, files in os.walk(str(slc_dir)):
            for fname in files:
                m = re.search(r'(\d{8})', fname)
                if m:
                    dates.add(m.group(1))
            for dname in dirs:
                if date_pattern.match(dname):
                    dates.add(dname)

    dates = sorted(dates)
    print(f'Found {len(dates)} unique dates in {slc_dir}')

    # Generate sequential pairs
    pairs = []
    max_step = min(n_connections + 1, len(dates))
    for i in range(len(dates) - 1):
        for j in range(i + 1, min(i + max_step, len(dates))):
            pairs.append((dates[i], dates[j]))

    print(f'Generated {len(pairs)} pairs (n_connections={n_connections})')

    # Write output
    output_file = output_dir / 'ifgram_list.txt'
    with open(output_file, 'w') as f:
        f.write('# Interferometric pairs\n')
        f.write('# Date12\n')
        for d1, d2 in sorted(pairs):
            f.write(f'    {d1}-{d2}\n')

    print(f'Wrote {len(pairs)} pairs to {output_file}')
    return output_file

def form_single_ifgram(ref_slc, sec_slc, output_file):
    """Form a complex interferogram from two geocoded SLC GeoTIFFs.

    Since both SLCs are already geocoded and aligned to the same grid,
    this computes ``ref * conj(sec)`` directly.

    Parameters
    ----------
    ref_slc : str or Path
        Path to the reference SLC GeoTIFF.
    sec_slc : str or Path
        Path to the secondary SLC GeoTIFF.
    output_file : str or Path
        Path for the output complex interferogram (.int.tif).

    Returns
    -------
    output_file : Path or None
    """
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_file.exists():
        print(f'  skip (exists): {output_file.name}')
        return output_file

    ds_ref = gdal.Open(str(ref_slc))
    ds_sec = gdal.Open(str(sec_slc))
    if ds_ref is None or ds_sec is None:
        print(f'  ERROR: cannot open {ref_slc} or {sec_slc}')
        return None

    ref_arr = ds_ref.GetRasterBand(1).ReadAsArray()
    sec_arr = ds_sec.GetRasterBand(1).ReadAsArray()

    if ref_arr.shape != sec_arr.shape:
        if ds_ref.RasterXSize == ds_sec.RasterXSize and ds_ref.RasterYSize == ds_sec.RasterYSize:
            ref_arr = ref_arr.astype(np.complex64)
            sec_arr = sec_arr.astype(np.complex64)
        else:
            print(f'  ERROR: shape mismatch {ref_arr.shape} vs {sec_arr.shape}')
            return None

    ref_cx = ref_arr.astype(np.complex64)
    sec_cx = sec_arr.astype(np.complex64)

    ifg = ref_cx * np.conj(sec_cx)

    gt = ds_ref.GetGeoTransform()
    proj = ds_ref.GetProjection()

    drv = gdal.GetDriverByName('GTiff')
    out_ds = drv.Create(str(output_file), ds_ref.RasterXSize, ds_ref.RasterYSize,
                        1, gdal.GDT_CFloat32,
                        ['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'])
    out_ds.SetGeoTransform(gt)
    out_ds.SetProjection(proj)
    out_ds.GetRasterBand(1).WriteArray(ifg)
    out_ds = None
    ds_ref = None
    ds_sec = None

    print(f'  created: {output_file.name}')
    return output_file

def ifgram_and_coherence(ref_h5, sec_h5, burst_id, ifgram_dir,
                         coh_win=5):
    """Form a complex interferogram and compute complex coherence
    from two OPERA CSLC HDF5 files for a single burst.

    Reads geocoded complex SLC arrays from OPERA-format HDF5 files
    using :func:`read_cslc_array`, forms the interferogram via
    ``ref * conj(sec)``, estimates the complex coherence using an
    ``coh_win`` × ``coh_win`` sliding boxcar window, and saves both
    products as GeoTIFF files.

    The coherence uses the ISCE3-Crossmul formula at full resolution:
        coh = |boxcar_mean(ifg)| / sqrt(
                  boxcar_mean(|ref|^2) * boxcar_mean(|sec|^2))

    Parameters
    ----------
    ref_h5 : str or Path
        Path to the reference OPERA CSLC HDF5 file.
    sec_h5 : str or Path
        Path to the secondary OPERA CSLC HDF5 file.
    burst_id : str
        Burst identifier (e.g. ``'t124_264306_iw2'``).
    ifgram_dir : str or Path
        Top-level directory for interferogram outputs. Files are
        saved under ``{ifgram_dir}/{ref_ymd}_{sec_ymd}/``, where
        the dates (YYYYMMDD) are extracted from the paths of
        *ref_h5* and *sec_h5*.
    coh_win : int
        Sliding-window size for coherence estimation (default 5
        for a 5×5 boxcar window).

    Returns
    -------
    ifg_path : Path
        Path to the saved complex interferogram GeoTIFF
        (``.int.tif``).
    coh_path : Path
        Path to the saved coherence GeoTIFF (``.coh.tif``).

    Notes
    -----
    -  Invalid (NaN) pixels in either SLC are set to ``0+0j`` before
       cross-multiplication.
    -  Both SLCs are assumed to share the same UTM geotransform.
    -  The coherence raster is saved at **full resolution** (same
       shape as the interferogram) computed by a sliding ``uniform_filter``.
       For subsampled (non-overlapping block) coherence, use
       :func:`generate_phsig_coh_tif` or ISCE3's
       ``isce3.signal.multilook_averaged``.
    """

    ref_h5 = Path(ref_h5)
    sec_h5 = Path(sec_h5)

    # Extract dates from HDF5 paths (parent dir = YYYYMMDD)
    ref_ymd = ref_h5.parent.name
    sec_ymd = sec_h5.parent.name

    # Build output directory and file names
    pair_dir = Path(ifgram_dir) / f'{ref_ymd}_{sec_ymd}'
    pair_dir.mkdir(parents=True, exist_ok=True)
    ifg_path = pair_dir / f'{burst_id}.int.tif'
    coh_path = pair_dir / f'{burst_id}.coh.tif'

    # Skip if already processed
    if ifg_path.exists() and coh_path.exists():
        print(f'  skip (exists): {burst_id} {ref_ymd}_{sec_ymd}')
        return ifg_path, coh_path

    # Read SLC data (geocoded, complex64)
    ref_arr, gt, epsg, proj_wkt = read_cslc_array(str(ref_h5))
    sec_arr = read_cslc_array(str(sec_h5))[0]

    # Handle NaN / invalid pixels: replace with complex zero
    flag_nodata = ~np.isfinite(ref_arr) | ~np.isfinite(sec_arr)
    ref_arr[flag_nodata] = 0.0 + 0.0j
    sec_arr[flag_nodata] = 0.0 + 0.0j

    # Form complex interferogram: ifg = ref * conj(sec)
    ifg = ref_arr * np.conj(sec_arr)

    # Compute complex coherence (sliding boxcar, full resolution)
    win_area = coh_win * coh_win
    ref_pow = (np.abs(ref_arr) ** 2).astype(np.float32)
    sec_pow = (np.abs(sec_arr) ** 2).astype(np.float32)
    ifg_sum = ndimage.uniform_filter(ifg, size=coh_win, mode='constant') * win_area
    ref_sum = ndimage.uniform_filter(ref_pow, size=coh_win, mode='constant') * win_area
    sec_sum = ndimage.uniform_filter(sec_pow, size=coh_win, mode='constant') * win_area
    with np.errstate(invalid='ignore'):
        coh = np.abs(ifg_sum) / np.sqrt(ref_sum * sec_sum)
    coh = np.nan_to_num(coh, nan=0.0).clip(0.0, 1.0).astype(np.float32)

    # Save interferogram and coherence GeoTIFFs
    save_tiff(str(ifg_path), ifg, gt, proj_wkt,
              dtype=gdal.GDT_CFloat32)
    save_tiff(str(coh_path), coh, gt, proj_wkt,
              dtype=gdal.GDT_Float32)
    print(f'  created: {ifg_path.name}  {coh_path.name}')

    return ifg_path, coh_path

def stitch_ifgrams(burst_dir, out_bounds_wsen, output_dir,
                   file_ext='.int.tif'):
    """Stitch per-burst interferograms into unified products.

    Reads per-burst GeoTIFFs with GDAL, computes union extent using
    proper min/max, normalises dy convention, and passes to
    :func:`stitch_arrays`.

    Parameters
    ----------
    burst_dir : str or Path
        Directory containing per-burst subdirectories.
    out_bounds_wsen : tuple
        (west, south, east, north) in EPSG:4326.
    output_dir : str or Path
        Output directory for stitched files.
    file_ext : str
        File extension to collect (default '.int.tif').

    Returns
    -------
    ok : int
    """
    burst_dir = Path(burst_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    burst_pattern = re.compile(r'^t\d+_\d+_iw\d+$')
    bursts = sorted(
    (d for d in burst_dir.iterdir() if d.is_dir() and burst_pattern.match(d.name)),
    key=lambda p: (p.name.split('_')[-1], int(p.name.split('_')[1]))
    )

    if not bursts:
        print('No burst directories found.')
        return 0

    pair_files = {}
    for burst_path in bursts:
        for f in sorted(burst_path.glob(f'*{file_ext}')):
            base = f.name.replace(file_ext, '')
            if re.match(r'\d{8}_\d{8}', base):
                pair_files.setdefault(base, []).append(f)

    epsg_utm = 32605
    for _, files in pair_files.items():
        for fpath in files:
            _, _, _, epsg = get_file_geo_metadata(fpath)
            if epsg:
                epsg_utm = epsg
                break
        break

    ok = 0
    for pair_name, file_list in sorted(pair_files.items()):
        out_path = output_dir / f'{pair_name}{file_ext}'
        if out_path.exists():
            print(f'  skip (exists): {pair_name}')
            ok += 1
            continue

        # Read files, compute extent correctly regardless of dy sign
        pieces = []
        proj_wkt = None
        sample_dtype = None
        for fp in file_list:
            ds = gdal.Open(str(fp))
            if ds is None:
                continue
            arr = ds.GetRasterBand(1).ReadAsArray()
            if sample_dtype is None:
                sample_dtype = arr.dtype
            gt_in = ds.GetGeoTransform()
            if proj_wkt is None:
                proj_wkt = ds.GetProjection()
            ds = None

            x0, dx_p, _, y0, _, dy_p = gt_in
            x1 = x0 + arr.shape[1] * dx_p
            y1 = y0 + arr.shape[0] * dy_p
            pieces.append({
                'arr': arr, 'gt': gt_in,
                'x_min': min(x0, x1), 'x_max': max(x0, x1),
                'y_min': min(y0, y1), 'y_max': max(y0, y1),
                'dx': dx_p, 'dy': dy_p,
            })

        if not pieces:
            continue

        dx_use, dy_use = pieces[0]['dx'], pieces[0]['dy']

        # Union extent with proper min/max (matches sarforge)
        ulx = min(p['x_min'] for p in pieces)
        lrx = max(p['x_max'] for p in pieces)
        uly = max(p['y_max'] for p in pieces)
        lry = min(p['y_min'] for p in pieces)

        # Normalise arrays to dy<0, stitch_arrays expects standard convention
        arrays_list = []
        for p in pieces:
            arr = p['arr']
            gt = p['gt']
            if gt[5] > 0:
                arr = np.flipud(arr)
                x0_s, dx_s, _, y0_s, _, dy_s = gt
                nrows = arr.shape[0]
                y0_s = y0_s + (nrows - 1) * dy_s
                dy_s = -dy_s
                gt = (x0_s, dx_s, 0, y0_s, 0, dy_s)
            arrays_list.append((arr, gt, proj_wkt))

        try:
            stitched, out_gt, out_wkt = stitch_arrays(
                arrays_list, out_bounds_wsen,
                epsg_utm=epsg_utm)
            save_tiff(str(out_path), stitched, out_gt, out_wkt)
            print(f'  stitched: {pair_name} ({len(file_list)} bursts)')
            ok += 1
        except Exception as e:
            print(f'  ERROR stitching {pair_name}: {e}')

    print(f'Stitched {ok}/{len(pair_files)} pairs')
    return ok

def multilook_tif(input_tif, output_tif=None, lks_y=1, lks_x=1,
                  method='mean'):
    """Apply multilooking to a GDAL-readable GeoTIFF file.

    Parameters
    ----------
    input_tif : str or Path
        Path to input GeoTIFF.
    output_tif : str or Path, optional
        Output path. Auto-generated from input if None.
    lks_y : int
        Number of azimuth looks.
    lks_x : int
        Number of range looks.
    method : str
        'mean' or 'nearest'.

    Returns
    -------
    output_tif : Path or None
    """
    input_tif = Path(input_tif)

    if output_tif is None:
        output_tif = input_tif.parent / f'multilooked_{input_tif.name}'
    output_tif = Path(output_tif)

    if output_tif.exists():
        print(f'  skip (exists): {output_tif.name}')
        return output_tif

    output_tif.parent.mkdir(parents=True, exist_ok=True)

    ds = gdal.Open(str(input_tif))
    if ds is None:
        print(f'  ERROR opening {input_tif}')
        return None

    band_count = ds.RasterCount
    bands = [ds.GetRasterBand(i + 1).ReadAsArray() for i in range(band_count)]
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ds = None

    if lks_y * lks_x == 1:
        for b in range(band_count):
            save_tiff(str(output_tif), bands[b], gt, proj)
        return output_tif

    ml_bands = []
    for bdata in bands:
        nr, nc = bdata.shape
        nr = nr - nr % lks_y
        nc = nc - nc % lks_x
        if method == 'nearest':
            ml = bdata[int(lks_y/2)::lks_y, int(lks_x/2)::lks_x]
        else:
            ml = bdata[:nr, :nc].reshape(
                nr // lks_y, lks_y, nc // lks_x, lks_x).mean(axis=(1, 3))
        ml_bands.append(ml)

    new_gt = (gt[0], gt[1] * lks_x, gt[2], gt[3], gt[4], gt[5] * lks_y)

    if band_count == 1:
        save_tiff(str(output_tif), ml_bands[0], new_gt, proj)
    else:
        for b in range(band_count):
            save_tiff(str(output_tif), ml_bands[b], new_gt, proj)

    print(f'  multilooked: {input_tif.name} -> {output_tif.name}')
    return output_tif

def filter_tif(input_tif, output_tif=None, alpha=0.5, psize=32):
    """Apply Goldstein adaptive phase filter to a GeoTIFF interferogram.

    Parameters
    ----------
    input_tif : str or Path
        Path to complex interferogram GeoTIFF.
    output_tif : str or Path, optional
        Output path. Auto-generated if None.
    alpha : float
        Filter exponent [0, 1].
    psize : int
        FFT patch size.

    Returns
    -------
    output_tif : Path or None
    """
    input_tif = Path(input_tif)

    if output_tif is None:
        output_tif = input_tif.parent / f'filtered_{input_tif.name}'
    output_tif = Path(output_tif)

    if output_tif.exists():
        print(f'  skip (exists): {output_tif.name}')
        return output_tif

    output_tif.parent.mkdir(parents=True, exist_ok=True)

    ds = gdal.Open(str(input_tif))
    if ds is None:
        print(f'  ERROR opening {input_tif}')
        return None

    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.complex64)
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ds = None

    nodata = np.abs(arr) < 1e-6
    filtered = goldstein_filter(arr, alpha=alpha, psize=psize,
                                nodata_mask=nodata)

    save_tiff(str(output_tif), filtered, gt, proj)
    print(f'  filtered: {input_tif.name} -> {output_tif.name}')
    return output_tif

def generate_phsig_coh_tif(input_tif, output_tif=None, nlks=8):
    """Compute phase-sigma correlation from a complex interferogram TIF.

    Parameters
    ----------
    input_tif : str or Path
        Path to complex interferogram GeoTIFF.
    output_tif : str or Path, optional
        Output path. Auto-generated if None.
    nlks : float
        Number of looks for correlation conversion.

    Returns
    -------
    output_tif : Path or None
    """
    input_tif = Path(input_tif)

    if output_tif is None:
        base = input_tif.name.replace('.int.tif', '').replace('.int', '')
        for prefix in ['filtered_', 'multilooked_', 'filtered_multilooked_']:
            if base.startswith(prefix):
                base = base[len(prefix):]
        output_tif = input_tif.parent / f'{base}.phsig.coh.tif'
    output_tif = Path(output_tif)

    if output_tif.exists():
        print(f'  skip (exists): {output_tif.name}')
        return output_tif

    output_tif.parent.mkdir(parents=True, exist_ok=True)

    ds = gdal.Open(str(input_tif))
    if ds is None:
        print(f'  ERROR opening {input_tif}')
        return None

    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.complex64)
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ds = None

    coh = estimate_phsig_correlation(arr, nlks=nlks)

    save_tiff(str(output_tif), coh.astype(np.float32), gt, proj)
    print(f'  phsig coh: {input_tif.name} -> {output_tif.name}')
    return output_tif

# ===========================================================================
# 18. Unwrapping
# ===========================================================================

def unwrap_single_ifgram(ifg_file, corr_file, output_file,
                         nlooks=8, cost_mode='smooth',
                         init_method='mcf', water_mask=None):
    """Unwrap a single interferogram using snaphu-py.

    Parameters
    ----------
    ifg_file : str or Path
        Path to complex interferogram GeoTIFF.
    corr_file : str or Path
        Path to correlation/coherence GeoTIFF.
    output_file : str or Path
        Output path for unwrapped phase GeoTIFF.
    nlooks : int
        Number of looks.
    cost_mode, init_method : str
        SNAPHU parameters.
    water_mask : str or Path, optional
        Directory containing ``swbd_nasadem.wbd``.

    Returns
    -------
    output_file : Path or None
    """

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_file.exists():
        print(f'  skip (exists): {output_file.name}')
        return output_file

    # Read interferogram
    ds = gdal.Open(str(ifg_file))
    if ds is None:
        print(f'  ERROR opening {ifg_file}')
        return None
    ifg = ds.GetRasterBand(1).ReadAsArray().astype(np.complex64)
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    epsg = None
    if proj:
        srs_ifg = osr.SpatialReference(proj)
        epsg = int(srs_ifg.GetAttrValue('AUTHORITY', 1)) if srs_ifg.GetAttrValue('AUTHORITY', 1) else None
    ds = None

    # Read correlation
    ds = gdal.Open(str(corr_file))
    if ds is None:
        print(f'  ERROR opening {corr_file}')
        return None
    corr = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    ds = None

    # Build invalid pixel mask: zero-amplitude in interferogram
    invalid = np.abs(ifg) == 0

    # Apply water mask if available
    if water_mask is not None and epsg is not None:
        try:
            wbd_mask = load_water_mask(gt, ifg.shape, epsg,
                                       wbd_path=water_mask)
            if wbd_mask.shape == ifg.shape:
                invalid = invalid | wbd_mask
                wbd_pct = 100 * wbd_mask.sum() / wbd_mask.size
                print(f'  water mask: {wbd_pct:.1f}% of grid')
        except Exception as e:
            print(f'  WARNING: water mask failed ({e}), proceeding without')

    print(f'  masked pixels: {invalid.sum()} ({100*invalid.sum()/invalid.size:.1f}%)')

    # Zero out invalid regions in interferogram data
    ifg[invalid] = 0.0 + 0.0j

    # Unwrap (invalid regions are zeroed in igram, no separate mask needed)
    try:
        unw, conncomp = snaphu.unwrap(ifg, corr, nlooks=float(nlooks),
                                      cost=cost_mode, init=init_method)
    except Exception as e:
        print(f'  SNAPHU error for {Path(ifg_file).name}: {e}')
        return None

    # Zero out invalid regions in output
    unw[invalid] = 0.0
    conncomp[invalid] = 0

    save_tiff(str(output_file), unw.astype(np.float32), gt, proj)

    conncomp_name = str(output_file).replace('.unw.tif', '.unw.conncomp.tif')
    save_tiff(conncomp_name, conncomp.astype(np.uint16), gt, proj)

    print(f'  unwrapped: {Path(ifg_file).name}')
    return output_file

# ===========================================================================
# 19. Baseline Computation
# ===========================================================================

def compute_baselines_for_bursts(burst_ids, cslc_dir, output_base):
    """Compute perpendicular baselines using isce3 Rdr2Geo back-projection.

    Reads orbit state vectors and LUT grids from OPERA CSLC HDF5 output,
    back-projects the scene centre to ECEF using the reference burst's
    zero-Doppler geometry and the DEM, then computes ECEF baseline and
    its perpendicular/parallel components for each secondary acquisition.

    Parameters
    ----------
    slc_dir : str or Path
        Directory containing SAFE files (for date discovery; CSLC HDF5
        files are used for orbit and geometry data).
    burst_ids : list of str
        Burst identifiers.
    output_base : str or Path
        Base directory for output baseline text files.
    orbit_dir : str or Path, optional
        Unused (orbit is read from CSLC HDF5); kept for API compatibility.
    dem_path : str or Path, optional
        DEM GeoTIFF for height at back-projected target. Falls back to
        0 m (WGS-84 ellipsoid) if omitted.

    Returns
    -------
    ok : int
        Number of baseline pairs computed.
    """
    output_base = Path(output_base)
    total_ok = 0

    for burst_id in burst_ids:
        # --- Discover available dates from CSLC output ---
        cslc_ext = Path(cslc_dir) / burst_id
        if not cslc_ext.is_dir():
            print(f'  {burst_id}: CSLC directory not found: {cslc_ext}')
            continue

        # Only keep dates that have a CSLC HDF5 product
        date_dirs = []
        for d in sorted(cslc_ext.iterdir()):
            if not d.is_dir() or len(d.name) != 8 or not d.name.isdigit():
                continue
            h5_candidate = d / f'{burst_id}_{d.name}.h5'
            if h5_candidate.is_file():
                date_dirs.append(d)

        if len(date_dirs) < 2:
            print(f'  {burst_id}: {len(date_dirs)} dates with CSLC H5, need >= 2')
            continue

        ref_ymd = date_dirs[0].name
        ref_h5 = cslc_ext / ref_ymd / f'{burst_id}_{ref_ymd}.h5'

        # --- Prepare reference orbit, LUT grids, DEM ---
        ref_orbit, ref_t0 = load_orbit_from_h5(str(ref_h5))
        with h5py.File(str(ref_h5), 'r') as f:
            sr   = f['/metadata/processing_information/timing_corrections/slant_range'][:]
            azt  = f['/metadata/processing_information/timing_corrections/zero_doppler_time'][:]

        # Centre of the LUT grid (mid-pixel)
        i_c = len(azt) // 2
        j_c = len(sr) // 2
        sr_c = float(sr[j_c])
        azt_c = float(azt[i_c])

        # Rdr2Geo back-projection → (lon_rad, lat_rad, h_m)
        # Use WGS-84 ellipsoid (h=0) for quick baseline computation;
        # the parallax difference from actual DEM height is negligible
        # for perpendicular baseline at the kilometre scale.
        tgt_llh = isce3.geometry.rdr2geo(
            azt_c - ref_t0, sr_c, ref_orbit, isce3.core.LookSide.Right,
        )

        # Convert LLH (rad, rad, m) to ECEF (m)
        lon_rad, lat_rad, h_m = tgt_llh
        ellipsoid = isce3.core.Ellipsoid()
        ref_xyz = ellipsoid.lon_lat_to_xyz(np.array([lon_rad, lat_rad, h_m]))

        out_dir = output_base / burst_id
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f'\n  {burst_id} ({len(date_dirs)} dates, ref={ref_ymd})')

        # Get ref satellite position at the back-projected time
        ref_pos, ref_vel = ref_orbit.interpolate(azt_c - ref_t0)

        burst_ok = 0
        for sec_dir in date_dirs[1:]:
            sec_ymd = sec_dir.name
            sec_h5 = cslc_ext / sec_ymd / f'{burst_id}_{sec_ymd}.h5'
            if not sec_h5.is_file():
                continue

            sec_orbit, sec_t0 = load_orbit_from_h5(str(sec_h5))
            sec_pos, _ = sec_orbit.interpolate(azt_c - sec_t0)

            # Baseline vector in ECEF
            B = sec_pos - ref_pos
            B_sq = float(np.dot(B, B))
            B_par = float(np.dot(B, ref_vel) / np.linalg.norm(ref_vel))
            B_perp = float(np.sqrt(max(0.0, B_sq - B_par * B_par)))

            out_file = out_dir / f'{ref_ymd}_{sec_ymd}.txt'
            with open(out_file, 'w') as f:
                f.write(f'Bperp (m): {B_perp:.3f}\n')
                f.write(f'Bpar (m): {B_par:.3f}\n')

            print(f'    {ref_ymd}-{sec_ymd}: B_par={B_par:.3f}, B_perp={B_perp:.3f}')
            burst_ok += 1
            total_ok += 1

    print(f'\nBaseline computation done: {total_ok} pairs')
    return total_ok

def merge_baselines(baseline_dir, output_dir):
    """Merge per-burst baseline text files into MintPy-compatible format.

    Reads ``Bperp (m)`` / ``Bpar (m)`` from per-burst ``REFDATE_SECDATE.txt``
    files and merges them into a single baseline file per date pair.

    Parameters
    ----------
    baseline_dir : str or Path
        Directory containing per-burst baseline subdirectories.
    output_dir : str or Path
        Output directory for merged baseline files.

    Returns
    -------
    ok : int
        Number of merged pairs.
    """
    baseline_dir = Path(baseline_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    burst_pattern = re.compile(r'^t\d+_\d+_iw\d+$')
    burst_dirs = sorted(d for d in baseline_dir.iterdir()
                        if d.is_dir() and burst_pattern.match(d.name))

    if not burst_dirs:
        print('No burst baseline directories found.')
        return 0

    pair_data = {}
    for bd in burst_dirs:
        for f in bd.glob('*.txt'):
            name = f.stem
            m = re.match(r'(\d{8})_(\d{8})', name)
            if not m:
                continue
            pair_key = name
            try:
                with open(f) as fh:
                    d = {}
                    for line in fh:
                        line = line.strip()
                        if line.startswith('Bperp'):
                            d['Bperp'] = float(line.split(':')[1].strip())
                        elif line.startswith('Bpar'):
                            d['Bpar'] = float(line.split(':')[1].strip())
                pair_data.setdefault(pair_key, []).append(d)
            except Exception:
                continue

    ok = 0
    for pair_key, values in sorted(pair_data.items()):
        out_file = output_dir / f'{pair_key}.txt'
        if out_file.exists():
            print(f'  skip (exists): {pair_key}')
            ok += 1
            continue

        Bperp = np.mean([v['Bperp'] for v in values])
        Bpar = np.mean([v['Bpar'] for v in values])

        with open(out_file, 'w') as f:
            f.write(f'Bperp (m): {Bperp:.3f}\n')
            f.write(f'Bpar (m): {Bpar:.3f}\n')

        print(f'  merged: {pair_key} ({len(values)} bursts)')
        ok += 1

    print(f'Merged {ok}/{len(pair_data)} pairs')
    return ok
