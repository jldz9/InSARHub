# File Structure

InSARHub writes a consistent set of files to disk as the pipeline progresses. Each stage adds files to the working directory — the same folder is reused from pair selection through analysis, so you can always tell how far a folder has been processed by which files are present.

---

## Directory Layout

**Single-stack (flat) run** — when only one track/frame is found, all files are written directly into `workdir/`:

```
workdir/
├── insarhub_config.json               # pipeline config (accumulates each stage)
├── stack_p0_f0.json                   # pairs, baselines, scenes, quality scores
├── network_p0_f0.png                  # interferogram network graph image
├── dem_p0_f0.tif                      # DEM raster (after downloader dem)
├── hyp3_jobs.json                     # submitted job IDs  (after processor submit)
├── hyp3_retry_jobs_*.json             # retry batches      (after processor retry)
├── .mintpy.cfg                        # MintPy config written by InSARHub (after analyzer init)
├── .insarhub_cache.json               # processor result cache (filenames + out_dir)
├── .insarhub_quality_cache.json       # weather, snow, landcover, coherence feature cache
├── .insarhub_pair_quality_db.json     # pre-scored quality for all N×(N-1)/2 scene pairs
├── decay_maps/                        # S1 coherence pixel decay GeoTIFFs (one per season)
│   ├── S1_coherence_decay_winter_vv.tif
│   ├── S1_coherence_decay_spring_vv.tif
│   ├── S1_coherence_decay_summer_vv.tif
│   └── S1_coherence_decay_fall_vv.tif
├── tmp/                               # extracted zip contents  (removed by cleanup)
└── clip/                              # AOI-clipped data        (removed by cleanup)
```

**Multi-stack run** — when the search covers more than one track/frame, each group gets its own `p{path}_f{frame}/` subfolder. Each subfolder contains exactly the same file structure as the single-stack layout above; nothing is written to the top-level workdir.

```
workdir/
├── p100_f466/                    # one subfolder per track/frame group
│   ├── insarhub_config.json
│   ├── stack_p100_f466.json
│   ├── .insarhub_quality_cache.json
│   ├── .insarhub_pair_quality_db.json
│   ├── decay_maps/
│   └── ...                       # same structure as single-stack
├── p93_f121/
│   └── ...                       # same structure as single-stack
```

MintPy inputs (`inputs/ifgramStack.h5`, etc.) and outputs (`timeseries*.h5`, `velocity.h5`, `velocity.tif`, etc.) are all written by MintPy and are not listed here. See the [MintPy documentation](https://mintpy.readthedocs.io) for details.

---

## Files by Stage

### Stage 1 — Pair Selection

Produced by `insarhub downloader --select-pairs` or GUI **Select Pairs**.

For multi-stack runs, all files go inside `p{path}_f{frame}/` subfolders. For single-stack (flat) runs, they go directly in `workdir/`.

| File | Description |
|------|-------------|
| `insarhub_config.json` | Downloader type and settings (written into each group folder, or workdir for flat runs) |
| `stack_p{path}_f{frame}.json` | Selected pairs, perpendicular baselines, scene list, and pair quality scores |
| `network_p{path}_f{frame}.png` | Interferogram network graph — nodes are scenes, edges are pairs, colored by quality score |
| `.insarhub_quality_cache.json` | Weather, snow, and coherence data fetched during pair scoring |
| `.insarhub_pair_quality_db.json` | Pre-scored quality for all N×(N−1)/2 scene combinations (built in background) |
| `decay_maps/` | S1 global coherence pixel decay GeoTIFFs cached from AWS S3 (one per season) |

> **Legacy:** older versions also wrote `downloader_config.json` per group folder. This is superseded by `insarhub_config.json` and can be ignored if present.

### Stage 1b — DEM Download

Produced by `insarhub downloader dem`. Optional — only needed if a local DEM is required for co-registration.

| File | Description |
|------|-------------|
| `dem_p{path}_f{frame}.tif` | Merged and reprojected DEM raster covering the stack AOI |

### Stage 2 — Job Submission

Produced by `insarhub processor submit` or GUI **Process**.

| File | Description |
|------|-------------|
| `insarhub_config.json` | Updated with processor type and settings (in each group folder) |
| `hyp3_jobs.json` | HyP3 job IDs grouped by account |
| `hyp3_retry_jobs_{timestamp}.json` | Job IDs for a retry batch (written on each **Retry**) |
| `.insarhub_cache.json` | Updated after each **Check** with succeeded filenames and output directory |

### Stage 3 — Analysis

Produced by `insarhub analyzer run` or GUI **Run Analyzer**.

| File | Description |
|------|-------------|
| `insarhub_config.json` | Updated with analyzer type |
| `.mintpy.cfg` | MintPy `smallbaselineApp` configuration written by InSARHub |
| `tmp/` | Unzipped HyP3 product contents (temporary) |
| `clip/` | AOI-clipped interferograms (temporary) |

All MintPy inputs and outputs are produced by MintPy. See the [MintPy documentation](https://mintpy.readthedocs.io) for the full list.

**After `cleanup`:**

`tmp/` and `clip/` are deleted. `insarhub_config.json`, `.mintpy.cfg`, and all MintPy outputs are preserved.

### Export Utilities

InSARHub provides optional export helpers that convert MintPy HDF5 outputs to more portable formats. These are not a pipeline stage — they are run manually or via the GUI **Export** button after MintPy has finished.

| File | Description |
|------|-------------|
| `velocity.tif`, `velocityERA5.tif` | LOS velocity GeoTIFFs converted from MintPy `velocity.h5` |
| `timeseries.tif`, `timeseriesResidual.tif` | Displacement time series GeoTIFFs (one band per date) |
| `temporalCoherence.tif`, `avgSpatialCoh.tif` | Coherence map GeoTIFFs |
| `demErr.tif`, `maskTempCoh.tif`, `maskConnComp.tif` | DEM error and mask GeoTIFFs |
| `{product}_footprint.shp` / `.gpkg` | AOI footprint polygon derived from raster bounds |

All source data comes from MintPy HDF5 files. These utilities only reformat existing outputs — they do not run any processing themselves.

---

## Key JSON File Formats

### `insarhub_config.json`

Central pipeline config that accumulates as each stage runs. All keys are optional — only the stages that have been executed are present.

```json
{
  "downloader": {
    "type": "S1_SLC",
    "config": {
      "start": "2020-01-01",
      "end": "2020-12-31",
      "relativeOrbit": 100,
      "frame": 466
    }
  },
  "processor": {
    "type": "Hyp3_InSAR",
    "config": {
      "phase_filter_parameter": 0.6,
      "looks": "20x4"
    }
  },
  "analyzer": "Hyp3_SBAS"
}
```

### `stack_p{path}_f{frame}.json`

Pair network and quality scores for one track/frame group.

```json
{
  "pairs": [
    ["S1A_IW_SLC__1SDV_20200101", "S1A_IW_SLC__1SDV_20200113"],
    ["S1A_IW_SLC__1SDV_20200113", "S1A_IW_SLC__1SDV_20200125"]
  ],
  "baselines": {
    "S1A_IW_SLC__1SDV_20200101": 0.0,
    "S1A_IW_SLC__1SDV_20200113": 12.4,
    "S1A_IW_SLC__1SDV_20200125": -5.8
  },
  "scenes": [
    "S1A_IW_SLC__1SDV_20200101",
    "S1A_IW_SLC__1SDV_20200113",
    "S1A_IW_SLC__1SDV_20200125"
  ],
  "pair_quality": {
    "scores": {
      "S1A_..._20200101,S1A_..._20200113": 87.5
    },
    "factors": {
      "S1A_..._20200101,S1A_..._20200113": {
        "coherence": 0.72,
        "ndvi": 0.85,
        "snow": 0.0,
        "rain": 0.1,
        "fire": 0.0
      }
    }
  }
}
```

### `hyp3_jobs.json`

Saved HyP3 job IDs grouped by account, plus the output directory.

```json
{
  "job_ids": {
    "username1": ["job-id-aaa", "job-id-bbb"],
    "username2": ["job-id-ccc"]
  },
  "out_dir": "/data/bryce/p100_f466"
}
```

---

## Internal Cache Files

These dot-files are written automatically. You can safely delete them — InSARHub will regenerate them on the next run.

### `.insarhub_cache.json`

Written by the processor after a **Check** or **Download** action. Stores the list of expected interferogram zip filenames and the HyP3 output directory so the **Render** tab can find downloaded products without re-reading all job files.

```json
{
  "filenames": [
    "S1AA_20200101T000000_20200113T000000_VVP012_INT20_G_ueF_1234.zip"
  ],
  "out_dir": "/data/bryce/p100_f466"
}
```

### `.insarhub_quality_cache.json`

Shared cache for all pair-quality feature data. Written by the pair scorer and updated incrementally — data that doesn't change (historical weather, MODIS snow, land cover) is never re-fetched.

| Section | Key format | Contents | Expires |
|---------|-----------|----------|---------|
| `geometry` | `<aoi_hash>` | AOI area, bounding box | 365 days |
| `landcover` | `<aoi_hash>` | WorldCover class fractions | 365 days |
| `weather` | `<lat>:<lon>:<date>` | Precipitation, temperature, wind | Never |
| `snow_modis` | `<lat>:<lon>:<date>` | MODIS snow-cover fraction | Never |
| `veg` | `<source>:<lat>:<lon>:<year>:<month>` | NDVI / vegetation index | Never |
| `s1_coherence` | `"map"` | S1 global coherence per-season pixel maps + per-pair evaluated results | Never |
| `ndvi` | `"map"` | MODIS NDVI per-date lookup | Never |

```json
{
  "_schema_version": 2,
  "weather": {
    "45.123:0.456:2020-01-01": {
      "precip_3d_mm": 2.1,
      "temp_mean_c": 3.4,
      "_fetched_at": "2024-01-01T00:00:00+00:00"
    }
  },
  "landcover": {
    "a1b2c3d4": {
      "tree_cover_frac": 0.12,
      "urban_frac": 0.34,
      "_fetched_at": "2024-01-01T00:00:00+00:00"
    }
  }
}
```

### `.insarhub_pair_quality_db.json`

Pre-computed quality scores for **all N×(N−1)/2 scene combinations** in the folder. Built once in the background after pair selection and reused by the Network Editor for instant lookups without any network calls.

The DB is only rebuilt when the scene list grows (new scenes added by a fresh search). Removing scenes or changing selection parameters (`dt_max`, `pb_max`, etc.) does **not** trigger a rebuild because scores depend only on scene acquisition dates, weather, and coherence — not on the graph algorithm.

```json
{
  "_schema_version": 1,
  "_built_at": "2024-01-01T00:00:00+00:00",
  "_n_scenes": 42,
  "_n_pairs": 861,
  "_complete": true,
  "_scene_names": ["S1A_IW_SLC__1SDV_20200101...", "..."],
  "scores": {
    "S1A_..._20200101:S1A_..._20200113": 0.82
  },
  "factors": {
    "S1A_..._20200101:S1A_..._20200113": {
      "dt_days": 12,
      "bperp_diff": 8.3,
      "coherence_expected": 0.71,
      "snow_cover_d1": 0.0,
      "snow_cover_d2": 0.0,
      "precip_3d_d1": 1.2,
      "precip_3d_d2": 0.5,
      "score": 0.82
    }
  }
}
```

While a build is in progress `_complete` is `false` and `scores`/`factors` are absent — the UI shows a "Building pair database…" indicator until it flips to `true`.

### `decay_maps/`

GeoTIFF files caching the fitted S1 coherence pixel decay maps downloaded from AWS S3 (`sentinel-1-global-coherence-earthbigdata`). One 3-band GeoTIFF per season:

| Band | Contents |
|------|----------|
| 1 | γ∞ — permanent-scatterer coherence floor |
| 2 | γ0 — initial coherence at t = 0 |
| 3 | τ — decorrelation time constant (days) |

These files survive process restarts, so S3 is only queried once per season per AOI. Delete them to force a re-download from S3.
