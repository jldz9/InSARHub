
from pathlib import Path

import geopandas as gpd
import h5py
import numpy as np
import rasterio
import re

from rasterio.features import shapes
from rasterio.transform import from_origin
from rasterio.crs import CRS
from shapely.geometry import shape, Polygon, MultiPolygon, box
from shapely.ops import unary_union


def _transform_from_attrs(attrs):
    A = {k.upper(): attrs[k] for k in attrs.keys()}
    x0, y0 = float(A["X_FIRST"]), float(A["Y_FIRST"])
    dx, dy = float(A["X_STEP"]), float(A["Y_STEP"])
    T = from_origin(x0, y0, abs(dx), abs(dy))
    # force north-up (negative y pixel size)
    return rasterio.Affine(T.a, 0, T.c, 0, -abs(dy), T.f)

def _crs_from_attrs(attrs):
    A = {k.upper(): attrs[k] for k in attrs.keys()}
    if "EPSG" in A:
        try: return CRS.from_epsg(int(A["EPSG"]))
        except: pass
    if "UTM_ZONE" in A:
        if bool(re.fullmatch(r'\d+[A-Za-z]', A["UTM_ZONE"])): # e.g., '33N'
            zone_str = A["UTM_ZONE"][-1]
            zone_num = int(A["UTM_ZONE"][:-1])
            if zone_str.upper() == 'N':
                epsg = 32600 + zone_num
            else:
                epsg = 32700 + zone_num
            return CRS.from_epsg(epsg)
        elif bool(re.fullmatch(r'\d+', str(A["UTM_ZONE"]))): # e.g., 33
            zone = int(A["UTM_ZONE"])
            lat = None
            for k in ("REF_LAT","LAT_REF1","LAT_REF2","LAT_REF3","LAT_REF4"):
                if k in A:
                    try: lat = float(A[k]); break
                    except: pass
            north = (lat is None) or (lat >= 0.0)
            epsg = 32600 + zone if north else 32700 + zone
            return CRS.from_epsg(epsg)
    return None

def _unit_from_attrs(attrs):
    for k in ("UNIT","UNIT_TYPE","units","Units"):
        if k in attrs: return str(attrs[k])
    return None

def h5_to_raster(
    h5_file: str | Path,
    out_raster: str | Path | None = None,
):
    """
    Convert a HDF5 dataset from mintpy to GeoTIFF raster.

    Parameters:
    - h5_file: Path to the input HDF5 file.
    - out_raster: Path to the output GeoTIFF file.
    """
    h5_file = Path(h5_file).expanduser().resolve()
    if out_raster is None:
        out_raster = h5_file.parent.joinpath(f"{h5_file.stem}.tif")
    else:
        out_raster = Path(out_raster).expanduser().resolve()

    valid_names = {
        'ERA5', 'geometryGeo', 'ifgramStack', 'velocity', 'velocityERA5',
        'avgSpatialCoh', 'demErr', 'maskConnComp', 'maskTempCoh',
        'numInvIfgram', 'temporalCoherence', 'timeseries', 'timeseriesResidual',
    }
    if h5_file.stem not in valid_names:
        raise ValueError(f"HDF5 file name '{h5_file.stem}' not recognised. Valid names: {sorted(valid_names)}")

    NODATA = -9999.0

    def _write_band(data: np.ndarray, out_path: Path, crs, transform, dtype="float32",
                    nodata=NODATA, tags: dict | None = None):
        data = data.astype(dtype)
        height, width = data.shape
        profile = dict(
            driver="GTiff", height=height, width=width, count=1,
            dtype=dtype, crs=crs, transform=transform, nodata=nodata,
            tiled=True, compress="deflate", predictor=2 if dtype in ("uint8", "int16") else 3,
            blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER",
        )
        with rasterio.open(out_path.as_posix(), "w", **profile) as dst:
            dst.write(data, 1)
            if tags:
                dst.update_tags(**tags)
        print(f"  → {out_path.name}")

    def _write_multiband(data: np.ndarray, out_path: Path, crs, transform,
                         dtype="float32", nodata=NODATA, band_names: list[str] | None = None,
                         tags: dict | None = None):
        """Write 3-D array (bands, height, width) as multi-band GeoTIFF."""
        data = data.astype(dtype)
        n, height, width = data.shape
        profile = dict(
            driver="GTiff", height=height, width=width, count=n,
            dtype=dtype, crs=crs, transform=transform, nodata=nodata,
            tiled=True, compress="deflate", predictor=3,
            blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER",
        )
        with rasterio.open(out_path.as_posix(), "w", **profile) as dst:
            dst.write(data)
            if band_names:
                for i, name in enumerate(band_names, 1):
                    dst.update_tags(i, date=name)
            if tags:
                dst.update_tags(**tags)
        print(f"  → {out_path.name} ({n} bands)")

    with h5py.File(h5_file.as_posix(), "r") as f:
        attrs = f.attrs
        transform = _transform_from_attrs(attrs)
        crs = _crs_from_attrs(attrs)
        unit = _unit_from_attrs(attrs)
        if crs is None or transform is None:
            raise ValueError("Cannot extract CRS or Transform from HDF5 file attributes.")

        no_data_val = float(attrs.get("NO_DATA_VALUE", np.nan))
        base_tags = {"source": h5_file.name}
        if unit:
            base_tags["units"] = unit

        def _clean(data: np.ndarray) -> np.ndarray:
            data = data.copy().astype(np.float32)
            if np.isfinite(no_data_val):
                data[data == no_data_val] = NODATA
            data[~np.isfinite(data)] = NODATA
            return data

        stem = h5_file.stem

        # ── velocity / velocityERA5 ─────────────────────────────────────────
        if stem in ("velocity", "velocityERA5"):
            for key in ("velocity", "velocityStd", "residue"):
                if key not in f:
                    continue
                data = _clean(f[key][()])
                out_path = out_raster.with_name(f"{out_raster.stem}_{key}.tif")
                _write_band(data, out_path, crs, transform, tags={**base_tags, "dataset": key})

        # ── timeseries / timeseriesResidual ─────────────────────────────────
        elif stem in ("timeseries", "timeseriesResidual"):
            key = stem
            if key not in f:
                raise ValueError(f"Dataset '{key}' not found in {h5_file.name}")
            data = _clean(f[key][()])          # shape: (n_dates, height, width)
            dates = [d.decode() if isinstance(d, bytes) else str(d)
                     for d in f.get("date", [])]
            _write_multiband(data, out_raster, crs, transform,
                             band_names=dates or None, tags=base_tags)

        # ── scalar 2-D float datasets ────────────────────────────────────────
        elif stem in ("avgSpatialCoh", "temporalCoherence", "demErr", "numInvIfgram"):
            key_map = {
                "avgSpatialCoh":      "avgSpatialCoh",
                "temporalCoherence":  "temporalCoherence",
                "demErr":             "dem_error",
                "numInvIfgram":       "numInvIfgram",
            }
            key = key_map[stem]
            if key not in f:
                # fall back: first dataset in file
                key = next(iter(f))
            data = _clean(f[key][()])
            _write_band(data, out_raster, crs, transform, tags={**base_tags, "dataset": key})

        # ── mask datasets (uint8) ────────────────────────────────────────────
        elif stem in ("maskTempCoh", "maskConnComp"):
            key = stem
            if key not in f:
                key = next(iter(f))
            data = f[key][()].astype(np.uint8)
            _write_band(data, out_raster, crs, transform,
                        dtype="uint8", nodata=255, tags={**base_tags, "dataset": key})

        # ── unknown / unimplemented ──────────────────────────────────────────
        else:
            raise ValueError(f"Conversion of '{stem}' is not yet supported.")


def save_footprint(raster_file: str | Path, out_footprint: str | Path | None = None):
    """
    Save the footprint of a raster file as a binary mask GeoTIFF.

    Parameters:
    - raster_file: Path to the input raster file.
    - out_footprint: Path to the output footprint GeoTIFF file.
    """
    raster_file = Path(raster_file).expanduser().resolve()
    if out_footprint is None:
        out_footprint = raster_file.with_name(f"{raster_file.stem}_footprint.shp")
    else:
        out_footprint = Path(out_footprint).expanduser().resolve()

    with rasterio.open(raster_file.as_posix(), 'r') as src:
        data = src.read(1)
        crs = src.crs
        transform = src.transform
        count = src.count

        if src.nodata is not None:
            b1 = src.read(1)
            valid_mask = b1 != src.nodata
        else: 
            b1 = src.read(1)
            valid_mask = np.isfinite(b1)

        footprint = []
        for geom, val in shapes(valid_mask.astype(np.uint8), mask=valid_mask, transform=transform):
            if val == 1:
                 poly = shape(geom)
                 if poly.area >0:
                     footprint.append(poly)

        if not footprint:
            footprint_geom = box(*src.bounds)
        else:
            footprint_geom = unary_union(footprint).buffer(0)

    gdf = gpd.GeoDataFrame({"source": [raster_file.name]}, geometry=[footprint_geom], crs=crs)
    gdf.to_file(out_footprint.as_posix())
    gdf.to_file(out_footprint.with_suffix('.gpkg'), layer='raster_footprint', driver="GPKG")

    