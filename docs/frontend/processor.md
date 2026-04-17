# Processor

Once you have added a job via **Add Job** in the Search panel, the job folder appears in the **Jobs** drawer. Click the **Jobs** button in the top-right corner of the toolbar to open the drawer.

<!-- screenshot: jobs button in toolbar -->
![Jobs Button](fig/jobs_button_light.png#only-light){: .doc-img}
![Jobs Button](fig/jobs_button_dark.png#only-dark){: .doc-img}
/// caption
The **Jobs** button in the top-right toolbar opens the Job Folders drawer.
///

Then click the downloader tag (e.g. **S1_SLC**) on the job folder to open its detail panel.

<!-- screenshot: clicking downloader tag on job folder -->
![Downloader Tag](fig/downloader_tag_light.png#only-light){: .doc-img style="width: 60%"}
![Downloader Tag](fig/downloader_tag_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
Click the downloader tag on a job folder to open its detail panel.
///

## Selecting Pairs

Constructing a well-designed interferometric pair network is a critical step in time-series InSAR analysis. A carefully chosen SBAS network balances temporal and perpendicular baseline constraints to maximize coherence while ensuring full temporal connectivity across the scene stack.

Click **Edit Network** to open the interactive baseline–time graph editor.

![Edit Network](fig/processor_edit_network_light.png#only-light){: .doc-img style="width: 50%"}
![Edit Network](fig/processor_edit_network_dark.png#only-dark){: .doc-img style="width: 50%"}
/// caption
Select Edit Network to open network modification window
///

The network graph is interactive. **Drag** from one scene node to another to create a new pair. **Click** an existing edge to remove it from the network. **Hover** over any edge to view its temporal baseline, perpendicular baseline, and quality score.

![Network Graph](fig/network_modify_light.gif#only-light){: .doc-img }
![Network Graph](fig/network_modify_dark.gif#only-dark){: .doc-img }
/// caption
Baseline–time graph showing the interferometric network. Click any edge to toggle it, or drag between nodes to add a new pair.
///

Edge colors reflect the pre-computed pair quality score — green edges are high quality, yellow are moderate, and red are poor. 
**Hover** over any edge to view its temporal baseline, perpendicular baseline, and quality score.


The network editor supports two workflows:

**Manual editing** — **click** any edge (interferogram pair) to toggle it active or removed.  **Drag** from one scene node to another to create a new pair. Click **Save** to persist the updated pair list to the job folder.

**Auto pair selection** — click **⚙ Parameters** to generate the network automatically from the scene stack:

| Parameter | Description |
|-----------|-------------|
| **Target Temporal Baselines** | Comma-separated target temporal separations (days) to form pairs around |
| **Tolerance** | Allowed deviation (days) from each target baseline |
| **Max Temporal** | Hard upper limit on temporal baseline (days) |
| **Max Perp. Baseline** | Hard upper limit on perpendicular baseline (m) |
| **Min Connections** | Minimum number of interferograms each scene must participate in |
| **Max Connections** | Maximum number of interferograms per scene |
| **Force Connected Network** | Add extra pairs to guarantee no isolated nodes |

**View Pairs** — lists all selected interferometric pairs with their temporal and perpendicular baseline values.

<!-- screenshot: view pairs -->
![View Pairs](fig/view_pairs_light.png#only-light){: .doc-img style="width: 60%"}
![View Pairs](fig/view_pairs_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
List of selected interferometric pairs with baseline information.
///

---

## Decay Maps

Click **Decay Maps** to open the Coherence Decay Maps drawer. This overlays seasonal S1 Global Coherence maps on the main map, giving you a quick read on expected coherence at your site before submitting any jobs.

![Decay Maps Button](fig/decay_maps_button_light.png#only-light){: .doc-img style="width: 60%"}
![Decay Maps Button](fig/decay_maps_button_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
The Decay Maps button in the downloader job panel.
///

Each available season and polarization is listed. Click any of the three band buttons to overlay it on the map:

| Band | Symbol | What it shows |
|------|--------|---------------|
| **1** | γ∞ PS floor | Permanent-scatterer coherence floor — the minimum coherence that persists regardless of time gap |
| **2** | γ0 initial coh | Initial coherence at acquisition — higher values indicate better short-baseline coherence |
| **3** | τ decay | Decorrelation time constant (days) — larger values mean coherence persists longer |

![Decay Maps Overlay](fig/decay_maps_overlay_light.png#only-light){: .doc-img}
![Decay Maps Overlay](fig/decay_maps_overlay_dark.png#only-dark){: .doc-img}
/// caption
Coherence decay map overlaid on the basemap. Hover over the map to read pixel values.
///

Click the same button again to hide the overlay. Click a different band to switch layers.

---

## View Data

Once interferograms have been downloaded, click **View Data** in the Processor panel to open the data browser. This lists all HyP3 product files extracted from the downloaded `.zip` archives and lets you overlay any of them directly on the map.

![View Data Button](fig/view_data_button_light.png#only-light){: .doc-img style="width: 60%"}
![View Data Button](fig/view_data_button_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
The View Data button in the Processor panel.
///

Each interferogram pair is listed with its available product files:

| Type | Description |
|------|-------------|
| `unw_phase` | Unwrapped interferometric phase |
| `corr` | Interferogram coherence |
| `dem` | Digital elevation model used in processing |
| `lv_theta` | Look vector elevation angle |
| `lv_phi` | Look vector azimuth angle |
| `water_mask` | Water body mask |

Click any file to render it as a raster overlay on the map. Click again to hide it.

![View Data Overlay](fig/view_data_overlay_light.png#only-light){: .doc-img}
![View Data Overlay](fig/view_data_overlay_dark.png#only-dark){: .doc-img}
/// caption
HyP3 interferogram product overlaid on the basemap.
///

---

## Submitting Jobs

Once the pair network is reviewed and satisfactory, click **Process** to open the processor selection dialog.

<!-- screenshot: click process button -->
![Process Button](fig/process_button_light.png#only-light){: .doc-img style="width: 60%"}
![Process Button](fig/process_button_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
Click **Process** to submit interferogram pairs to HyP3.
///

<!-- screenshot: processor selection dialog -->
![Processor Selection](fig/processor_dialog_light.png#only-light){: .doc-img style="width: 60%"}
![Processor Selection](fig/processor_dialog_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
Processor selection dialog. Choose a processor (e.g. `Hyp3_InSAR`) and confirm to submit all pairs to HyP3.
///

!!! tip "Test before submitting"
    For submitting jobs to an external server, check **Dry Run** in the processor dialog to validate your environment and credentials without submitting real jobs. A successful dry run produces output similar to:

    ```
    [Dry run] Would submit 65 pairs via Hyp3_InSAR from p93_f121
    ```

    This is recommended before your first submission to ensure everything is configured correctly.

For a full description of all processor parameters and options, see the [Processor Reference](../advanced/processor.md).

Once jobs are successfully submitted, a **Processor** tag with your processor name will appear in the job folder panel, indicating that HyP3 processing is active for this stack.

<!-- screenshot: processor tab appears -->
![Processor Tab](fig/processor_tab_light.png#only-light){: .doc-img style="width: 60%"}
![Processor Tab](fig/processor_tab_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
The **Processor** tab appears in the job folder panel after jobs are successfully submitted.
///

---

## Monitoring Jobs

Once jobs are submitted, a job file is automatically saved to the job folder and loaded by default the next time you open the Processor panel. This allows you to resume monitoring even after closing the application.

A drop-down menu at the top of the Processor panel lists all job files found under the job folder, including the initial submission file (`hyp3_jobs.json`) and any retry files generated by subsequent **Retry** actions (e.g. `hyp3_retry_jobs_20260306t095505.json`). Select a different file from the list to inspect or monitor a specific submission.

Click **Refresh** to check the latest status of all submitted jobs from HyP3. Each job displays one of the following statuses:

| Status | Meaning |
|--------|---------|
| `RUNNING` | Job is actively being processed on HyP3 |
| `SUCCEEDED` | Processing completed successfully |
| `FAILED` | Processing failed |

<!-- screenshot: job status list -->
![Job Status](fig/processor_status_light.png#only-light){: .doc-img style="width: 80%"}
![Job Status](fig/processor_status_dark.png#only-dark){: .doc-img style="width: 80%"}
/// caption
The processor job panel
///

If any jobs have `FAILED`, click **Retry** to resubmit them. Once jobs show `SUCCEEDED`, click **Download** to fetch the processed interferograms to the work directory.

---

## Other Actions

| Button | Description |
|--------|-------------|
| **Retry** | Resubmit all failed jobs to HyP3 |
| **Download** | Download all succeeded interferograms to the work directory |
| **Watch** | Continuously poll HyP3 until all jobs complete, then download automatically |
| **Credits** | Check remaining HyP3 processing credits |

---

Once all jobs have succeeded and interferograms are downloaded, proceed to the Analyzer panel to run time-series InSAR analysis.

[Analyzer](analyzer.md){.md-button}


