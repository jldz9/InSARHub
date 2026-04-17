# Results Viewer

After analysis completes, InSARHub can display MintPy results directly on the map.

## Loading the Velocity Map

1. Open a job folder that contains MintPy outputs (`velocity.h5`)
2. Open the **View Results** panel
3. Select a timeseries file from the list (e.g. `timeseries_ERA5_ramp_demErr.h5`)
4. Click **Plot**

<!-- screenshot: view results panel with ts file selected -->
![View Results Panel](fig/results_panel_light.png#only-light){: .doc-img style="width: 60%"}
![View Results Panel](fig/results_panel_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
Results panel with timeseries file selected, ready to plot.
///

The velocity map is overlaid on the map with a symmetric colorbar scaled to the 98th percentile of the data (red = subsidence, blue = uplift). Zero-displacement pixels are transparent.

<!-- screenshot: velocity map overlaid on map -->
![Velocity Map](fig/velocity_map_light.png#only-light){: .doc-img-wide }
![Velocity Map](fig/velocity_map_dark.png#only-dark){: .doc-img-wide }
/// caption
Line-of-sight velocity map overlaid on the basemap.
///

---

## Timeseries at a Pixel

Click any pixel on the velocity map to plot the displacement timeseries at that location.

<!-- screenshot: timeseries drawer open at bottom -->
![Timeseries Drawer](fig/timeseries_light.png#only-light){: .doc-img-wide }
![Timeseries Drawer](fig/timeseries_dark.png#only-dark){: .doc-img-wide }
/// caption
Displacement timeseries for a clicked pixel. First date is set to zero (relative displacement).
///

The timeseries drawer:

- Shows displacement in **cm** relative to the first acquisition date
- Follows the app's light/dark theme
- Can be closed by clicking the × button

---

## Colorbar

The velocity colorbar is symmetric and auto-scaled to the **98th percentile** of the absolute velocity values in the scene:

- **Red** — negative velocity (subsidence / moving away from satellite)
- **White** — near-zero displacement
- **Blue** — positive velocity (uplift / moving toward satellite)
- **Transparent** — exactly zero (no data or reference pixels)
