# Program Structure

InSARHub is built around three loosely-coupled layers — **Downloader**, **Processor**, and **Analyzer** — each implemented as a registry of named backends. A shared `insarhub_config.json` written to the job folder accumulates configuration as the pipeline progresses, so every stage can be run independently or chained together.

- **Downloader** — searches ASF for scenes (full SLC or individual SLC-BURST granules), selects interferogram pairs with quality scoring, and fetches SLC data and orbit files.
- **Processor** — takes the selected pairs and produces (unwrapped) interferograms: cloud (HyP3), local ISCE2 `stackSentinel`, local GMTSAR `p2p_processing`/`p2p_S1_TOPS_Frame`, or ISCE3/COMPASS burst geocoding.
- **Analyzer** — inverts the interferogram stack into a displacement time series: MintPy `smallbaselineApp`, GMTSAR's own `sbas` binary, or dolphin's `timeseries`.

The Web UI and CLI are thin shells over the same Python API, so any workflow that runs in the browser can be reproduced exactly on the command line or in a script.

![InSARHub workflow](fig/InSARHub_workflow.png){: .doc-img-wide }

## Pipeline Matrix

Each downloader feeds a set of compatible processors, and each processor a set of compatible analyzers. The registry wires these via `compatible_downloader` / `compatible_processor`; a workdir's saved `insarhub_config.json` records which pipeline it belongs to.

```
Downloader          Processor            Analyzer
─────────────────   ──────────────────   ─────────────────────────
S1_SLC           →  Hyp3_S1           →  Hyp3_Mintpy_SBAS
                 →  ISCE2_S1          →  ISCE2_Mintpy_SBAS
                 →  GMTSAR_S1         →  GMTSAR_Mintpy_SBAS
                                      →  GMTSAR_SBAS
S1_Burst         →  ISCE3_Burst       →  ISCE3_Dolphin_PL
NISAR_GSLC       →  ISCE3_NISAR       →  ISCE3_Dolphin_PL
```

Any local backend can run inside a container instead of on the host — see [Container Execution](container.md) for how `--container` works.
