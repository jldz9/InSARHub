# -*- coding: utf-8 -*-
"""TIF rendering, MintPy velocity/timeseries, and interferogram list endpoints."""

import json
import zipfile as _zipfile
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi.responses import Response as _Resp
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling

router = APIRouter()

_TS_PRIORITY = [
    'timeseries_ERA5_ramp_demErr.h5',
    'timeseries_ERA5_ramp.h5',
    'timeseries_ERA5_demErr.h5',
    'timeseries_ERA5.h5',
    'timeseriesResidual_ramp.h5',
    'timeseriesResidual.h5',
    'timeseries.h5',
]


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _rgba_to_png_bytes(rgba) -> bytes:
    try:
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.fromarray(rgba, 'RGBA').save(buf, format='PNG', optimize=False, compress_level=1)
        return buf.getvalue()
    except ImportError:
        pass
    import struct, zlib
    h, w = rgba.shape[:2]
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    raw  = b''.join(b'\x00' + bytes(row) for row in rgba)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', ihdr) + _chunk(b'IDAT', zlib.compress(raw, 6)) + _chunk(b'IEND', b'')


def _colormap_numpy(data, mask, vmin: float, vmax: float, type_name: str):
    rng = vmax - vmin or 1.0
    t   = np.where(mask, 0.0, np.clip((data - vmin) / rng, 0.0, 1.0))
    h, w = data.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    if type_name == 'unw_phase':
        hd  = (t * 360.0) % 360.0
        hi  = (hd / 60).astype(np.int32) % 6
        f   = hd / 60.0 - np.floor(hd / 60.0)
        r = np.select([hi==0,hi==1,hi==2,hi==3,hi==4,hi==5],[1,1-f,0,0,f,1])
        g = np.select([hi==0,hi==1,hi==2,hi==3,hi==4,hi==5],[f,1,1,f,0,0])
        b = np.select([hi==0,hi==1,hi==2,hi==3,hi==4,hi==5],[0,0,f,1,1,1-f])
        rgba[:,:,0] = (r*255).astype(np.uint8)
        rgba[:,:,1] = (g*255).astype(np.uint8)
        rgba[:,:,2] = (b*255).astype(np.uint8)
    elif type_name == 'corr':
        v = (t*255).astype(np.uint8)
        rgba[:,:,0] = v; rgba[:,:,1] = v; rgba[:,:,2] = v
    elif type_name == 'velocity':
        # Diverging blue-white-red: negative LOS → blue, zero → white, positive → red
        stops_t = np.array([0.0, 0.5, 1.0])
        rgba[:,:,0] = np.interp(t, stops_t, [0,   255, 220]).astype(np.uint8)
        rgba[:,:,1] = np.interp(t, stops_t, [0,   255, 0  ]).astype(np.uint8)
        rgba[:,:,2] = np.interp(t, stops_t, [220, 255, 0  ]).astype(np.uint8)
    else:
        stops_t = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
        rgba[:,:,0] = np.interp(t, stops_t, [68,  59,  33,  94,  253]).astype(np.uint8)
        rgba[:,:,1] = np.interp(t, stops_t, [1,   82,  145, 201, 231]).astype(np.uint8)
        rgba[:,:,2] = np.interp(t, stops_t, [84,  139, 140, 98,  37 ]).astype(np.uint8)
    rgba[:,:,3] = np.where(mask, 0, 255).astype(np.uint8)
    return rgba


def _tif_file_type(stem: str) -> str:
    for token in ("unw_phase", "corr", "dem", "lv_theta", "lv_phi", "water_mask",
                  "inc_map", "los_disp", "wrapped_phase", "browse"):
        if token in stem:
            return token
    return stem.split("_")[-1]


def _tif_bounds_wgs84(zip_path: str, tif_name: str) -> list | None:
    try:
        try:
            import rasterio
            from rasterio.warp import transform_bounds
            with rasterio.open(f"/vsizip/{zip_path}/{tif_name}") as src:
                return list(transform_bounds(src.crs, "EPSG:4326", *src.bounds))
        except ImportError:
            from osgeo import gdal, osr
            ds = gdal.Open(f"/vsizip/{zip_path}/{tif_name}")
            if ds is None:
                return None
            gt = ds.GetGeoTransform()
            cols, rows = ds.RasterXSize, ds.RasterYSize
            src_srs = osr.SpatialReference()
            src_srs.ImportFromWkt(ds.GetProjection())
            tgt_srs = osr.SpatialReference()
            tgt_srs.ImportFromEPSG(4326)
            tgt_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            ct = osr.CoordinateTransformation(src_srs, tgt_srs)
            corners = [(gt[0], gt[3]), (gt[0] + cols * gt[1], gt[3]),
                       (gt[0] + cols * gt[1], gt[3] + rows * gt[5]),
                       (gt[0], gt[3] + rows * gt[5])]
            lons, lats = [], []
            for x, y in corners:
                pt = ct.TransformPoint(x, y)
                lons.append(pt[0]); lats.append(pt[1])
            ds = None
            return [min(lons), min(lats), max(lons), max(lats)]
    except Exception:
        return None


def _mintpy_attr_val(attrs, key):
    v = attrs[key]
    if isinstance(v, (bytes, bytearray)):
        return v.decode().strip()
    if isinstance(v, np.ndarray):
        v = v.flat[0]
        if isinstance(v, (bytes, bytearray, np.bytes_)):
            return v.decode().strip() if hasattr(v, 'decode') else str(v)
        return v.item() if hasattr(v, 'item') else float(v)
    if hasattr(v, 'item'):
        return v.item()
    return v


def _mintpy_epsg(attrs) -> int:
    import re
    if 'EPSG' in attrs:
        try:
            return int(float(str(_mintpy_attr_val(attrs, 'EPSG')).strip()))
        except Exception:
            pass
    for key in ('UTM_ZONE', 'utmZone', 'utm_zone'):
        if key in attrs:
            s = str(_mintpy_attr_val(attrs, key)).strip().upper()
            m = re.match(r'(\d+)([NS]?)', s)
            if m:
                zone = int(m.group(1))
                hemi = m.group(2) or 'N'
                return (32600 if hemi == 'N' else 32700) + zone
    raise ValueError(
        'Projected coordinates detected (X_FIRST out of ±360° range) '
        'but no EPSG or UTM_ZONE attribute found in the HDF5 file.'
    )


def _mintpy_bounds(attrs) -> list:
    x_first = float(_mintpy_attr_val(attrs, 'X_FIRST'))
    y_first = float(_mintpy_attr_val(attrs, 'Y_FIRST'))
    x_step  = float(_mintpy_attr_val(attrs, 'X_STEP'))
    y_step  = float(_mintpy_attr_val(attrs, 'Y_STEP'))
    width   = int(float(str(_mintpy_attr_val(attrs, 'WIDTH')).strip()))
    length  = int(float(str(_mintpy_attr_val(attrs, 'LENGTH')).strip()))
    half_x = 0.5 * abs(x_step)
    half_y = 0.5 * abs(y_step)
    x_center_last = x_first + x_step * (width  - 1)
    y_center_last = y_first + y_step * (length - 1)
    west  = min(x_first, x_center_last) - half_x
    east  = max(x_first, x_center_last) + half_x
    south = min(y_first, y_center_last) - half_y
    north = max(y_first, y_center_last) + half_y
    if abs(x_first) > 360 or abs(y_first) > 90:
        epsg = _mintpy_epsg(attrs)
        tf = Transformer.from_crs(epsg, 4326, always_xy=True)
        xs, ys = tf.transform([west, east, west, east],
                               [south, south, north, north])
        return [min(xs), min(ys), max(xs), max(ys)]
    return [west, south, east, north]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/render-coh-map")
async def render_coh_map(path: str, season: str, pol: str = "vv", band: int = 1):
    """Render one band of a coherence decay map GeoTIFF as a map overlay.

    Parameters
    ----------
    path   : absolute folder path (same as the quality pipeline folder)
    season : "winter" | "spring" | "summer" | "fall"
    pol    : "vv" (default) or "vh"
    band   : 1 = γ∞ (PS floor, 0–1), 2 = γ0 (initial coherence, 0–1),
             3 = τ (decorrelation time, days)

    Returns
    -------
    JSON with keys: png_b64, pixel_b64, bounds, pixel_width, pixel_height,
    nodata, type, vmin, vmax  (same format as /api/render-tif)
    """
    import base64

    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {path}")

    tif_path = folder / "decay_maps" / f"S1_coherence_decay_{season}_{pol}.tif"
    if not tif_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Decay map not found: {tif_path.name}. Run pair-quality first.",
        )

    if band not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="band must be 1, 2, or 3")

    _BAND_LABELS = {1: "γ∞ PS floor", 2: "γ0 initial coh", 3: "τ decay days"}
    _BAND_TYPES  = {1: "corr",        2: "corr",          3: "default"}

    try:
        import rasterio
        from rasterio.warp import transform_bounds

        with rasterio.open(tif_path) as src:
            data_raw = src.read(band).astype(np.float32)
            nodata   = float(src.nodata) if src.nodata is not None else -9999.0
            bounds_native = src.bounds
            bounds_wgs84  = transform_bounds(src.crs, "EPSG:4326", *bounds_native)
            H, W = src.height, src.width

        mask = data_raw == nodata
        valid = data_raw[~mask]
        if valid.size == 0:
            raise HTTPException(status_code=422, detail="No valid pixels in band")

        # Robust vmin/vmax — clip at 2nd/98th percentile to remove outliers
        vmin = float(np.percentile(valid, 2))
        vmax = float(np.percentile(valid, 98))
        if vmin >= vmax:
            vmin, vmax = float(valid.min()), float(valid.max())

        type_name = _BAND_TYPES[band]
        rgba = _colormap_numpy(data_raw, mask, vmin, vmax, type_name)

        # Upsample PNG for crisp display — native data is ~1km/pixel so a
        # typical 100×100 tile needs 8-10× zoom before it fills the viewport.
        # We upsample to at least 512px on the longest side using nearest-
        # neighbor (np.repeat) so the raster looks sharp, not blurry.
        _MIN_DIM = 512
        up = max(1, _MIN_DIM // max(H, W))
        if up > 1:
            rgba_display = np.repeat(np.repeat(rgba, up, axis=0), up, axis=1)
        else:
            rgba_display = rgba

        png_bytes = _rgba_to_png_bytes(rgba_display)
        png_b64   = base64.b64encode(png_bytes).decode()

        # Float32 pixel buffer at native resolution for accurate hover values
        pixel_buf = data_raw.tobytes()
        pixel_b64 = base64.b64encode(pixel_buf).decode()

        return {
            "png_b64":      png_b64,
            "pixel_b64":    pixel_b64,
            "bounds":       list(bounds_wgs84),
            "pixel_width":  W,
            "pixel_height": H,
            "nodata":       nodata,
            "type":         type_name,
            "label":        _BAND_LABELS[band],
            "vmin":         round(vmin, 4),
            "vmax":         round(vmax, 4),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/folder-ifg-list")
async def folder_ifg_list(path: str):
    """List interferogram zip files in a folder with per-file types and WGS84 bounds."""
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    search_roots: list[Path] = [folder]
    expected_names: list[str] = []

    cache_file = folder / ".insarhub_cache.json"
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
            expected_names = cache.get("filenames", [])
            out_dir = cache.get("out_dir")
            if out_dir:
                p = Path(out_dir)
                if p.exists() and p not in search_roots:
                    search_roots.append(p)
        except Exception:
            pass
    else:
        for job_file in sorted(folder.glob("hyp3*.json")):
            if job_file.name == ".insarhub_cache.json":
                continue
            try:
                data = json.loads(job_file.read_text())
                out_dir = data.get("out_dir")
                if out_dir:
                    p = Path(out_dir)
                    if p.exists() and p not in search_roots:
                        search_roots.append(p)
            except Exception:
                pass

    seen: set[str] = set()
    pairs = []

    def _process_zip(zip_path: Path):
        k = str(zip_path)
        if k in seen:
            return
        seen.add(k)
        try:
            with _zipfile.ZipFile(zip_path) as zf:
                all_names = zf.namelist()
                tif_names = sorted([n for n in all_names if n.endswith(".tif") and not n.endswith("/")])
                if not tif_names:
                    return
                bounds = _tif_bounds_wgs84(k, tif_names[0])
                files = [{"filename": t, "type": _tif_file_type(Path(t).stem)}
                         for t in tif_names]
                pairs.append({"name": zip_path.stem, "zip": k,
                              "files": files, "bounds": bounds})
        except Exception:
            pass

    if expected_names:
        for root in search_roots:
            for name in expected_names:
                candidate = root / name
                if candidate.exists():
                    _process_zip(candidate)
            if not pairs:
                for name in expected_names:
                    for found in root.rglob(name):
                        _process_zip(found)
    else:
        for root in search_roots:
            for zip_path in sorted(root.glob("*.zip")):
                _process_zip(zip_path)
            if not pairs:
                for zip_path in sorted(root.rglob("*.zip")):
                    _process_zip(zip_path)

    return {"pairs": pairs}


@router.get("/api/serve-tif")
async def serve_tif(zip: str, file: str):
    """Serve a TIF file extracted from a zip archive."""
    try:
        with _zipfile.ZipFile(zip) as zf:
            data = zf.read(file)
            return _Resp(content=data, media_type="image/tiff",
                         headers={"Cache-Control": "no-store",
                                  "Content-Disposition": f"inline; filename={Path(file).name}"})
    except KeyError:
        raise HTTPException(status_code=404, detail=f"'{file}' not in archive")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/render-tif")
async def render_tif_colored(zip: str, file: str, type_hint: str = ""):
    """Server-side render a TIF to colored PNG + downsampled float32 for hover."""
    import base64

    MAX_PIXEL   = 256

    try:
        with _zipfile.ZipFile(zip) as zf:
            tif_bytes = zf.read(file)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        import rasterio
        from rasterio.warp import transform_bounds
        from rasterio.io import MemoryFile

        with MemoryFile(tif_bytes) as memf:
            with memf.open() as src:
                orig_h, orig_w = src.height, src.width
                dh = orig_h
                dw = orig_w
                nodata_val = src.nodata
                bounds_wgs84 = list(transform_bounds(src.crs, "EPSG:4326", *src.bounds))

                # Reproject to Web Mercator (EPSG:3857) — same approach as
                # render-velocity.  Maplibre stretches the image between 4
                # corner points in Mercator space, so pixels must be Mercator-
                # aligned; reprojecting to geographic (4326) still causes a
                # slight shift at most latitudes.
                raw = src.read(1, out_shape=(dh, dw)).astype(np.float32)
                if nodata_val is not None:
                    raw[raw == float(nodata_val)] = np.nan
                raw = np.where(np.isfinite(raw), raw, np.nan)

                src_tf = from_bounds(src.bounds.left, src.bounds.bottom,
                                     src.bounds.right, src.bounds.top, dw, dh)

                # WGS84 envelope of the source raster
                w84_w, w84_s, w84_e, w84_n = bounds_wgs84

                # Project that WGS84 envelope into Mercator
                tf_to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
                merc_w, merc_s = tf_to_merc.transform(w84_w, w84_s)
                merc_e, merc_n = tf_to_merc.transform(w84_e, w84_n)

                dst_tf = from_bounds(merc_w, merc_s, merc_e, merc_n, dw, dh)
                disp_data = np.full((dh, dw), np.nan, dtype=np.float32)
                reproject(
                    source=raw, destination=disp_data,
                    src_transform=src_tf, src_crs=src.crs,
                    dst_transform=dst_tf, dst_crs=CRS.from_epsg(3857),
                    resampling=Resampling.bilinear,
                    src_nodata=np.nan, dst_nodata=np.nan,
                )

                # Convert Mercator bounds back to WGS84 for Maplibre corners
                tf_to_wgs = Transformer.from_crs(3857, 4326, always_xy=True)
                wgs_w, wgs_s = tf_to_wgs.transform(merc_w, merc_s)
                wgs_e, wgs_n = tf_to_wgs.transform(merc_e, merc_n)
                bounds_wgs84 = [wgs_w, wgs_s, wgs_e, wgs_n]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"rasterio error: {e}")

    mask = ~np.isfinite(disp_data)
    if nodata_val is not None:
        mask |= (disp_data == float(nodata_val))

    valid = disp_data[~mask]
    vmin = float(valid.min()) if valid.size else 0.0
    vmax = float(valid.max()) if valid.size else 1.0

    type_name = type_hint or _tif_file_type(Path(file).stem)
    rgba = _colormap_numpy(disp_data, mask, vmin, vmax, type_name)
    png_bytes = _rgba_to_png_bytes(rgba)
    png_b64   = base64.b64encode(png_bytes).decode()

    scale_p = min(1.0, MAX_PIXEL / max(dh, dw))
    ph = max(1, int(dh * scale_p))
    pw = max(1, int(dw * scale_p))
    row_idx = (np.arange(ph) * dh / ph).astype(int)
    col_idx = (np.arange(pw) * dw / pw).astype(int)
    pix_data = disp_data[np.ix_(row_idx, col_idx)]
    pixel_b64 = base64.b64encode(pix_data.astype(np.float32).tobytes()).decode()

    return {
        "png_b64":      png_b64,
        "pixel_b64":    pixel_b64,
        "bounds":       bounds_wgs84,
        "vmin":         vmin,
        "vmax":         vmax,
        "nodata":       nodata_val,
        "type":         type_name,
        "width":        orig_w,
        "height":       orig_h,
        "pixel_width":  pw,
        "pixel_height": ph,
    }


@router.get("/api/mintpy-check")
async def mintpy_check(path: str):
    """Return whether velocity.h5 exists and list all available timeseries*.h5 files."""
    folder = Path(path).expanduser().resolve()
    has_velocity = (folder / 'velocity.h5').exists()
    ts_files = [n for n in _TS_PRIORITY if (folder / n).exists()]
    has_overview = (folder / 'numTriNonzeroIntAmbiguity.h5').exists()
    has_network  = (folder / 'coherenceSpatialAvg.txt').exists()
    return {"has_velocity": has_velocity, "timeseries_files": ts_files,
            "has_overview": has_overview, "has_network": has_network}


@router.get("/api/mintpy-network-data")
async def mintpy_network_data(path: str):
    """Read coherenceSpatialAvg.txt and return nodes+pairs in folder-network-data format."""
    folder = Path(path).expanduser().resolve()
    coh_txt = folder / 'coherenceSpatialAvg.txt'
    if not coh_txt.exists():
        raise HTTPException(status_code=404, detail='coherenceSpatialAvg.txt not found')

    pairs, coherences, bperps = [], [], []
    with coh_txt.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            date12, coh, btemp, bperp = parts[0], float(parts[1]), float(parts[2]), float(parts[3])
            ref_d, sec_d = date12.split('_')
            pairs.append((ref_d, sec_d))
            coherences.append(coh)
            bperps.append(bperp)

    # Derive per-scene bperp: set earliest scene = 0, propagate via pairs
    all_dates = sorted({d for p in pairs for d in p})
    scene_bperp: dict[str, float] = {all_dates[0]: 0.0}
    # Simple iterative propagation (handles DAGs)
    changed = True
    while changed:
        changed = False
        for (ref_d, sec_d), bp in zip(pairs, bperps):
            if ref_d in scene_bperp and sec_d not in scene_bperp:
                scene_bperp[sec_d] = scene_bperp[ref_d] + bp
                changed = True
            elif sec_d in scene_bperp and ref_d not in scene_bperp:
                scene_bperp[ref_d] = scene_bperp[sec_d] - bp
                changed = True
    # Fallback: any unresolved scenes get 0
    for d in all_dates:
        scene_bperp.setdefault(d, 0.0)

    nodes = [{"id": d, "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "bperp": round(scene_bperp[d], 2)}
             for d in all_dates]
    pair_list = [[ref_d, sec_d] for ref_d, sec_d in pairs]
    coh_map = {f"{ref_d}_{sec_d}": round(coh, 4) for (ref_d, sec_d), coh in zip(pairs, coherences)}

    # Read current dropIfgram state from ifgramStack.h5
    dropped_pairs: list[str] = []
    ifgram_h5 = folder / 'inputs' / 'ifgramStack.h5'
    if ifgram_h5.exists():
        try:
            import h5py
            with h5py.File(ifgram_h5, 'r') as hf:
                drop_flags = hf['dropIfgram'][:]
                dates_h5   = hf['date'][:]          # shape (N, 2), bytes
            for flag, row in zip(drop_flags, dates_h5):
                if not flag:  # False = dropped (True = keep)
                    d12 = f"{row[0].decode()}_{row[1].decode()}"
                    dropped_pairs.append(d12)
        except Exception:
            pass

    return {"stacks": {"mintpy_network": {"nodes": nodes, "pairs": pair_list}},
            "coherence": coh_map,
            "dropped_pairs": dropped_pairs}


class MintpySaveRequest(BaseModel):
    folder_path: str
    active_pairs: list[str]   # list of "YYYYMMDD_YYYYMMDD" that should be KEPT


@router.post("/api/mintpy-save-network")
async def mintpy_save_network(req: MintpySaveRequest):
    """Persist network edits as modify_network config in insarhub_config.json.

    Computes the dropped pairs (all pairs in ifgramStack.h5 minus active_pairs)
    and writes them to network_excludeDate12 in the analyzer config so that the
    next modify_network run re-applies the exclusions from config rather than
    relying on a direct HDF5 edit.
    """
    from insarhub.app.state import read_insarhub_config, write_insarhub_config

    folder = Path(req.folder_path).expanduser().resolve()
    ifgram_h5 = folder / 'inputs' / 'ifgramStack.h5'
    if not ifgram_h5.exists():
        raise HTTPException(status_code=404, detail='inputs/ifgramStack.h5 not found')

    try:
        import h5py
        with h5py.File(ifgram_h5, 'r') as hf:
            dates_h5 = hf['date'][:]  # shape (N, 2), bytes
        all_pairs = [f"{row[0].decode()}_{row[1].decode()}" for row in dates_h5]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    active_set  = set(req.active_pairs)
    dropped     = [d12 for d12 in all_pairs if d12 not in active_set]
    excluded_str = " ".join(dropped) if dropped else "auto"

    try:
        cfg = read_insarhub_config(folder)
        az_cfg = cfg.get("analyzer", {}).get("config", {})
        az_cfg["network_excludeDate12"] = excluded_str
        write_insarhub_config(folder, {"analyzer": {"config": az_cfg}})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Config write failed: {exc}")

    return {
        "excluded": len(dropped),
        "total":    len(all_pairs),
        "network_excludeDate12": excluded_str,
    }


@router.get("/api/render-velocity")
async def render_velocity(path: str):
    """Render velocity.h5 → colored PNG + float32 pixel array for hover."""
    import base64

    MAX_PIXEL = 256

    vel_path = Path(path).expanduser().resolve() / 'velocity.h5'
    if not vel_path.exists():
        raise HTTPException(status_code=404, detail='velocity.h5 not found')
    try:
        import h5py

        with h5py.File(vel_path, 'r') as f:
            ds   = f['velocity']
            data = ds[:].astype(np.float32)
            attrs = {k: v for k, v in f.attrs.items()}
            attrs.update({k: v for k, v in ds.attrs.items()})
        if data.ndim == 3:
            data = data[0]
        orig_h, orig_w = data.shape

        x_first = float(_mintpy_attr_val(attrs, 'X_FIRST'))
        y_first = float(_mintpy_attr_val(attrs, 'Y_FIRST'))
        x_step  = float(_mintpy_attr_val(attrs, 'X_STEP'))
        y_step  = float(_mintpy_attr_val(attrs, 'Y_STEP'))

        is_projected = abs(x_first) > 360 or abs(y_first) > 90
        src_epsg = _mintpy_epsg(attrs) if is_projected else 4326
        src_crs = CRS.from_epsg(src_epsg)
        dst_crs = CRS.from_epsg(3857)

        half_x = 0.5 * abs(x_step)
        half_y = 0.5 * abs(y_step)
        src_west  = x_first - half_x
        src_east  = x_first + x_step * (orig_w - 1) + half_x
        src_north = y_first + half_y
        src_south = y_first + y_step * (orig_h - 1) - half_y

        src_tf = from_bounds(src_west, src_south, src_east, src_north, orig_w, orig_h)

        if is_projected:
            tf_src_to_wgs = Transformer.from_crs(src_epsg, 4326, always_xy=True)
            xs, ys = tf_src_to_wgs.transform(
                [src_west, src_east, src_west, src_east],
                [src_south, src_south, src_north, src_north],
            )
            west, south, east, north = min(xs), min(ys), max(xs), max(ys)
        else:
            west, south, east, north = src_west, src_south, src_east, src_north

        tf_to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
        merc_w, merc_s = tf_to_merc.transform(west, south)
        merc_e, merc_n = tf_to_merc.transform(east, north)
        dst_w, dst_h = orig_w, orig_h
        dst_tf = from_bounds(merc_w, merc_s, merc_e, merc_n, dst_w, dst_h)

        src_data = np.where(np.isfinite(data) & (data != 0), data, np.nan)
        dst_data = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
        reproject(
            source=src_data,
            destination=dst_data,
            src_transform=src_tf,
            src_crs=src_crs,
            dst_transform=dst_tf,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
            src_nodata=np.nan,
            dst_nodata=np.nan,
        )

        tf_to_wgs = Transformer.from_crs(3857, 4326, always_xy=True)
        wgs_w, wgs_s = tf_to_wgs.transform(merc_w, merc_s)
        wgs_e, wgs_n = tf_to_wgs.transform(merc_e, merc_n)
        bounds = [wgs_w, wgs_s, wgs_e, wgs_n]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Processing error: {str(e)}')

    data = dst_data
    mask = ~np.isfinite(data) | (data == 0)

    valid_v = data[~mask]
    if valid_v.size > 0:
        abs_max = float(np.percentile(np.abs(valid_v), 98))
        vmax =  max(abs_max, 1e-6)
        vmin = -vmax
    else:
        vmin, vmax = -0.1, 0.1
    rgba = _colormap_numpy(data, mask, vmin, vmax, 'velocity')
    png_bytes = _rgba_to_png_bytes(rgba)
    png_b64 = base64.b64encode(png_bytes).decode()

    scale_p = min(1.0, MAX_PIXEL / max(dst_h, dst_w))
    ph, pw = max(1, int(dst_h * scale_p)), max(1, int(dst_w * scale_p))
    row_idx = (np.arange(ph) * dst_h / ph).astype(int)
    col_idx = (np.arange(pw) * dst_w / pw).astype(int)
    pix_data = data[np.ix_(row_idx, col_idx)]
    pixel_b64 = base64.b64encode(pix_data.astype(np.float32).tobytes()).decode()

    unit = str(attrs.get('UNIT', 'm/year'))
    return {
        'png_b64': png_b64,
        'pixel_b64': pixel_b64,
        'bounds': bounds,
        'vmin': vmin,
        'vmax': vmax,
        'width': dst_w,
        'height': dst_h,
        'pixel_width': pw,
        'pixel_height': ph,
        'unit': unit,
        'label': f'Velocity ({unit})'
    }


# ── MintPy diagnostic file renderer ──────────────────────────────────────────

_DIAG_META = {
    'avgSpatialCoh': {
        'file':  'avgSpatialCoh.h5',
        'cmap':  'viridis',
        'label': 'Avg Spatial Coherence',
        'vmin':  0.0, 'vmax': 1.0,
    },
    'avgPhaseVelocity': {
        'file':  'avgPhaseVelocity.h5',
        'cmap':  'velocity',
        'label': 'Avg Phase Velocity (rad/yr)',
        'vmin':  None, 'vmax': None,
    },
    'numTriNonzeroIntAmbiguity': {
        'file':  'numTriNonzeroIntAmbiguity.h5',
        'cmap':  'viridis',
        'label': 'Unwrapping Error Count',
        'vmin':  0.0, 'vmax': None,
    },
    'maskConnComp': {
        'file':  'maskConnComp.h5',
        'cmap':  'viridis',
        'label': 'Connected Component Mask',
        'vmin':  0.0, 'vmax': 1.0,
    },
}


@router.get("/api/render-mintpy-diag")
async def render_mintpy_diag(path: str, name: str):
    """Render a MintPy diagnostic HDF5 file to PNG + float32 pixel array."""
    import base64

    MAX_PIXEL = 1024

    if name not in _DIAG_META:
        raise HTTPException(status_code=400, detail=f"Unknown diagnostic: {name}. Choose from {list(_DIAG_META)}")

    meta     = _DIAG_META[name]
    h5_path  = Path(path).expanduser().resolve() / meta['file']
    if not h5_path.exists():
        raise HTTPException(status_code=404, detail=f"{meta['file']} not found")

    try:
        import h5py

        with h5py.File(h5_path, 'r') as f:
            # Auto-detect first 2-D dataset
            ds_name = next(
                (k for k in f.keys() if isinstance(f[k], h5py.Dataset) and f[k].ndim >= 2),
                None
            )
            if ds_name is None:
                raise ValueError(f"No 2-D dataset found in {h5_path.name}. Keys: {list(f.keys())}")
            ds    = f[ds_name]
            data  = ds[:].astype(np.float32)
            attrs = {k: v for k, v in f.attrs.items()}
            attrs.update({k: v for k, v in ds.attrs.items()})

        if data.ndim == 3:
            data = data[0]
        orig_h, orig_w = data.shape

        x_first = float(_mintpy_attr_val(attrs, 'X_FIRST'))
        y_first = float(_mintpy_attr_val(attrs, 'Y_FIRST'))
        x_step  = float(_mintpy_attr_val(attrs, 'X_STEP'))
        y_step  = float(_mintpy_attr_val(attrs, 'Y_STEP'))

        is_projected = abs(x_first) > 360 or abs(y_first) > 90
        src_epsg = _mintpy_epsg(attrs) if is_projected else 4326
        src_crs  = CRS.from_epsg(src_epsg)
        dst_crs  = CRS.from_epsg(3857)

        half_x    = 0.5 * abs(x_step)
        half_y    = 0.5 * abs(y_step)
        src_west  = x_first - half_x
        src_east  = x_first + x_step  * (orig_w - 1) + half_x
        src_north = y_first + half_y
        src_south = y_first + y_step  * (orig_h - 1) - half_y

        src_tf = from_bounds(src_west, src_south, src_east, src_north, orig_w, orig_h)

        if is_projected:
            tf_src_to_wgs = Transformer.from_crs(src_epsg, 4326, always_xy=True)
            xs, ys = tf_src_to_wgs.transform(
                [src_west, src_east, src_west, src_east],
                [src_south, src_south, src_north, src_north],
            )
            west, south, east, north = min(xs), min(ys), max(xs), max(ys)
        else:
            west, south, east, north = src_west, src_south, src_east, src_north

        tf_to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
        merc_w, merc_s = tf_to_merc.transform(west,  south)
        merc_e, merc_n = tf_to_merc.transform(east,  north)

        # Upsample to at least 1024px on the long side for a crisp map overlay
        _TARGET = 1024
        scale   = max(1.0, _TARGET / max(orig_w, orig_h))
        dst_w, dst_h = int(orig_w * scale), int(orig_h * scale)
        dst_tf = from_bounds(merc_w, merc_s, merc_e, merc_n, dst_w, dst_h)

        mask_val = 0.0 if name == 'maskConnComp' else np.nan
        src_data = np.where(np.isfinite(data), data, mask_val)
        dst_data = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
        reproject(
            source=src_data, destination=dst_data,
            src_transform=src_tf, src_crs=src_crs,
            dst_transform=dst_tf, dst_crs=dst_crs,
            resampling=Resampling.nearest,
            src_nodata=mask_val, dst_nodata=np.nan,
        )

        tf_to_wgs  = Transformer.from_crs(3857, 4326, always_xy=True)
        wgs_w, wgs_s = tf_to_wgs.transform(merc_w, merc_s)
        wgs_e, wgs_n = tf_to_wgs.transform(merc_e, merc_n)
        bounds = [wgs_w, wgs_s, wgs_e, wgs_n]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Processing error: {e}')

    data = dst_data
    mask = ~np.isfinite(data)

    vmin = meta['vmin'] if meta['vmin'] is not None else float(np.nanmin(data)) if np.any(~mask) else 0.0
    vmax = meta['vmax'] if meta['vmax'] is not None else float(np.nanmax(data)) if np.any(~mask) else 1.0

    rgba      = _colormap_numpy(data, mask, vmin, vmax, meta['cmap'])
    png_bytes = _rgba_to_png_bytes(rgba)
    png_b64   = base64.b64encode(png_bytes).decode()

    scale_p = min(1.0, MAX_PIXEL / max(dst_h, dst_w))
    ph, pw  = max(1, int(dst_h * scale_p)), max(1, int(dst_w * scale_p))
    row_idx = (np.arange(ph) * dst_h / ph).astype(int)
    col_idx = (np.arange(pw) * dst_w / pw).astype(int)
    pix_data   = data[np.ix_(row_idx, col_idx)]
    pixel_b64  = base64.b64encode(pix_data.astype(np.float32).tobytes()).decode()

    return {
        'png_b64':      png_b64,
        'pixel_b64':    pixel_b64,
        'bounds':       bounds,
        'vmin':         vmin,
        'vmax':         vmax,
        'width':        dst_w,
        'height':       dst_h,
        'pixel_width':  pw,
        'pixel_height': ph,
        'label':        meta['label'],
    }


@router.get("/api/timeseries-pixel")
async def timeseries_pixel(path: str, lat: float, lon: float, ts_file: str | None = None):
    """Extract a single pixel time series without loading the full 3-D stack."""
    folder = Path(path).expanduser().resolve()
    if ts_file:
        ts_name = ts_file if (folder / ts_file).exists() else None
    else:
        ts_name = next((n for n in _TS_PRIORITY if (folder / n).exists()), None)
    if ts_name is None:
        raise HTTPException(status_code=404, detail='No timeseries file found')
    try:
        import h5py
        with h5py.File(folder / ts_name, 'r') as f:
            ds_ts = f['timeseries']
            attrs = {k: v for k, v in f.attrs.items()}
            attrs.update({k: v for k, v in ds_ts.attrs.items()})
            raw_dates = f['date'][:]
            x_first = float(_mintpy_attr_val(attrs, 'X_FIRST'))
            y_first = float(_mintpy_attr_val(attrs, 'Y_FIRST'))
            x_step  = float(_mintpy_attr_val(attrs, 'X_STEP'))
            y_step  = float(_mintpy_attr_val(attrs, 'Y_STEP'))
            width   = int(float(str(_mintpy_attr_val(attrs, 'WIDTH')).strip()))
            length  = int(float(str(_mintpy_attr_val(attrs, 'LENGTH')).strip()))
            query_x, query_y = lon, lat
            if abs(x_first) > 360 or abs(y_first) > 90:
                epsg = _mintpy_epsg(attrs)
                tf = Transformer.from_crs(4326, epsg, always_xy=True)
                query_x, query_y = tf.transform(lon, lat)
            col = max(0, min(int(round((query_x - x_first) / x_step)), width  - 1))
            row = max(0, min(int(round((query_y - y_first) / y_step)), length - 1))
            values = [float(v) for v in ds_ts[:, row, col]]
        def _decode_date(d):
            s = d.decode() if isinstance(d, (bytes, bytearray)) else str(d)
            return s.strip()
        dates     = [_decode_date(d) for d in raw_dates]
        # Keep values and dates in sync — drop any entry whose date string is too short
        date_value_pairs = [(d, v) for d, v in zip(dates, values) if len(d) >= 8]
        iso_dates = [f'{d[:4]}-{d[4:6]}-{d[6:8]}' for d, _ in date_value_pairs]
        values    = [v for _, v in date_value_pairs]
        unit      = str(_mintpy_attr_val(attrs, 'UNIT')) if 'UNIT' in attrs else 'm'
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f'Missing geo-attribute: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'h5py error: {e}')

    return {'dates': iso_dates, 'values': values, 'file': ts_name, 'unit': unit}
