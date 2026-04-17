# File Structure

InSARHub writes a consistent set of files to disk as the pipeline progresses. Each stage adds files to the working directory — the same folder is reused from pair selection through analysis, so you can always tell how far a folder has been processed by which files are present.

---

## Directory Layout

InSARHub separates files by track/frame group. All pipeline files go into a `p{path}_f{frame}/` subfolder — nothing is written to the top-level workdir for multi-stack runs.

**Multi-stack run** (search covers more than one track/frame):

```
workdir/
├── p100_f466/                    # one subfolder per track/frame group
│   ├── insarhub_config.json      # pipeline config (accumulates each stage)
│   ├── stack_p100_f466.json      # pairs, baselines, scenes, quality scores
│   ├── ...
├── p93_f121/
│   ├── insarhub_config.json
│   ├── stack_p93_f121.json
│   └── ...
```

**Single-stack (flat) run** — when only one track/frame is found, files are written directly into workdir with no subfolder:

```
workdir/
├── insarhub_config.json          # pipeline config (accumulates each stage)
├── stack_p0_f0.json              # pairs, baselines, scenes, quality scores
├── hyp3_jobs.json                # submitted job IDs  (after processor submit)
├── hyp3_retry_jobs_*.json        # retry batches      (after processor retry)
├── .mintpy.cfg                   # MintPy config written by InSARHub (after analyzer init)
├── tmp/                          # extracted zip contents  (removed by cleanup)
└── clip/                         # AOI-clipped data        (removed by cleanup)
```

MintPy inputs (`inputs/ifgramStack.h5`, etc.) and outputs (`timeseries*.h5`, `velocity.h5`, `velocity.tif`, etc.) are all written by MintPy and are not listed here. See the [MintPy documentation](https://mintpy.readthedocs.io) for details.

For multi-stack runs the same set of files appears inside each `p{path}_f{frame}/` subfolder independently.

---

## Files by Stage

### Stage 1 — Pair Selection

Produced by `insarhub downloader --select-pairs` or GUI **Select Pairs**.

For multi-stack runs, all files go inside `p{path}_f{frame}/` subfolders. For single-stack (flat) runs, they go directly in `workdir/`.

| File | Description |
|------|-------------|
| `insarhub_config.json` | Downloader type and settings (written into each group folder, or workdir for flat runs) |
| `stack_p{path}_f{frame}.json` | Selected pairs, perpendicular baselines, scene list, and pair quality scores |

### Stage 2 — Job Submission

Produced by `insarhub processor submit` or GUI **Process**.

| File | Description |
|------|-------------|
| `insarhub_config.json` | Updated with processor type and settings (in each group folder) |
| `hyp3_jobs.json` | HyP3 job IDs grouped by account |
| `hyp3_retry_jobs_{timestamp}.json` | Job IDs for a retry batch (written on each **Retry**) |

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
