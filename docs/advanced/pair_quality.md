# Pair Quality Scoring

InSARHub scores every interferogram pair before and after network selection to estimate its likely coherence. Scores drive the color coding in the Network Editor and the pre-built pair database used for instant lookups.

---

## Overview

A quality score of **100** means the pair is very likely to produce a usable interferogram. A score of **0** means it is expected to decorrelate completely. The score combines physical signals — expected coherence from a global satellite dataset, snow and rain conditions, season, and land cover — into a single number.

Scores are computed by `PairQuality` (for the selected pairs) and stored for all N×(N−1)/2 scene combinations in `.insarhub_pair_quality_db.json` by `PairQualityDB`.

---

## Scoring Pipeline

```
FeatureAssembler.assemble()
        │
        ▼
  FeatureVector dict
        │
        ▼
  coherence_score()  ──► Tier 1: S1 global coherence (AWS S3)
        │                Tier 2: LC/NDVI scorer (WorldCover)
        │                Tier 3: Climatology safeline
        ▼
  score: int [0–100]
  factors: dict (penalty breakdown)
```

### Data sources

| Signal | Source | Fallback |
|--------|--------|----------|
| Expected coherence | S1 Global Coherence dataset, AWS S3 (Kellndorfer et al. 2022) | Latitude-band climatology table |
| Snow cover fraction | MODIS daily snow product | None (treated as 0) |
| Precipitation | Open-Meteo historical reanalysis | None (treated as 0) |
| Land cover fractions | ESA WorldCover 10 m | None (branch blending skipped) |
| NDVI | MODIS 16-day composite | Climatology |
| Fire events | NASA FIRMS VIIRS (optional, requires `FIRMS_MAP_KEY`) | Skipped |

All fetched data is cached in `.insarhub_quality_cache.json` — historical data never changes, so each date/location is only fetched once.

---

## Scoring Modes

### Tier 1 — S1 Global Coherence (primary)

When the AWS S3 dataset is reachable, the base signal is the **expected interferometric coherence γ** derived from the Kellndorfer et al. (2022) global seasonal dataset.

Per-pixel decay model parameters (γ∞, γ0, τ) are fitted per season from the S3 COG tiles and cached as GeoTIFFs in `decay_maps/`. For each pair the model is evaluated at the actual temporal baseline:

```
γ(dt) = γ∞ + (γ0 − γ∞) · exp(−dt / τ)
```

For cross-season pairs the pair span is split at season boundaries and the segments are chained using duration-weighted effective parameters.

**Coherence penalty** — shifted quadratic, zero penalty above γ = 0.60, full penalty at γ ≤ 0.10:

```
clamped     = clamp((0.60 − γ) / 0.50, 0, 1)
coh_penalty = clamped²
```

This avoids penalising workable pairs just for not being perfect — global S1 average at 12 days is 0.30–0.45, which is usable.

**Environmental penalties** applied on top:

| Penalty | Weight | Saturates at |
|---------|--------|-------------|
| Coherence | 1.00 | γ ≤ 0.10 |
| Snow (worst single metric) | 0.25 | fraction = 1.0 |
| Precipitation D1 | 0.75 | 30 mm over 3 days |
| Precipitation D2 | 0.75 | 30 mm over 3 days |
| Freeze-thaw crossing | 0.05 | — |

```
total_penalty = 1.00·coh + 0.25·snow + 0.75·pr_d1 + 0.75·pr_d2 + 0.05·ft
score = clamp(round((1 − total_penalty) × 100), 0, 100)
```

**Hard kill — wet snow** → score = 0 immediately, no further scoring:

- Temperature on acquisition day > 0°C **and** MODIS snow fraction > 30 %
- C-band penetration depth collapses to 5–10 cm at ≥1 % liquid water content (Strozzi et al. 1999)

### Tier 2 — LC/NDVI (S3 unavailable)

When S3 is unreachable and WorldCover land cover data is available, the scorer routes through the **land-cover branching model**. The AOI is split into three land cover classes, each scored with its own weight set, and the results are blended by area fraction:

| Branch | Land cover | Key driver |
|--------|-----------|------------|
| A — Urban/Bare | Urban + bare soil/rock | Geometric baseline, relaxed temporal penalty |
| B — Vegetation | Cropland + grassland + shrub | NDVI × temporal interaction, season crossing |
| C — Forest | Tree cover + mangrove | Always low coherence, capped at 0.25 |

```
score = w_A · score_A + w_B · score_B + w_C · score_C
```

where w_A, w_B, w_C are the normalised AOI fractions of each class.

**Branch weights summary (penalty weights, higher = worse):**

| Feature | Branch A | Branch B | Branch C |
|---------|---------|---------|---------|
| `dt_normalized` | 0.15 | 0.18 | 0.25 |
| `bperp_normalized` | 0.12 | 0.08 | 0.10 |
| `snow_cover` (per date) | 0.08 | 0.08 | 0.08 |
| `veg_temporal` | 0.00 | 0.12 | 0.15 |
| `season_penalty` | 0.04 | 0.08 | 0.08 |
| `is_annual_repeat` bonus | −0.15 | −0.18 | −0.18 |

**Hard kills in LC mode** (any → score = 0):

| Condition | Threshold |
|-----------|-----------|
| Water dominant | > 50 % water |
| Snow/ice dominant | > 40 % snow/ice land cover |
| Wet snow | temp > 0°C and snow fraction > 30 % |
| Fresh snowfall | MODIS snow cover change > 50 % between dates |
| Heavy snow cover | > 90 % snow cover on either date |
| Heavy rain | > 30 mm/day on either date |
| Fire event | NASA FIRMS detects hotspots within 7 days of either acquisition (requires `FIRMS_MAP_KEY`) |

**NDVI transition cap** — when vegetation fraction > 40 % and NDVI crosses the dormant/active threshold (0.30) between the two dates, the score is capped at 0.50 regardless of other signals.

### Tier 3 — Climatology safeline

When S3 fails **and** no NDVI/landcover data is available, a hardcoded latitude-band coherence table is used as the base signal. The same environmental penalties as Tier 1 are then applied. This path always returns a value — the pipeline never produces a null score.

---

## Season Penalty

Applied in Tier 2 (LC mode) and the flat fallback. Captures the increased decorrelation when the two acquisition dates span different vegetation or snow seasons.

| Season combination | Penalty |
|-------------------|---------|
| Same season | 0.00 |
| Adjacent seasons (e.g. spring–summer) | 0.35 |
| Opposite seasons (no winter involved) | 0.70 |
| One date in winter, other not | 0.95 |

Seasons are flipped for the southern hemisphere (6-month offset).

---

## Annual Repeat Bonus

A pair whose temporal baseline is within ±20 days of an integer multiple of 365 days (up to 4 years) receives a bonus that reduces its penalty. The pair captures the same seasonal vegetation and snow state on both dates — the dominant decorrelation sources cancel.

- Tier 1/2: bonus reduces the `is_annual_repeat` penalty weight (−0.15 to −0.18)
- Legacy flat mode: −0.30 subtracted from raw penalty sum

---

## Reading the `factors` dict

Every scored pair stores a `factors` dict alongside its score. In Tier 1 the key fields are:

```json
{
  "score": 82,
  "coherence_expected": 0.54,
  "coherence_source": "s3",
  "coherence_season_d1": "winter",
  "coherence_season_d2": "winter",
  "coherence_same_season": true,
  "penalties": {
    "coherence":   0.08,
    "snow":        0.00,
    "precip_d1":   0.03,
    "precip_d2":   0.01,
    "freeze_thaw": 0.00
  },
  "hard_kill": null,
  "dt_days": 12,
  "bperp_diff": 8.3,
  "snow_cover_d1": 0.0,
  "snow_cover_d2": 0.0,
  "precip_3day_d1": 1.2,
  "precip_3day_d2": 0.4
}
```

In Tier 2 (LC mode) the `branch` and `hard_kills` fields replace `penalties`:

```json
{
  "score": 0.71,
  "branch": "B_veg",
  "branch_weights": {"A": 0.12, "B": 0.74, "C": 0.14},
  "hard_kills": [],
  "warnings": ["ndvi_transition"],
  "score_A": 0.88,
  "score_B": 0.68,
  "score_C": 0.21
}
```

---

## Python API

```python
from insarhub.utils.pair_quality import PairQuality

pq = PairQuality("/data/bryce/p100_f466")
result = pq.compute()

# result.scores  → {"scene_a:scene_b": 82, ...}   (int 0–100)
# result.factors → {"scene_a:scene_b": {...}, ...}

pq.print_summary()   # prints a ranked table to stdout
```

### Pre-built database (all scene combinations)

```python
from insarhub.utils.pair_quality._db import PairQualityDB

# Build once (runs in background)
db = PairQualityDB("/data/bryce/p100_f466")
thread = db.precompute_background(scenes_by_stack, bperp_by_stack)

# Instant lookup (no network calls)
score = PairQualityDB.lookup(folder, ref_scene, sec_scene)   # float | None
status = PairQualityDB.status(folder)   # {exists, complete, n_scenes, n_pairs, built_at}
```

The database is rebuilt automatically after pair selection when new scenes are added. It is **not** rebuilt when selection parameters (`dt_max`, `pb_max`, etc.) change, since scores depend only on scene dates, weather, and coherence — not the graph algorithm.
