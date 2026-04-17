# Frontend Reference

Detailed reference for every panel in the InSARHub web interface.

## Downloader Panel

Open by clicking the **Downloader** tag on a job folder.

### Config Tab
Shows the downloader configuration stored in `insarhub_config.json`. Click any row to copy the value.

### Actions

| Button | Description |
|--------|-------------|
| **View Network** | Display the interferometric network graph |
| **View Pairs** | List all selected interferogram pairs |
| **Select Pairs** | Open the pair selection dialog — configure `dt_targets`, tolerances, and degree limits |
| **Download** | Download all available HyP3 interferograms to disk |
| **Submit** | Submit selected pairs to the processor (HyP3) |

### Select Pairs Dialog

| Field | Description |
|-------|-------------|
| **dt targets** | Comma-separated temporal baselines (days) to target, e.g. `12, 24, 48` |
| **dt tolerance** | Allowed deviation from each target (days) |
| **dt max** | Maximum temporal baseline (days) |
| **Min / Max degree** | Min and max connections per scene in the network |
| **Force connect** | Ensure no isolated scenes in the network |

---

## Processor Panel

Open by clicking the **Processor** tag on a job folder.

### HyP3 Job Files

Lists all `hyp3_jobs.json` files in the folder. Select one to act on it.

### Actions

| Button | Description |
|--------|-------------|
| **Refresh** | Poll HyP3 for current job statuses |
| **Retry** | Re-submit any failed jobs |
| **Download** | Download all succeeded interferograms |
| **Stop** | Cancel an in-progress download |

---

## Analyzer Panel

Open by clicking the **Analyzer** tag on a job folder.

### Step Checklist

Each MintPy processing step is shown with a checkbox. Select the steps you want to run:

| Step | Description |
|------|-------------|
| `prep_data` | Prepare and clip HyP3 products, write `mintpy.cfg` |
| `load_data` | Load interferograms into MintPy HDF5 stack |
| `modify_network` | Remove low-coherence interferograms |
| `reference_point` | Select a stable reference pixel |
| `quick_overview` | Generate diagnostic overview layers (coherence, phase velocity, unwrapping errors, connected component mask) — enables the **Overview** button |
| `invert_network` | SBAS inversion → timeseries |
| `correct_LOD` | Correct local oscillator drift |
| `correct_SET` | Solid Earth tide correction |
| `correct_troposphere` | Tropospheric delay correction |
| `deramp` | Remove ramp signal |
| `correct_topography` | DEM error correction |
| `residual_RMS` | Compute residual RMS |
| `reference_date` | Set reference date |
| `velocity` | Estimate linear velocity map |
| `geocode` | Geocode results to geographic coordinates |
| `hdfeos5` | Export to HDF-EOS5 format |

### Buttons

| Button | Description |
|--------|-------------|
| **All / None** | Select or deselect all steps |
| **Config** | Open Settings → Analyzer tab for this analyzer type |
| **Run N steps** | Run the selected steps in order |
| **Stop** | Cancel after the current step finishes |
| **Cleanup** | Remove temporary directories and zip archives |

### Config (per-analyzer)

Each analyzer type stores its own configuration independently. Changes to `Hyp3_SBAS` config never affect `Mintpy_SBAS_Base` config and vice versa. Open via the **Config** button or Settings → Analyzer tab.

---

## MintPy Results Viewer

Available after the `velocity` step completes. Click **View Results** in the Analyzer panel.

### Timeseries File Selector

Lists all available `timeseries*.h5` files in the folder, ordered by quality:

```
timeseries_ERA5_ramp_demErr.h5   ← best (all corrections applied)
timeseries_ERA5_ramp.h5
timeseries_ERA5_demErr.h5
timeseries_ERA5.h5
timeseriesResidual_ramp.h5
timeseriesResidual.h5
timeseries.h5                    ← raw (no corrections)
```

Select the file to use for pixel time series extraction, then click **Plot**.

### Plot Button

Clicking **Plot**:

1. Renders the velocity map as a colored PNG overlay on the map (colorscale auto-scaled to 98th percentile of data)
2. Arms map-click for time series extraction using the selected timeseries file

### Velocity Map

- Color scale: blue = subsidence, red = uplift, symmetric range scaled to 98th percentile
- Zero-displacement pixels are transparent
- Opacity: 85%

### Pixel Time Series

Click any point on the velocity overlay to extract and plot the displacement time series for that pixel.

- Displacement is shown in **mm**, relative to the first acquisition date (first date = 0)
- Dates are shown on the X axis
- The chart follows the app's dark/light theme

---

## Keyboard & Mouse Reference

| Action | Result |
|--------|--------|
| Click map (box mode, 1st) | Set first corner of AOI |
| Click map (box mode, 2nd) | Complete AOI box |
| Double-click map (polygon mode) | Close polygon |
| Click velocity overlay | Extract pixel time series |
| Click footprint | Open scene detail panel |
| Scroll | Zoom map |
| Drag | Pan map |
