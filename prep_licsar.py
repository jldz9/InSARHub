#!/usr/bin/env python3
"""Prepare LiCSAR interferogram products for MintPy time-series analysis.

LiCSAR product files expected per interferogram directory:
    geo_unw.tif   - unwrapped phase (radians)
    geo_cc.tif    - coherence (0-1)
    geo_dem.tif   - DEM (meters, ellipsoidal)
    geo_inc.tif   - incidence angle (radians, from vertical)
    geo_azi.tif   - azimuth angle (radians, from north, clockwise)
    geo_mask.tif  - water/land mask (optional, 0=water 1=land)

Usage:
    python prep_licsar.py -f './interferograms/*/geo_unw.tif'
    python prep_licsar.py -f './interferograms/*/geo_*.tif'

Output:
    A .rsc sidecar file next to each .tif, readable by MintPy's load_data.py
"""

import argparse
import os
import re
from pathlib import Path

from mintpy.constants import SPEED_OF_LIGHT
from mintpy.objects import sensor
from mintpy.utils import readfile, utils1 as ut, writefile


# LiCSAR filename → MintPy dataset name and unit
LICSAR_FILE_MAP = {
    'geo_unw':  {'dataset': 'unwrapPhase',    'unit': 'radian'},
    'geo_cc':   {'dataset': 'coherence',      'unit': '1'},
    'geo_dem':  {'dataset': 'height',         'unit': 'm'},
    'geo_inc':  {'dataset': 'incidenceAngle', 'unit': 'radian'},
    'geo_azi':  {'dataset': 'azimuthAngle',   'unit': 'radian'},
    'geo_mask': {'dataset': 'waterMask',      'unit': '1'},
}


def parse_licsar_dates(ifg_dir: str) -> tuple[str, str]:
    """Parse reference and secondary dates from LiCSAR interferogram directory name.

    LiCSAR directory naming convention: YYYYMMDD_YYYYMMDD
    e.g. 20200101_20200113

    Args:
        ifg_dir (str): Path to interferogram directory.

    Returns:
        tuple[str, str]: (date1, date2) as 'YYMMDD' strings for MintPy DATE12.

    Raises:
        ValueError: If the directory name does not match the expected date pattern.
    """
    dirname = Path(ifg_dir).name
    match = re.search(r'(\d{8})_(\d{8})', dirname)
    if not match:
        raise ValueError(
            f"Cannot parse dates from directory '{dirname}'. "
            "Expected format: YYYYMMDD_YYYYMMDD"
        )
    date1 = match.group(1)  # YYYYMMDD
    date2 = match.group(2)
    # MintPy DATE12 uses YYMMDD-YYMMDD
    return date1[2:], date2[2:]


def identify_licsar_file(fname: str) -> dict | None:
    """Identify a LiCSAR file type by its base name.

    Args:
        fname (str): Path to the LiCSAR GeoTIFF file.

    Returns:
        dict with 'dataset' and 'unit' keys, or None if not recognized.
    """
    stem = Path(fname).stem  # e.g. 'geo_unw', 'geo_cc'
    return LICSAR_FILE_MAP.get(stem)


def add_licsar_metadata(fname: str, meta: dict) -> dict:
    """Add LiCSAR-specific metadata to an existing GDAL metadata dictionary.

    Extracts dates from the parent directory name, sets unit and dataset name,
    and fills required MintPy metadata fields for Sentinel-1.

    Args:
        fname (str): Path to the LiCSAR GeoTIFF file.
        meta (dict): Existing metadata from readfile.read_gdal_vrt().

    Returns:
        dict: Updated metadata dictionary ready to write as .rsc sidecar.
    """
    file_info = identify_licsar_file(fname)
    if file_info is None:
        raise ValueError(f"Unrecognized LiCSAR file: {fname}")

    # ── dataset type and unit ──────────────────────────────────────────────────
    meta['UNIT'] = file_info['unit']
    meta['PROCESSOR'] = 'licsar'

    # ── Sentinel-1 hardcoded values (same as prep_hyp3) ───────────────────────
    meta['PLATFORM'] = 'Sen'
    meta['ANTENNA_SIDE'] = -1
    meta['WAVELENGTH'] = SPEED_OF_LIGHT / sensor.SEN['carrier_frequency']

    # LiCSAR default looks (typically 4 range × 1 azimuth for standard products)
    meta.setdefault('RLOOKS', '4')
    meta.setdefault('ALOOKS', '1')
    meta['RANGE_PIXEL_SIZE'] = (
        sensor.SEN['range_pixel_size'] * int(meta['RLOOKS'])
    )
    meta['AZIMUTH_PIXEL_SIZE'] = (
        sensor.SEN['azimuth_pixel_size'] * int(meta['ALOOKS'])
    )

    # ── orbit direction from heading ──────────────────────────────────────────
    # LiCSAR does not provide a metadata text file; derive from geometry.
    # X_STEP > 0 → ascending, X_STEP can be used as a proxy if HEADING absent.
    if 'HEADING' not in meta:
        # Sentinel-1 descending heading ≈ -168°, ascending ≈ -12°
        # Most LiCSAR products are ascending; set a default and let user override.
        meta['HEADING'] = '-168.0'  # descending default; change if ascending

    heading = float(meta['HEADING'])
    meta['ORBIT_DIRECTION'] = 'ASCENDING' if abs(heading) < 90 else 'DESCENDING'

    # ── corner coordinates ────────────────────────────────────────────────────
    N = float(meta['Y_FIRST'])
    W = float(meta['X_FIRST'])
    S = N + float(meta['Y_STEP']) * int(meta['LENGTH'])
    E = W + float(meta['X_STEP']) * int(meta['WIDTH'])

    if meta['ORBIT_DIRECTION'] == 'ASCENDING':
        meta.update({
            'LAT_REF1': str(S), 'LAT_REF2': str(S),
            'LAT_REF3': str(N), 'LAT_REF4': str(N),
            'LON_REF1': str(W), 'LON_REF2': str(E),
            'LON_REF3': str(W), 'LON_REF4': str(E),
        })
    else:
        meta.update({
            'LAT_REF1': str(N), 'LAT_REF2': str(N),
            'LAT_REF3': str(S), 'LAT_REF4': str(S),
            'LON_REF1': str(E), 'LON_REF2': str(W),
            'LON_REF3': str(E), 'LON_REF4': str(W),
        })

    # ── interferogram-specific metadata ───────────────────────────────────────
    is_ifg = file_info['dataset'] in ('unwrapPhase', 'coherence')
    if is_ifg:
        ifg_dir = os.path.dirname(fname)
        date1, date2 = parse_licsar_dates(ifg_dir)
        meta['DATE12'] = f'{date1}-{date2}'
        # LiCSAR does not provide baseline in file metadata; set placeholder.
        # Update with actual values from baseline file if available.
        meta.setdefault('P_BASELINE_TOP_HDR',    '0.0')
        meta.setdefault('P_BASELINE_BOTTOM_HDR', '0.0')

    return meta


def prep_licsar(inps: argparse.Namespace) -> None:
    """Generate MintPy-compatible .rsc sidecar files for LiCSAR products.

    Args:
        inps: Parsed arguments with attribute `file` (list of GeoTIFF paths).
    """
    inps.file = ut.get_file_list(inps.file, abspath=True)

    if not inps.file:
        raise FileNotFoundError(
            "No files found. Check the -f glob pattern.\n"
            "Example: -f './interferograms/*/geo_unw.tif'"
        )

    for fname in inps.file:
        file_info = identify_licsar_file(fname)
        if file_info is None:
            print(f"[skip] Unrecognized file: {fname}")
            continue

        print(f"[prep] {fname}")
        meta = readfile.read_gdal_vrt(fname)
        meta = add_licsar_metadata(fname, meta)

        rsc_file = fname + '.rsc'
        writefile.write_roipac_rsc(meta, out_file=rsc_file)
        print(f"  → wrote {rsc_file}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Prepare LiCSAR interferogram products for MintPy.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '-f', '--file',
        nargs='+',
        required=True,
        help="LiCSAR GeoTIFF file(s) or glob pattern. "
             "Example: './interferograms/*/geo_unw.tif'",
    )
    return parser


if __name__ == '__main__':
    parser = create_parser()
    inps = parser.parse_args()
    prep_licsar(inps)
