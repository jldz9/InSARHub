from dataclasses import dataclass, field, asdict
from typing import ClassVar, List, Union, Optional, Any
from pathlib import Path
from asf_search import constants
from insarhub import _env

# ---------------------------------------------------------------------------
# Downloader configurations
# ---------------------------------------------------------------------------
@dataclass
class ASF_Base_Config:
    '''
    Dataclass containing all configuration options for asf_search.
    
    This class provides a unified interface for configuring ASF (Alaska Satellite Facility) 
    search parameters.
    '''
    name: str = "ASF_Base_Config"
    dataset: str | list[str] | None = None
    platform: str | list[str] | None = None
    instrument: str | None = None
    absoluteBurstID: int | list[int] | None = None
    absoluteOrbit: int | list[int] | None = None
    asfFrame: int | list[int] | None = None
    beamMode: str | None = None
    beamSwath: str | list[str] | None = None
    campaign: str | None = None
    maxDoppler: float | None = None
    minDoppler: float | None = None
    maxFaradayRotation: float | None = None
    minFaradayRotation: float | None = None
    flightDirection: str | None = None
    flightLine: str | None = None
    frame: int | list[int] | None = None
    frameCoverage: str | None = None
    fullBurstID: str | list[str] | None = None
    groupID: str | None = None
    jointObservation: bool | None = None
    lookDirection: str | None = None
    offNadirAngle: float | list[float] | None = None
    operaBurstID: str | list[str] | None = None
    polarization: str | list[str] | None = None
    mainBandPolarization: str | list[str] | None = None
    sideBandPolarization: str | list[str] | None = None
    processingLevel: str | None = None
    productionConfiguration: str | list[str] | None = None
    rangeBandwidth: str | list[str] | None = None
    relativeBurstID: str | list[str] | None = None
    relativeOrbit: int | list[int] | None = None
    intersectsWith: str | None = None  
    processingDate: str | None = None
    start: str | None = None
    end: str | None = None
    season: list[int] | None = None
    stack_from_id: str | None = None
    maxResults: int | None = None
    granule_names: str | list[str] | None = None
    workdir: Path | str = field(default_factory=lambda: Path.cwd())
    ssl_verify: bool = True
    # Per-downloader download concurrency. Each downloader owns this; there is
    # deliberately no global setting shadowing it, because the two downloaders
    # parallelise different work: S1_SLC opens concurrent HTTP transfers, while
    # S1_Burst assembles whole dates (each one a burst2safe run that unpacks and
    # rewrites a multi-GB product). A value that suits one starves or overloads
    # the other.
    max_workers: int = 3

    # ── UI metadata consumed by the API / settings panel ─────────────────────
    _ui_groups: ClassVar[list] = [
        {"label": "Dataset",
         "fields": ["dataset", "platform", "instrument"]},
        {"label": "SAR Parameters",
         "fields": ["beamMode", "beamSwath", "processingLevel",
                    "polarization", "mainBandPolarization", "sideBandPolarization",
                    "lookDirection", "flightDirection", "flightLine"]},
        {"label": "Orbit & Frame",
         "fields": ["relativeOrbit", "absoluteOrbit", "frame", "asfFrame", "frameCoverage"]},
        {"label": "Burst IDs",
         "fields": ["absoluteBurstID", "relativeBurstID", "fullBurstID", "operaBurstID"]},
        {"label": "Temporal & Location",
         "fields": ["start", "end", "processingDate", "season",
                    "intersectsWith", "stack_from_id", "maxResults"]},
        {"label": "By Granule Name",
         "fields": ["granule_names"]},
        {"label": "Advanced",
         "fields": ["campaign", "groupID",
                    "maxDoppler", "minDoppler", "maxFaradayRotation", "minFaradayRotation",
                    "offNadirAngle", "jointObservation",
                    "productionConfiguration", "rangeBandwidth"]},
        {"label": "Download",
         "fields": ["max_workers"]},
    ]
    _ui_fields: ClassVar[dict] = {
        "max_workers":     {"type": "number", "min": 1, "max": 16, "step": 1,
                            "hint": "Concurrent download threads."},
        # Dataset
        "dataset":         {"type": "text",
                            "hint": "Dataset to search (e.g. SENTINEL-1, ALOS, NISAR)"},
        "platform":        {"type": "text",
                            "hint": "Platform name (e.g. S1A, ALOS)"},
        "instrument":      {"type": "text",
                            "hint": "Instrument name (e.g. C-SAR)"},
        # SAR Parameters
        "beamMode":        {"type": "select", "options": ["", "IW", "EW", "SM", "WV"],
                            "hint": "SAR acquisition mode"},
        "beamSwath":       {"type": "text",
                            "hint": "Beam swath identifier"},
        "processingLevel": {"type": "select",
                            "options": ["", "SLC", "GRD", "GRD_HD", "GRD_MS",
                                        "BURST", "RTC_HI_RES", "RTC_LOW_RES"],
                            "hint": "Processing level"},
        "polarization":    {"type": "text",
                            "hint": "Polarization(s), e.g. VV or VV+VH"},
        "mainBandPolarization": {"type": "text",
                            "hint": "Main band polarization (NISAR dual-band)"},
        "sideBandPolarization": {"type": "text",
                            "hint": "Side band polarization (NISAR dual-band)"},
        "lookDirection":   {"type": "select", "options": ["", "LEFT", "RIGHT"],
                            "hint": "Radar look direction"},
        "flightDirection": {"type": "select", "options": ["", "ASCENDING", "DESCENDING"],
                            "hint": "Orbit direction (empty = both)"},
        "flightLine":      {"type": "text",
                            "hint": "Flight line identifier"},
        # Orbit & Frame
        "relativeOrbit":   {"type": "text",
                            "hint": "Relative orbit (path) number(s), e.g. 64 or 64,65"},
        "absoluteOrbit":   {"type": "text",
                            "hint": "Absolute orbit number(s)"},
        "frame":           {"type": "text",
                            "hint": "Sensor native frame number(s)"},
        "asfFrame":        {"type": "text",
                            "hint": "ASF internal frame number(s)"},
        "frameCoverage":   {"type": "text",
                            "hint": "Frame coverage filter"},
        # Burst IDs
        "absoluteBurstID": {"type": "text",
                            "hint": "Absolute burst ID(s)"},
        "relativeBurstID": {"type": "text",
                            "hint": "Relative burst ID(s)"},
        "fullBurstID":     {"type": "text",
                            "hint": "Full burst ID, e.g. T064_135524_IW1"},
        "operaBurstID":    {"type": "text",
                            "hint": "OPERA burst ID(s)"},
        # Temporal & Location
        "start":           {"type": "text",
                            "hint": "Default start date (ISO 8601, e.g. 2020-01-01)"},
        "end":             {"type": "text",
                            "hint": "Default end date (ISO 8601, e.g. 2022-12-31)"},
        "processingDate":  {"type": "text",
                            "hint": "Processing date filter (ISO 8601)"},
        "season":          {"type": "text",
                            "hint": "Day-of-year range for seasonal filtering, e.g. 1,90"},
        "intersectsWith":  {"type": "text",
                            "hint": "WKT geometry for spatial intersection"},
        "stack_from_id":   {"type": "text",
                            "hint": "Build stack from a reference scene ID"},
        "maxResults":      {"type": "auto_number", "min": 1, "max": 50000, "step": 100,
                            "hint": "Maximum number of search results returned"},
        "granule_names":   {"type": "text",
                            "hint": "Granule/scene names (comma-separated), or a path to a CSV/XLSX/TXT file. "
                                    "When set, overrides normal parameter-based search."},
        # Advanced
        "campaign":        {"type": "text",
                            "hint": "Campaign name filter (UAVSAR / airborne datasets)"},
        "groupID":         {"type": "text",
                            "hint": "Group ID filter"},
        "maxDoppler":      {"type": "auto_number",
                            "hint": "Maximum Doppler centroid frequency (Hz)"},
        "minDoppler":      {"type": "auto_number",
                            "hint": "Minimum Doppler centroid frequency (Hz)"},
        "maxFaradayRotation": {"type": "auto_number",
                            "hint": "Maximum Faraday rotation angle (degrees)"},
        "minFaradayRotation": {"type": "auto_number",
                            "hint": "Minimum Faraday rotation angle (degrees)"},
        "offNadirAngle":   {"type": "text",
                            "hint": "Off-nadir angle(s), e.g. 34.3 or 21.5,26.2"},
        "jointObservation":{"type": "bool",
                            "hint": "Filter for joint ALOS PALSAR/AVNIR-2 observations"},
        "productionConfiguration": {"type": "text",
                            "hint": "Production configuration identifier"},
        "rangeBandwidth":  {"type": "text",
                            "hint": "Range bandwidth filter"},
    }
    # ─────────────────────────────────────────────────────────────────────────

    def __post_init__(self):
        if isinstance(self.workdir, str):
            self.workdir = Path(self.workdir).expanduser().resolve()

@dataclass
class S1_SLC_Config(ASF_Base_Config):
    name:str = "S1_SLC_Config"
    dataset: str | list[str] | None =  constants.DATASET.SENTINEL1
    instrument: str | None = constants.INSTRUMENT.C_SAR
    beamMode:str | None = constants.BEAMMODE.IW
    polarization: str|list[str] | None = field(default_factory=lambda: [constants.POLARIZATION.VV, constants.POLARIZATION.VV_VH])
    processingLevel: str | None = constants.PRODUCT_TYPE.SLC


@dataclass
class S1_Burst_Config(ASF_Base_Config):
    """Sentinel-1 SLC-BURST search + SAFE assembly.

    ASF distributes individual TOPS bursts as their own granules (dataset
    SLC-BURST). They are ~1/9th the size of a full slice, so an AOI-limited
    stack downloads far less data than the equivalent SLC search -- the point
    of burst-based processing.

    Bursts are not directly consumable by SAFE-expecting tools, so download()
    hands the selected granules to burst2safe, which assembles them into valid
    .SAFE directories (merging annotation/calibration/noise XML and writing a
    manifest). This mirrors the burst2stack CLI, except the granule list comes
    from *this* downloader's own search+filter rather than a second, independent
    search -- so the filters the user applied are the ones that govern what is
    actually assembled.

    Attributes:
        swaths: Subswaths to keep, e.g. ["IW2", "IW3"]. None = all three.
        mode: Acquisition mode passed to burst2safe (IW or EW).
        min_bursts: Minimum bursts per assembled SAFE; burst2safe pads with
            neighbouring bursts to reach it. Guards against a 1-burst SAFE that
            most downstream tools reject.
        all_anns: Include annotation for every subswath, not just the ones
            downloaded. Default True; turning it off makes a single-subswath
            SAFE unreadable by s1reader/COMPASS (see the field comment).
        keep_files: Keep the intermediate per-burst downloads after assembly.
    """
    name: str = "S1_Burst_Config"
    dataset: str | list[str] | None = constants.DATASET.SLC_BURST
    instrument: str | None = constants.INSTRUMENT.C_SAR
    beamMode: str | None = constants.BEAMMODE.IW
    polarization: str | list[str] | None = field(
        default_factory=lambda: [constants.POLARIZATION.VV])
    processingLevel: str | None = constants.PRODUCT_TYPE.BURST

    # ── burst -> SAFE assembly (burst2safe) ────────────────────────────────
    swaths: list[str] | None = None
    mode: str = "IW"
    min_bursts: int = 1
    # Default True, unlike burst2safe's own False. Annotation XML costs a few
    # hundred KB against ~500 MB of measurement data, and without it a
    # single-subswath SAFE is unreadable: s1reader derives the OPERA burst ID
    # from the IW2 mid-burst sensing time and opens the IW2 annotation whichever
    # swath you asked for. Verified -- an IW3-only SAFE with all_anns loads all
    # 4 IW3 bursts; without it, "ValueError: burst iw2-slc-vv not in SAFE". The
    # alternative is force-adding IW2 DATA, roughly doubling an IW1/IW3-only
    # download. Both COMPASS tutorials pass --all-anns unconditionally.
    all_anns: bool = True
    keep_files: bool = False
    # max_workers is inherited from ASF_Base_Config. For bursts it counts
    # concurrent DATE ASSEMBLIES rather than HTTP transfers: each unit is a
    # burst2safe run that also issues its own ASF search, so a high value
    # invites rate limits. Hence the lower default than S1_SLC.
    max_workers: int = 2

    # EXTEND the base groups, never replace them. _ui_groups/_ui_fields are
    # ClassVars, so assigning a fresh list here shadows ASF_Base_Config's
    # entirely -- which dropped AOI, dates, orbit/frame and every other search
    # field from the GUI form, leaving only the five assembly options.
    _ui_groups: ClassVar[list] = ASF_Base_Config._ui_groups + [
        {"label": "Burst assembly",
         "fields": ["swaths", "mode", "min_bursts", "all_anns", "keep_files"]},
    ]
    _ui_fields: ClassVar[dict] = {
        **ASF_Base_Config._ui_fields,
        "swaths":     {"type": "multiselect", "options": ["IW1", "IW2", "IW3"],
                       "hint": "Subswaths to keep. Empty = all three."},
        "mode":       {"type": "select", "options": ["IW", "EW"],
                       "hint": "Acquisition mode passed to burst2safe."},
        "min_bursts": {"type": "number", "step": 1,
                       "hint": "Minimum bursts per assembled SAFE; burst2safe pads with neighbours to reach it."},
        "all_anns":   {"type": "bool",
                       "hint": "Keep annotations for every burst in the slice, not just the selected ones."},
        "keep_files": {"type": "bool",
                       "hint": "Keep intermediate per-burst downloads after assembly."},
        "max_workers": {"type": "number", "min": 1, "max": 8, "step": 1,
                        "hint": "Concurrent DATE ASSEMBLIES, not HTTP transfers: each unit is a burst2safe run that unpacks and rewrites a whole SAFE, and issues its own ASF search. Keep low."},
    }


# ---------------------------------------------------------------------------
# Processor configurations
# ---------------------------------------------------------------------------
@dataclass
class ISCE2_S1_Config:
    """Configuration for the ISCE2 stackSentinel time-series processor.

    stackSentinel.py generates a set of numbered run scripts from all SLC files
    in slc_dir, then InSARHub executes them sequentially while parallelising the
    independent commands within each step.

    Attributes:
        workdir: Processing root.  All outputs (run_files/, merged/, etc.) live here.
        slc_dir: Directory containing all Sentinel-1 SLC .SAFE files (or .zips).
        orbit_dir: Directory with .EOF orbit files.  Created automatically if absent.
        aux_dir: Directory with Sentinel-1 AUX_CAL files.  Defaults to workdir/aux;
            ISCE2 downloads missing files there on first run.
        dem_path: ISCE2-binary DEM (dem.wgs84 + .xml sidecar).  When None, GLO-30
            is pre-downloaded, preferring the joint footprint of the actual SLCs
            found in slc_dir/workdir (union of every scene's manifest corners,
            covering every frame in a merged multi-frame stack) over bbox --
            the search AOI only reflects what was searched for, not what ASF
            actually returned (whole-scene footprints extend beyond it) or
            what a merge combined. bbox is used only as a fallback when no
            SLCs are on disk yet to derive a footprint from.
        isce_home: ISCE2 installation root.  Falls back to $ISCE_HOME env var.
        bbox: Area of interest as [S, N, W, E] degrees.  Only used as a DEM
            bbox fallback when no SLCs are present yet to auto-derive a
            footprint from (see dem_path above); otherwise informational.
        num_overlap_connections: Connections used for NESD azimuth coregistration.
        reference_date: Stack reference date YYYYMMDD.  None = stackSentinel auto-selects.
        coregistration: 'NESD' (default, more accurate) or 'geometry' (faster).
        max_workers: Parallel commands within each run step.
    """

    _ui_groups: ClassVar[list] = [
        {"label": "Paths",
         "fields": ["slc_dir", "orbit_dir", "aux_dir", "dem_path"]},
        {"label": "Area of interest",
         "fields": ["full_frame", "bbox"]},
        {"label": "Coregistration",
         "fields": ["coregistration", "esd_coherence_threshold", "snr_misreg_threshold"]},
        {"label": "Interferogram",
         "fields": ["workflow", "looks_range", "looks_azimuth", "filter_strength",
                    "rm_filter", "polarization", "unw_method", "virtual_merge"]},
        {"label": "Ionosphere",
         "fields": ["param_ion", "num_connections_ion"]},
        {"label": "Job",
         "fields": ["max_workers", "skip_existing", "num_proc", "num_proc4topo"]},
        {"label": "HPC (SLURM)",
         "fields": ["hpc_mode", "max_concurrent_hpc"]},
        {"label": "Container",
         "fields": ["container"]},
    ]
    _ui_fields: ClassVar[dict] = {
        "workdir":                  {"type": "text", "hint": "Processing root directory (auto = folder being processed)"},
        "slc_dir":                  {"type": "text", "hint": "Directory with Sentinel-1 SLC .SAFE files. 'auto' = workdir/slc"},
        "orbit_dir":                {"type": "text", "hint": "Directory with .EOF orbit files. Default = workdir/slc"},
        "aux_dir":                  {"type": "text", "hint": "Directory with AUX_CAL files. Default = workdir/slc"},
        "dem_path":                 {"type": "text", "hint": "ISCE2-format DEM, GeoTIFF, or directory. Default = workdir/dem (GLO-30 auto-downloaded if absent)"},
        "bbox":                     {"type": "text", "hint": "Bounding box S N W E, e.g. 33.0 38.0 -120.0 -115.0. Leave blank to auto-derive from SLC footprints"},
        "full_frame":               {"type": "bool",
                                     "hint": "Process full SLC frame — ignore AOI/bbox and let ISCE2 determine the extent"},
        "coregistration":           {"type": "select", "options": ["NESD", "geometry"],
                                     "hint": "NESD = geometry + ESD refinement (recommended); geometry = orbit-only (faster)"},
        "esd_coherence_threshold":  {"type": "number", "min": 0, "max": 1, "step": 0.05, "default": 0.7,
                                     "hint": "Min coherence in burst overlaps for ESD azimuth estimation"},
        "snr_misreg_threshold":     {"type": "number", "min": 1, "max": 30, "step": 1, "default": 10,
                                     "hint": "Min SNR for range misregistration cross-correlation"},
        "looks_range":              {"type": "number", "min": 1, "max": 100, "step": 1, "default": 20,
                                     "hint": "Range looks"},
        "looks_azimuth":            {"type": "number", "min": 1, "max": 20,  "step": 1, "default": 4,
                                     "hint": "Azimuth looks"},
        "filter_strength":          {"type": "number", "min": 0, "max": 1, "step": 0.05, "default": 0.5,
                                     "hint": "Goldstein filter strength"},
        "polarization":             {"type": "select", "options": ["vv", "vh"],
                                     "hint": "Polarization channel"},
        "unw_method":               {"type": "select", "options": ["snaphu", "icu"],
                                     "hint": "Phase unwrapping algorithm"},
        "workflow":                 {"type": "select",
                                     "options": ["interferogram", "slc", "offset",
                                                 "correlation", "dense_offsets", "ionosphere"],
                                     "hint": "Processing workflow type"},
        "rm_filter":                {"type": "bool",
                                     "hint": "Remove interferometric filter before unwrapping"},
        "virtual_merge":            {"type": "select", "options": ["", "True", "False"],
                                     "hint": "Virtual burst merging (leave blank to use ISCE2 default)"},
        "param_ion":                {"type": "text",
                                     "hint": "Path to ionosphere parameter file (leave blank to skip)"},
        "num_connections_ion":      {"type": "number", "min": 1, "max": 10, "step": 1, "default": 3,
                                     "hint": "Interferogram connections for ionosphere estimation"},
        "max_workers":              {"type": "number", "min": 1, "max": 16, "step": 1, "default": 4,
                                     "hint": "Parallel commands within each run step"},
        "skip_existing":            {"type": "bool",
                                     "hint": "Skip steps that already completed successfully"},
        "num_proc":                 {"type": "number", "min": 1, "max": 32, "step": 1, "default": 4,
                                     "hint": "ISCE2's own internal multiprocessing (stackSentinel.py --numProcess) for geo2rdr/resample steps (run_05/06/09/10) — separate from max_workers/cpus_per_task, must be matched to whatever cores those steps are actually given or the extra cores go unused"},
        "num_proc4topo":            {"type": "number", "min": 1, "max": 32, "step": 1, "default": 6,
                                     "hint": "ISCE2's own internal multiprocessing (stackSentinel.py --numProcess4topo) for the topo step (run_01) — same caveat as num_proc, matched to run_01's sbatch_options.json cpus_per_task"},
        "hpc_mode":                 {"type": "bool",
                                     "hint": "Submit a sbatch manager job per step; each manager controls child job submission in batches"},
        "max_concurrent_hpc":       {"type": "number", "min": 1, "max": 200, "step": 1, "default": 12,
                                     "hint": "Max concurrent child jobs submitted by each step manager (default 12)"},
        "dry_run":                  {"type": "bool",
                                     "hint": "Preview commands without executing (HPC: generate sbatch scripts only; local: print commands only)"},
        "sbatch_options_per_step":  {"type": "text",
                                     "hint": "JSON dict mapping step number (or 'default') to SLURM resource keys, e.g. {\"default\": {\"partition\": \"compute\", \"account\": \"myproj\", \"time\": \"04:00:00\", \"cpus_per_task\": 2, \"mem\": \"8G\"}, \"07\": {\"cpus_per_task\": 4, \"mem\": \"32G\"}}. Steps not listed inherit the 'default' entry."},
        "container":                {"type": "text",
                                     "hint": "Path to a .sif/Apptainer image or a Docker image reference with insarhub installed — re-runs this command inside the container instead of on the host. Not remembered between runs; pass again for retry/subsequent submits."},
    }

    name: str                             = "ISCE2_S1_Config"
    workdir: Path | str                   = field(default_factory=lambda: Path.cwd())
    slc_dir: Path | str                   = "auto"
    orbit_dir: Path | str | None          = "auto"
    aux_dir: Path | str | None            = "auto"
    dem_path: Path | str | None           = "auto"
    isce_home: Path | str | None          = "auto"
    saved_job_path: Path | str | None     = None
    # Area / dates
    full_frame: bool                      = False  # True = no bbox, process full SLC extent
    bbox: list[float] | None              = None   # [S, N, W, E]
    swath_num: str                        = "1 2 3"
    start_date: str | None                = None   # YYYY-MM-DD
    end_date: str | None                  = None
    exclude_dates: str | None             = None   # comma-separated YYYYMMDD
    include_dates: str | None             = None
    # Network
    num_overlap_connections: int          = 3
    reference_date: str | None            = None   # YYYYMMDD
    # Coregistration
    coregistration: str                   = "NESD"
    esd_coherence_threshold: float        = 0.7
    snr_misreg_threshold: float           = 10.0
    # Interferogram
    looks_range: int                      = 20
    looks_azimuth: int                    = 4
    filter_strength: float                = 0.5
    polarization: str                     = "vv"
    unw_method: str                       = "snaphu"
    workflow: str                         = "interferogram"
    rm_filter: bool                       = False
    virtual_merge: str | None             = None   # "True" | "False" | None (ISCE2 default)
    # Ionosphere
    param_ion: str | None                 = None
    num_connections_ion: int              = 3
    # Compute
    use_gpu: bool                         = False
    # Matched to the tuned sbatch_options.json defaults ("01"=6 cpus,
    # "09"/"10"=4 cpus) -- run_01/09/10's full-frame geometric work is
    # otherwise single-threaded regardless of how many SLURM cores a step
    # manager allocates (found via a real p100_f466 run: run_01 took 1h17m+
    # at num_proc4topo=1 on a 3-swath stack).
    num_proc: int                         = 4
    num_proc4topo: int                    = 6
    text_cmd: str                         = ""
    # Job control
    max_workers: int                      = 4
    skip_existing: bool                   = True
    submission_chunk_size: int            = 1
    # HPC / SLURM
    hpc_mode: bool                        = False
    max_concurrent_hpc: int               = 12
    dry_run: bool                         = False
    sbatch_options_per_step: dict           = field(default_factory=dict)
    container: str | None                 = None

    def __post_init__(self):
        _AUTO = {"auto", ""}

        # workdir must be resolved first so other "auto" paths can reference it
        if isinstance(self.workdir, str):
            self.workdir = Path(self.workdir).expanduser().resolve()

        # slc_dir: "auto" → workdir/slc
        if str(self.slc_dir).strip().lower() in _AUTO:
            self.slc_dir = Path(self.workdir) / "slc"
        elif isinstance(self.slc_dir, str):
            self.slc_dir = Path(self.slc_dir).expanduser().resolve()

        # orbit_dir / aux_dir: "auto" → workdir/slc (same folder as downloaded SLC/orbit files)
        for attr in ("orbit_dir", "aux_dir"):
            val = getattr(self, attr)
            if val is None or str(val).strip().lower() in _AUTO:
                setattr(self, attr, Path(self.workdir) / "slc")
            elif isinstance(val, str):
                setattr(self, attr, Path(val).expanduser().resolve())

        # dem_path: "auto" → workdir/dem directory
        if self.dem_path is None or str(self.dem_path).strip().lower() in _AUTO:
            self.dem_path = Path(self.workdir) / "dem"
        elif isinstance(self.dem_path, str):
            self.dem_path = Path(self.dem_path).expanduser().resolve()

        # isce_home / saved_job_path: "auto" / None → None (resolved at runtime)
        for attr in ("isce_home", "saved_job_path"):
            val = getattr(self, attr)
            if val is None or str(val).strip().lower() in _AUTO:
                setattr(self, attr, None)
            elif isinstance(val, str):
                setattr(self, attr, Path(val).expanduser().resolve())

        # full_frame clears bbox so the backend never uses a stale AOI
        if self.full_frame:
            self.bbox = None


@dataclass
class Hyp3_Base_Config:
    """
    Base configuration for HyP3 job interaction.

    This dataclass defines shared configuration options used for
    submitting, managing, and downloading jobs from the HyP3 API.

    Attributes:
        workdir (Path | str):
            Directory where downloaded products will be stored.
            If provided as a string, it will be converted to a
            resolved ``Path`` object during initialization.

        saved_job_path (Path | str | None):
            Optional path to a saved job JSON file for reloading
            previously submitted jobs. If provided as a string,
            it will be converted to a resolved ``Path`` object.

        earthdata_credentials_pool (dict[str, str] | None):
            Dictionary mapping usernames to passwords for managing
            multiple Earthdata accounts. Used for parallel or
            quota-aware submissions.

        skip_existing (bool):
            If True, skip submission or download of products that
            already exist locally.

        submission_chunk_size (int):
            Number of jobs submitted per batch request to the API.
            Helps avoid request size limits and API throttling.

        max_workers (int):
            Maximum number of worker threads used for concurrent
            submissions or downloads. Recommended to keep below 8
            to avoid overwhelming the API or triggering rate limits.
    """

    name: str = "Hyp3_Base_Config"
    workdir: Path | str = field(default_factory=lambda: Path.cwd())
    saved_job_path: Path | str | None = None
    earthdata_credentials_pool: dict[str, str] | None = None
    skip_existing: bool = True
    # Runtime-only, set by the CLI's --dry-run. Needs to be a real field so it
    # reaches the processor: Hyp3Base stamps insarhub_config.json from its
    # __init__, so without this a dry run -- which constructs a processor just
    # to preview -- rewrote the folder's config every time.
    dry_run: bool = False
    submission_chunk_size: int = 200
    max_workers: int = 4 # Multithreading <8 to avoid overwhelming the API and to be mindful of local resources, also avoid bans from too many requests. 

    def __post_init__(self):
        # Auto-convert string paths to Path objects
        if isinstance(self.workdir, str):
            self.workdir = Path(self.workdir).expanduser().resolve()
        if self.saved_job_path and isinstance(self.saved_job_path, str):
            self.saved_job_path = Path(self.saved_job_path).expanduser().resolve()


@dataclass
class Hyp3_S1_Config(Hyp3_Base_Config):
    """
    Configuration options for `hyp3_sdk` InSAR GAMMA processing jobs.

    This dataclass defines all parameters used when submitting
    InSAR jobs to the ASF HyP3 service using the GAMMA workflow.

    UI metadata is stored in ``_ui_groups`` / ``_ui_fields`` and consumed
    by the API layer to auto-generate the settings panel.

    Attributes:
        pairs (list[tuple[str, str]] | None):
            List of Sentinel-1 scene ID pairs in the form
            [(reference_scene, secondary_scene), ...].
            If None, pairs must be provided during submission.

        name_prefix (str | None):
            Prefix added to generated HyP3 job names.

        include_look_vectors (bool):
            If True, include look vector layers in the output product.

        include_los_displacement (bool):
            If True, include line-of-sight (LOS) displacement maps.

        include_inc_map (bool):
            If True, include incidence angle maps.

        looks (str):
            Multi-looking factor in the format "range x azimuth"
            (e.g., "20x4").

        include_dem (bool):
            If True, include the DEM used during processing.

        include_wrapped_phase (bool):
            If True, include wrapped interferometric phase output.

        apply_water_mask (bool):
            If True, apply a water mask during processing.

        include_displacement_maps (bool):
            If True, include unwrapped displacement maps.

        phase_filter_parameter (float):
            Phase filtering strength parameter (typically between 0 and 1).
            Higher values apply stronger filtering.
    """

    # ── UI metadata consumed by the API / settings panel ─────────────────────
    _ui_groups: ClassVar[list] = [
        {"label": "Processing",
         "fields": ["looks", "phase_filter_parameter", "name_prefix", "apply_water_mask"]},
        {"label": "Outputs",
         "fields": ["include_dem", "include_look_vectors", "include_inc_map",
                    "include_los_displacement", "include_wrapped_phase", "include_displacement_maps"]},
        {"label": "Job",
         "fields": ["skip_existing", "submission_chunk_size", "max_workers"]},
    ]
    _ui_fields: ClassVar[dict] = {
        "looks":                    {"type": "select", "options": ["20x4", "10x2"],
                                     "hint": "Range × azimuth looks (20x4 ≈ 80 m, 10x2 ≈ 40 m)"},
        "phase_filter_parameter":   {"type": "number", "min": 0, "max": 1, "step": 0.1,
                                     "default": 0.6,
                                     "hint": "Goldstein filter strength (0 = off, 1 = maximum)"},
        "name_prefix":              {"type": "text"},
        "apply_water_mask":         {"type": "bool"},
        "include_dem":              {"type": "bool"},
        "include_look_vectors":     {"type": "bool"},
        "include_inc_map":          {"type": "bool"},
        "include_los_displacement": {"type": "bool"},
        "include_wrapped_phase":    {"type": "bool"},
        "include_displacement_maps":{"type": "bool"},
        "skip_existing":            {"type": "bool",
                                     "hint": "Skip re-downloading already-completed jobs"},
        "submission_chunk_size":    {"type": "number", "min": 1, "max": 500, "step": 1,
                                     "default": 200,
                                     "hint": "Jobs per API batch request"},
        "max_workers":              {"type": "number", "min": 1, "max": 16, "step": 1,
                                     "default": 4,
                                     "hint": "Parallel download threads for completed job outputs (default 4)"},
    }
    # ─────────────────────────────────────────────────────────────────────────

    name: str = "Hyp3_S1_Config"
    pairs: list[tuple[str, str]] | None = None
    name_prefix: str | None = 'ifg'
    include_look_vectors:bool=True
    include_los_displacement:bool=False
    include_inc_map:bool=True
    looks:str='20x4'
    include_dem :bool=True
    include_wrapped_phase :bool=False
    apply_water_mask :bool=True
    include_displacement_maps:bool=True
    # 0.5 aligns the Goldstein filter strength with ISCE2_S1.filter_strength
    # and GMTSAR's phasefilt (hardcoded alpha=0.5), so the three backends
    # are comparable. ASF's own HyP3 default is 0.6.
    phase_filter_parameter :float=0.5


@dataclass
class GMTSAR_Base_Config:
    """Common GMTSAR configuration shared across all satellite platforms.

    Holds the sensor-agnostic GMTSAR processing parameters -- the 31
    pop_config parameters that are identical for every SAT (ERS/ENVI/ALOS/
    ALOS2/S1/CSK/TSX/RS2/GF3, verified by diffing `pop_config <SAT>` across
    all of them) -- plus the shared GMTSAR runtime (install paths, DEM,
    job knobs). Sensor-specific processors subclass this and add their own
    platform fields: GMTSAR_S1_Config adds slc_dir/orbit_dir/subswath/
    polarization and the 5 S1_TOPS-only processing params
    (spec_div/spec_mode/range_dec/azimuth_dec/det_stitch).

    Field defaults match `pop_config`'s own defaults exactly, so leaving
    them untouched reproduces GMTSAR's stock config.py; override any field
    to change the generated config.py (see GMTSAR_S1._write_config_py()).
    A value of -999 is GMTSAR's own "auto-derive / unset" sentinel.
    """

    _ui_groups: ClassVar[list] = [
        {"label": "GMTSAR runtime",
         "fields": ["sat", "dem_path", "dem_source", "dem_mode", "config_template",
                    "gmtsar_root", "gmtsar_env_bin", "use_python_framework"]},
        {"label": "GMTSAR: stages",
         "fields": ["proc_stage", "skip_stage", "skip_master",
                    "skip_1", "skip_2", "skip_3", "skip_4", "skip_5", "skip_6"]},
        {"label": "GMTSAR: preprocess",
         "fields": ["num_patches", "earth_radius", "near_range", "fd1", "region_cut"]},
        {"label": "GMTSAR: coregistration",
         "fields": ["coregistration", "esd_mode", "esd_network",
                    "esd_network_max_days", "esd_network_max_conn"]},
        {"label": "GMTSAR: topo",
         "fields": ["topo_phase", "topo_interp_mode", "shift_topo"]},
        {"label": "GMTSAR: interferogram + filter",
         "fields": ["switch_master", "filter_wavelength", "dec_factor",
                    "compute_phase_gradient", "correct_iono", "iono_filt_rng",
                    "iono_filt_azi", "iono_dsamp", "iono_skip_est"]},
        {"label": "GMTSAR: unwrap",
         "fields": ["threshold_snaphu", "coherence_mask_threshold", "near_interp",
                    "mask_water", "defomax"]},
        {"label": "GMTSAR: geocode",
         "fields": ["threshold_geocode"]},
        {"label": "Job",
         "fields": ["max_workers", "skip_existing", "dry_run"]},
    ]
    _ui_fields: ClassVar[dict] = {
        "sat":             {"type": "select",
                             "options": ["S1_TOPS", "S1_STRIP", "ALOS", "ALOS_SLC", "ALOS2",
                                         "ALOS2_SCAN", "ENVI", "ENVI_SLC", "ERS", "CSK_RAW",
                                         "CSK_SLC", "TSX", "RS2", "GF3"],
                             "hint": "GMTSAR SAT argument"},
        "dem_path":        {"type": "text", "hint": "GMTSAR-format DEM (topo/dem.grd). Leave blank to auto-download via GMTSAR make_dem (SRTM) from the SLC footprint / AOI."},
        "dem_source":      {"type": "select", "options": ["glo30", "srtm"],
                             "hint": "Auto-DEM source when dem_path is blank. glo30 = Copernicus GLO-30 via dem_stitcher (same DEM HyP3 and ISCE2_S1 use -- keeps the three backends comparable). srtm = GMTSAR's own make_dem (SRTM, resolution set by dem_mode)."},
        "dem_mode":        {"type": "select", "options": [1, 2], "hint": "make_dem SRTM resolution when auto-downloading: 1 = 1-arcsec (~30 m), 2 = 3-arcsec (~90 m)"},
        "config_template": {"type": "text", "hint": "Reuse an existing GMTSAR config.py verbatim (leave blank to generate from these fields)"},
        "gmtsar_root":     {"type": "text", "hint": "GMTSAR repo root -- its bin/ is prepended to subprocess PATH. Blank = auto-detect ($GMTSAR env var, then a known GMTSAR script on $PATH)"},
        "use_python_framework": {"type": "bool", "hint": "Call GMTSAR's Python framework (bin/<name>) instead of the classic csh (bin/<name>.csh). Same workflow and arguments -- only which implementation runs. snaphu.csh has no Python port and always uses csh."},
        "gmtsar_env_bin":  {"type": "text", "hint": "bin/ dir of the conda env GMTSAR needs (provides the real `gmt` binary + numba/scipy) -- InSARHub's own env has no `gmt` (real end-to-end test, 2026-07-21). Blank = auto-detect (`gmt` on $PATH, then a sibling conda env with `gmt` in its bin/)"},
        "proc_stage":      {"type": "number", "step": 1, "hint": "Start stage (1 preprocess ... 6 geocode)"},
        "skip_stage":      {"type": "text", "hint": "Comma-list of stages to skip, or -999 for none"},
        "skip_master":     {"type": "number", "step": 1, "hint": "Skip master preprocessing (0/1)"},
        "skip_1":          {"type": "number", "step": 1, "hint": "Skip stage 1"},
        "skip_2":          {"type": "number", "step": 1, "hint": "Skip stage 2"},
        "skip_3":          {"type": "number", "step": 1, "hint": "Skip stage 3"},
        "skip_4":          {"type": "number", "step": 1, "hint": "Skip stage 4"},
        "skip_5":          {"type": "number", "step": 1, "hint": "Skip stage 5"},
        "skip_6":          {"type": "number", "step": 1, "hint": "Skip stage 6"},
        "coregistration":  {"type": "select", "options": ["esd", "geometry"],
                             "hint": "Stack alignment (stack_mode). esd = enhanced spectral diversity (preproc_batch_tops_esd, matches ISCE2_S1 NESD -- the default); geometry = orbit/DEM only (preproc_batch_tops)."},
        "esd_mode":        {"type": "select", "options": [0, 1, 2],
                             "hint": "ESD averaging when coregistration=esd: 0 average, 1 median, 2 interpolation. Ignored for geometry."},
        "esd_network":     {"type": "bool",
                             "hint": "Refine alignment with a short-baseline ESD network inverted for per-date corrections (ISCE topsStack topology), instead of measuring each scene directly against a possibly distant super-master. Recommended for stacks spanning more than ~2 months."},
        "esd_network_max_days": {"type": "number", "step": 12,
                             "hint": "Longest temporal baseline measured directly by the ESD network (days). Larger = denser network but less reliable measurements."},
        "esd_network_max_conn": {"type": "number", "step": 1,
                             "hint": "Forward links per date in the ESD network. Must keep the network connected or the inversion is rank deficient."},
        "num_patches":     {"type": "number", "step": 1, "hint": "Number of patches (-999 = auto)"},
        "earth_radius":    {"type": "number", "hint": "Earth radius, m (-999 = auto from orbit)"},
        "near_range":      {"type": "number", "hint": "Near range, m (-999 = auto)"},
        "fd1":             {"type": "number", "hint": "Doppler centroid fd1 (-999 = auto)"},
        "region_cut":      {"type": "text", "hint": "Radar-coord crop 'x0/xN/y0/yN' (-999 = full frame)"},
        "topo_phase":      {"type": "number", "step": 1, "hint": "Subtract topographic phase (0/1)"},
        "topo_interp_mode":{"type": "number", "step": 1, "hint": "Topo interpolation mode (0/1)"},
        "shift_topo":      {"type": "number", "step": 1, "hint": "Shift topo to align w/ amplitude (0/1)"},
        "switch_master":   {"type": "number", "step": 1, "hint": "Swap master/aligned (0/1)"},
        "filter_wavelength":{"type": "number", "step": 10, "hint": "Gaussian filter wavelength, m"},
        "dec_factor":      {"type": "number", "step": 1, "hint": "Filter decimation factor"},
        "compute_phase_gradient":{"type": "number", "step": 1, "hint": "Compute phase gradient (0/1)"},
        "correct_iono":    {"type": "number", "step": 1, "hint": "Ionospheric correction (0/1)"},
        "iono_filt_rng":   {"type": "number", "step": 0.1, "hint": "Iono filter range"},
        "iono_filt_azi":   {"type": "number", "step": 0.1, "hint": "Iono filter azimuth"},
        "iono_dsamp":      {"type": "number", "step": 1, "hint": "Iono downsample factor"},
        "iono_skip_est":   {"type": "number", "step": 1, "hint": "Skip iono estimation (0/1)"},
        "threshold_snaphu":{"type": "number", "step": 0.05, "hint": "SNAPHU coherence threshold (0 = skip unwrap)"},
        "near_interp":     {"type": "number", "step": 1, "hint": "Nearest-neighbour interp of low-coh gaps (0/1)"},
        "mask_water":      {"type": "number", "step": 1, "hint": "Mask water before unwrap (0/1)"},
        "coherence_mask_threshold": {"type": "number", "step": 0.005,
                           "hint": "Stacked-coherence mask before snaphu (0 = off)"},
        "defomax":         {"type": "number", "step": 1, "hint": "SNAPHU max deformation phase cycles"},
        "threshold_geocode":{"type": "number", "step": 0.05, "hint": "Coherence threshold for geocoding"},
        "max_workers":     {"type": "number", "min": 1, "max": 16, "step": 1, "default": 4,
                             "hint": "Independent pairs processed concurrently"},
        "skip_existing":   {"type": "bool", "hint": "Skip pairs that already succeeded"},
        "dry_run":         {"type": "bool", "hint": "Stage the case and report what would run, without executing"},
    }

    name: str                         = "GMTSAR_Base_Config"
    workdir: Path | str               = field(default_factory=lambda: Path.cwd())
    dem_path: Path | str | None       = None   # None = auto-download (see dem_source)
    # glo30 (Copernicus GLO-30 via dem_stitcher) matches the DEM HyP3 and
    # ISCE2_S1 use, so backend comparisons aren't confounded by DEM source.
    # srtm falls back to GMTSAR's own make_dem.
    dem_source: str                   = "glo30"
    dem_mode: int                     = 1      # make_dem SRTM only: 1=1-arcsec, 2=3-arcsec
    sat: str                          = "S1_TOPS"
    config_template: Path | str | None = None
    max_workers: int                  = 4
    skip_existing: bool               = True
    dry_run: bool                     = False
    # GMTSAR's own runtime -- deliberately independent of whatever conda env
    # the InSARHub process itself runs under. See gmtsar_s1.py's
    # _subprocess_env() docstring for the real failure this fixes.
    gmtsar_root: Path | str | None    = None
    gmtsar_env_bin: Path | str | None = None
    # Drive GMTSAR through its Python framework (gmtsar/python/utils/*) rather
    # than the classic csh scripts. The fork installs both side by side in
    # bin/: the unsuffixed name is the Python port, <name>.csh the original,
    # so this only selects which of the two InSARHub invokes -- it does NOT
    # change the workflow's shape or its arguments. 84 of 86 csh scripts have
    # ports; snaphu.csh is one of the two that do not (and bin/snaphu is the
    # SNAPHU unwrapper binary, not a port), so that call stays on csh either
    # way -- see GMTSAR_S1._gmtsar_script().
    use_python_framework: bool        = True
    # Not persisted to insarhub_config.json (pass again for retry/subsequent
    # submits, same as ISCE2_S1_Config.container) -- see GMTSAR_S1's
    # _reinvoke_via_container() docstring.
    container: str | None             = None

    # ── GMTSAR processing params (common to every SAT; pop_config defaults) ──
    # stack alignment: "esd" (enhanced spectral diversity, preproc_batch_tops_esd
    # -- the structural analogue of ISCE2_S1's NESD coregistration, the default)
    # or "geometry" (orbit/DEM only, preproc_batch_tops).
    coregistration: str          = "esd"
    esd_mode: int                = 1     # 0 average, 1 median, 2 interpolation
    proc_stage: int              = 1
    skip_stage: int | str        = -999
    skip_1: int                  = 0
    skip_2: int                  = 0
    skip_3: int                  = 0
    skip_4: int                  = 0
    skip_5: int                  = 0
    skip_6: int                  = 0
    skip_master: int             = 0
    num_patches: int             = -999
    earth_radius: float          = -999
    near_range: float            = -999
    fd1: float                   = -999
    region_cut: int | str        = -999
    topo_phase: int              = 1
    topo_interp_mode: int        = 0
    shift_topo: int              = 0
    switch_master: int           = 0
    # GMTSAR overloads this parameter for two unrelated jobs:
    #   1. the Gaussian pre-filter kernel width, and
    #   2. the GEOCODED output posting -- proj_ra2ll sets the lon/lat grid to
    #      filter_wavelength/4 metres (proj_ra2ll:155, from the gauss_* file).
    #
    # It was previously 25, chosen only for (1): 25 m collapses the Gaussian
    # kernel to 1x1 (verified with make_gaussian_filter), leaving just the
    # Goldstein phasefilt (alpha=0.5, hardcoded), which matches ISCE/HyP3.
    # Good reasoning, but (2) made it unusable: 25/4 = 6.25 m is finer than
    # ANY multilooked S1 product, so proj_ra2ll -- a scatter via blockmedian +
    # xyz2grd, with nothing interpolating -- left ~99% of every *_ll.grd as
    # NaN. No look setting rescues it; 8x2 still only reaches ~4% fill.
    #
    # 320 gives an 80 m posting, exactly what HyP3 ships for the same 20x4
    # looks (its INT80 products), and comfortably coarser than the 74 m
    # ground pixel those looks produce. The cost is that the 11x11 Gaussian
    # comes back, so GMTSAR is no longer Goldstein-only like ISCE/HyP3 --
    # a filtering difference is a fair price for a raster that has data in
    # it. To restore the old behaviour you must also fix the posting; see
    # GMTSAR_S1._check_geocode_posting(), which now refuses the combination
    # instead of letting it fail silently at MintPy load time.
    filter_wavelength: int       = 320
    dec_factor: int              = 2
    compute_phase_gradient: int  = 0
    correct_iono: int            = 0
    iono_filt_rng: float         = 1.0
    iono_filt_azi: float         = 1.0
    iono_dsamp: int              = 1
    iono_skip_est: int           = 1
    # >0 enables SNAPHU unwrapping; GMTSAR's default 0 SKIPS unwrapping, which
    # leaves no unwrap.grd for any SBAS/MintPy step downstream.
    threshold_snaphu: float      = 0.1
    # 1 -> snaphu_interp.csh, which fills low-coherence gaps before
    # unwrapping. This is what GMTSAR's own Sentinel-1 time-series recipe
    # uses at every unwrapping step ("snaphu_interp.csh 0.01 40" / "0.001
    # 40"), and it is what GMTSAR's native `sbas` needs, since that inversion
    # wants every pixel valid. Was 0 for a while so GMTSAR interferograms
    # would match ISCE topsStack's (which has no such step) before both fed
    # the same MintPy; following the recipe takes precedence, and on a
    # multi-subswath stack this now only affects the single unwrap of the
    # merged frame -- the per-subswath runs no longer unwrap at all (see
    # GMTSAR_S1._config_with_stage). Note for backend comparisons: GMTSAR
    # interferograms are gap-interpolated where ISCE's are not; MintPy
    # re-masks by coherence and inverts per-pixel, so the effect there is
    # small, but it is not nothing.
    near_interp: int             = 1
    # 0 by default, but no longer because the landmask is unusable -- that
    # earlier finding was specific to intf_tops.csh, which derives the mask
    # region from `gmt grdinfo phase.grd -I-` (with the `if (region_cut ==
    # "")` guard above it commented out, so it always overrides) at a point
    # where that read can fail; landmask.csh then ran with an empty region
    # and the broken landmask_ra.grd corrupted snaphu's phase_patch (real
    # failure: no unwrap.grd at all). That call sits inside intf_tops.csh's
    # `if (threshold_snaphu != 0)` block, and a multi-subswath stack now
    # passes threshold_snaphu = 0 there (see GMTSAR_S1._config_with_stage),
    # so the fragile path is never reached. The merge stage's landmask is a
    # different, sound one: it reads `gmt grdinfo phasefilt.grd -I-` -- the
    # same file GMTSAR's own recipe has you read by hand -- and honours an
    # explicit region_cut. Left 0 because that recipe recommends a landmask
    # only "if you have large areas of open water in your region of
    # interest"; set 1 for coastal/lake scenes, where it also speeds
    # unwrapping up ("if you leave those areas in, the unwrapping process
    # may take longer than it should").
    mask_water: int              = 0
    # Stacked-coherence mask applied BEFORE snaphu (GMTSAR recipe section 8b,
    # "Unwrapping in Regions of Poor Coherence"): the mean coherence over all
    # merged pairs is thresholded into mask_def.grd, which snaphu.csh /
    # snaphu_interp.csh multiply into the correlation grid before writing
    # snaphu's input -- so masked ground never enters the solve at all. This is
    # stronger than threshold_snaphu alone, which is per-pair: a pixel that is
    # bad across the whole stack gets removed everywhere, rather than sneaking
    # through in whichever pairs happen to clear the threshold. It is also the
    # main lever on unwrapping cost, which dominates a multi-subswath run
    # (single pairs took >4 h on the merged frame). 0 disables the mask.
    # 0.075 is the recipe's own example value.
    coherence_mask_threshold: float = 0.075
    # 0 per GMTSAR's recipe: "Maximum discontinuity threshold is usually set
    # to zero for interseismic motion, where there are no large
    # displacements. A number greater than zero will allow for phase jumps
    # along discontinuities such as earthquake ruptures." The recipe's own
    # examples use 40 because its scene is the 2018 Kilauea eruption; for
    # subsidence/interseismic monitoring 0 is the value it prescribes.
    defomax: int                 = 0
    threshold_geocode: float     = 0.10

    # Ordered names of the GMTSAR config.py processing params this class
    # owns -- GMTSAR_S1._write_config_py() writes these (plus any the
    # subclass adds via GMTSAR_CONFIG_PARAMS) into config.py. Kept as an
    # explicit list, not derived from fields(), so runtime/infra fields
    # (workdir, gmtsar_root, ...) are never mistaken for GMTSAR params.
    GMTSAR_CONFIG_PARAMS: ClassVar[tuple] = (
        "proc_stage", "skip_stage", "skip_1", "skip_2", "skip_3", "skip_4",
        "skip_5", "skip_6", "skip_master", "num_patches", "earth_radius",
        "near_range", "fd1", "region_cut", "topo_phase", "topo_interp_mode",
        "shift_topo", "switch_master", "filter_wavelength", "dec_factor",
        "compute_phase_gradient", "correct_iono", "iono_filt_rng",
        "iono_filt_azi", "iono_dsamp", "iono_skip_est", "threshold_snaphu",
        "near_interp", "mask_water", "defomax", "threshold_geocode",
    )


@dataclass
class GMTSAR_S1_Config(GMTSAR_Base_Config):
    """Configuration for the GMTSAR p2p_processing Sentinel-1 processor.

    p2p_processing generates one interferogram per (reference, secondary)
    pair invocation, run from a shared GMTSAR case directory (raw/, topo/,
    config.py). InSARHub parallelises independent pairs up to max_workers,
    mirroring how ISCE2_S1 parallelises independent commands within a step.

    Output lands in <workdir>/gmtsar/intf/<julian_date_pair>/ (e.g.
    intf/2019184_2019196/ -- GMTSAR's own Julian-date pair naming, NOT
    ref/sec stems, confirmed via a real run) using GMTSAR's native file
    names (corr_ll.grd, phasefilt_ll.grd, *.PRM files) -- this is already
    exactly what MintPy's own prep_gmtsar.py expects, so no
    output-normalization step is needed before handing off to a Mintpy
    analyzer.

    Two distinct GMTSAR entry points are supported, chosen automatically
    from whether `subswath` names one IW or several (via
    GMTSAR_S1._multiswath), because they have genuinely different real CLI
    contracts (confirmed against gmtsar/python/utils/p2p_processing and
    utils/p2p_S1_TOPS_Frame directly, and against real recipes in
    gmtsar/python/tests/recipes/):

    Both modes take the SAME pairs shape:
        pairs = [(ref_safe, ref_eof, sec_safe, sec_eof), ...] -- .SAFE
        directory names + matching .EOF orbit filenames.

    subswath names exactly one IW (e.g. "2") -- single-subswath, via
        p2p_processing. p2p_processing does not read .SAFE directories
        itself -- it expects one subswath's .tiff/.xml already extracted to
        raw/ under matching stem names (Sentinel-1's own naming:
        s1a-iw<N>-slc-<pol>-<start>-<end>-<orbit>-<mission>-<swath>).
        GMTSAR_S1 does that extraction itself, so callers only ever hand it
        raw .SAFE/.EOF names, same as multi-subswath mode. One shared
        case_dir for the whole pairs list; output in
        intf/<julian_date_pair>/ (GMTSAR-assigned, pair-namespaced).

    subswath names more than one IW (e.g. "1 2 3", the default) --
        multi-subswath Frame, via p2p_S1_TOPS_Frame, which always processes
        every subswath present (it takes no subswath argument itself).
        p2p_S1_TOPS_Frame is NOT pair-namespaced (it always writes to
        F1/F2/F3/merge/ in its current working directory), so each pair
        gets its OWN case subdirectory (case_dir/<ref>_<sec>/) rather
        than sharing one -- otherwise a second pair would silently
        overwrite the first pair's merge/ output.

    Attributes:
        workdir: Processing root. gmtsar/ (raw/, topo/, config.py,
            intf/ or per-pair subdirs) lives here.
        slc_dir: Directory containing Sentinel-1 SLC .SAFE dirs (or .zips).
        orbit_dir: Directory with .EOF orbit files.
        dem_path: GMTSAR-format DEM grid (topo/dem.grd). Unlike ISCE2_S1,
            bbox-driven auto-download is NOT implemented yet -- must be
            supplied explicitly. See gmtsar_s1.py's module docstring for
            the concrete "known gaps" list.
        sat: p2p_processing's SAT argument (single-subswath mode only).
            Exposed (not hardcoded) for forward-compat -- GMTSAR already
            supports 14 sensor families beyond S1_TOPS (see
            gmtsar/python/tests/cases.py upstream), this processor is
            just the first one wired in.
        subswath: IW subswath(s), ISCE-style space-separated (e.g. "1 2 3"
            = full frame merged via p2p_S1_TOPS_Frame, "2" = single-subswath
            via p2p_processing). p2p_processing itself does not read .SAFE
            directories -- it expects one subswath's .tiff/.xml files
            already extracted to matching-stem files in raw/ (confirmed
            against GMTSAR's own bundled single-subswath test fixture,
            H_res/raw/: its per-stem files are plain symlinks into the
            equivalent Frame-mode F<N>/ subswath files pulled from the same
            .SAFE). GMTSAR_S1 does this extraction itself. Default "1 2 3"
            (full frame, matching stack_mode's own default).
        parallel: p2p_S1_TOPS_Frame's own internal subswath parallelism
            flag (0=sequential, 1=parallel). Only used in multi-subswath mode.
        config_template: Path to a GMTSAR config.py to reuse as-is. If
            None, one is auto-generated per case via `pop_config <sat>`
            (GMTSAR's own default-config tool), matching p2p_processing's
            own "no config.py given" behavior.
        max_workers: Independent pairs processed concurrently.
        skip_existing: Don't redo a pair whose output dir already
            has a .succeeded status marker.
        gmtsar_root: GMTSAR repo root ($GMTSAR). Required -- GMTSAR_S1
            raises at construction time if unset. Its bin/ is prepended
            to every GMTSAR subprocess call's PATH.
        gmtsar_env_bin: bin/ dir of the conda env GMTSAR needs (provides
            the real `gmt` binary plus numba/scipy). Required -- InSARHub's
            own env does not provide `gmt` at all (confirmed via a real
            end-to-end test, 2026-07-21), so subprocess calls fail
            near-instantly without this. See gmtsar_s1.py's
            _subprocess_env() docstring for the full writeup.
    """

    # S1-specific groups first, then the common GMTSAR processing groups
    # inherited from GMTSAR_Base_Config (so the GUI form shows Sentinel-1
    # I/O + TOPS knobs up top, the shared processing params below).
    _ui_groups: ClassVar[list] = [
        {"label": "Paths",
         "fields": ["slc_dir", "orbit_dir", "dem_path"]},
        {"label": "Area of interest",
         "fields": ["AOI"]},
        {"label": "Sentinel-1",
         "fields": ["subswath", "parallel", "polarization"]},
        {"label": "Sentinel-1 TOPS processing",
         "fields": ["spec_div", "spec_mode", "range_dec", "azimuth_dec", "det_stitch"]},
        {"label": "Time-series stack",
         "fields": ["stack_mode", "reference"]},
        {"label": "HPC (SLURM)",
         "fields": ["hpc_mode", "max_concurrent_hpc"]},
        {"label": "Container",
         "fields": ["container"]},
    ] + GMTSAR_Base_Config._ui_groups
    _ui_fields: ClassVar[dict] = {
        **GMTSAR_Base_Config._ui_fields,
        "slc_dir":         {"type": "text", "hint": "Directory with Sentinel-1 SLC .SAFE files"},
        "orbit_dir":       {"type": "text", "hint": "Directory with .EOF orbit files"},
        "AOI":             {"type": "text", "hint": "Geographic area of interest: same formats the downloader --aoi accepts (a bbox [min_lon, min_lat, max_lon, max_lat], a spatial file path, or a WKT string). Auto-converted to GMTSAR region_cut (radar coords) via SAT_llt2rat, cropping processing to this area, and used as the DEM-download extent fallback. Blank = read from the workdir's insarhub_config.json (downloader intersectsWith); still blank = full frame + SLC-footprint DEM."},
        "subswath":        {"type": "text",
                             "hint": "IW subswath(s), ISCE-style space-separated (e.g. \"1 2 3\" = full frame merged via p2p_S1_TOPS_Frame, \"2\" = single-subswath via p2p_processing). Same field also controls stack_mode's multi-swath handling: more than one subswath runs a separate F<N> pipeline per swath and then merges them (merge_batch); a single subswath keeps the flat layout."},
        "parallel":        {"type": "bool", "hint": "p2p_S1_TOPS_Frame internal subswath parallelism (multi-subswath mode only)"},
        "polarization":    {"type": "select", "options": ["vv", "vh"], "hint": "Polarization channel"},
        "stack_mode":      {"type": "bool", "hint": "Time-series stack: align whole stack to one super-master + batch interferograms + baseline_table.dat (for GMTSAR_SBAS / GMTSAR_MINTPY_SBAS). Off = independent per-pair."},
        "reference":       {"type": "text", "hint": "Super-master scene for stack_mode (blank = earliest scene)"},
        "spec_div":        {"type": "number", "step": 1, "hint": "S1_TOPS spectral diversity ESD (0/1)"},
        "spec_mode":       {"type": "number", "step": 1, "hint": "S1_TOPS spectral-diversity mode"},
        "range_dec":       {"type": "number", "step": 1, "hint": "Range decimation factor"},
        "azimuth_dec":     {"type": "number", "step": 1, "hint": "Azimuth decimation factor"},
        "det_stitch":      {"type": "number", "step": 1, "hint": "Detrend/stitch across frames (0/1)"},
        "hpc_mode":        {"type": "bool",
                             "hint": "stack_mode only: submit a sbatch manager job per stack stage (align/intf/merge); each manager controls child job submission in batches"},
        "max_concurrent_hpc": {"type": "number", "min": 1, "max": 200, "step": 1, "default": 12,
                             "hint": "Max concurrent child jobs submitted by each stage manager (default 12)"},
        "container":       {"type": "text",
                             "hint": "Path to a .sif/Apptainer image or a Docker image reference with insarhub installed — re-runs this command inside the container instead of on the host (HPC mode: only each stage's child jobs run in the container; the sbatch manager scaffolding stays on the host). Not remembered between runs; pass again for retry/subsequent submits."},
    }

    # S1-specific processing params (present in S1_TOPS's pop_config but NOT
    # common to every SAT). Appended to the base list so _write_config_py()
    # emits them into config.py too.
    GMTSAR_CONFIG_PARAMS: ClassVar[tuple] = GMTSAR_Base_Config.GMTSAR_CONFIG_PARAMS + (
        "spec_div", "spec_mode", "range_dec", "azimuth_dec", "det_stitch",
    )

    name: str                     = "GMTSAR_S1_Config"
    slc_dir: Path | str | None    = "auto"
    orbit_dir: Path | str | None  = "auto"
    # Geographic AOI -> auto-converted to region_cut (radar coords) via
    # SAT_llt2rat, and used as the DEM-download extent fallback. Accepts any
    # form utils.tool._to_wkt does (same as the downloader --aoi): a bbox
    # [min_lon, min_lat, max_lon, max_lat], a spatial file path, or a WKT
    # string. None = read from workdir insarhub_config.json (downloader
    # intersectsWith); if still None, full frame + SLC-footprint DEM.
    AOI: str | list[float] | None = None
    # NOTE: AOI additionally drives the reframing step that runs before
    # alignment (GMTSAR S1 TOPS tutorial 3c/4b: pins.ll + the
    # create_frame_tops.csh core of organize_files_tops.csh). Every scene is
    # cut to ONE common along-track window = the AOI clipped to what the
    # scenes actually share, so an AOI larger than the stack reduces to the
    # scenes' common footprint. AOI None/blank = FULL FRAME, no cut at all.
    # This is deliberately
    # not a separate switch: reframing is part of the documented workflow,
    # and its extent is exactly what AOI already means. See
    # GMTSAR_S1._reframe_scenes().
    # ISCE-style space-separated subswath list, matching ISCE2_S1_Config.swath_num.
    # "1 2 3" = full frame, so GMTSAR covers the same ground as HyP3/ISCE by
    # default. A bare int still works (back-compat). Also decides single- vs
    # multi-subswath dispatch for non-stack (per-pair) mode -- see
    # GMTSAR_S1._multiswath -- since a separate frame_mode flag would just
    # be redundant with this field's own shape.
    subswath: int | str           = "1 2 3"
    parallel: bool                = True
    polarization: str             = "vv"
    # Time-series stack mode: instead of processing each pair independently
    # (per-pair p2p_processing/p2p_S1_TOPS_Frame), align the WHOLE stack to
    # one shared super-master and batch-generate interferograms, producing a
    # single baseline_table.dat spanning every date -- the coherent stack a
    # GMTSAR_SBAS / GMTSAR_MINTPY_SBAS analyzer needs. See gmtsar_s1.py's
    # _run_stack() for the pipeline (shells out to GMTSAR's own
    # preproc_batch_tops / intf_tops_parallel / merge_batch).
    stack_mode: bool              = False
    # Super-master scene for stack_mode (first line of data.in, every date
    # aligned to it). None = use the earliest scene across all pairs.
    reference: str | None         = None
    # ── S1_TOPS-only GMTSAR processing params (pop_config S1_TOPS extras) ──
    spec_div: int                 = 0
    spec_mode: int                = 1
    # 20x4 matches Hyp3_S1_Config.looks ("20x4") and ISCE2_S1_Config
    # looks_range/looks_azimuth, so all three backends multilook the same.
    # (GMTSAR's own defaults are 8/2.)
    #
    # NOTE these are coupled to filter_wavelength. proj_ra2ll geocodes onto a
    # grid of filter_wavelength/4 metres, so that posting must stay COARSER
    # than the multilooked ground pixel or the projection leaves holes:
    # 20x4 + the stock 200 m filter geocodes 74 m data onto a 50 m grid and
    # drops a third of it. GMTSAR_S1._check_geocode_posting() enforces the
    # relation; see it for the arithmetic and the escape hatch below.
    range_dec: int                = 20
    azimuth_dec: int              = 4
    det_stitch: int               = 0
    #: Skip the filter_wavelength/looks consistency check and geocode anyway,
    #: accepting a sparse (holey) *_ll.grd. Only useful when you want the
    #: finer looks and intend to fill or resample the grids yourself.
    allow_sparse_geocode: bool    = False
    # ── Network ESD misregistration (stack_mode only) ───────────────────────
    # preproc_batch_tops[_esd].csh measures every scene's azimuth
    # misregistration DIRECTLY against the super-master, so a scene 144 days
    # out is measured across 144 days -- where the burst-overlap ESD estimate
    # is unreliable. The residual appears as phase steps at burst boundaries
    # that grow with distance from the master (measured on p100_f466: a
    # tutorial-exact stock-GMTSAR run shows ~20 discontinuities at 11-13x
    # background, while a same-scene p2p run shows none).
    #
    # esd_network adds a stage that instead measures a SHORT-baseline network
    # (where ESD is reliable) and inverts it for per-date corrections -- the
    # topology ISCE topsStack uses (run_07_pairs_misreg +
    # run_08_timeseries_misreg), which holds ~0.0005 px residual flat from 12
    # to 144 days out on this same stack with the same master.
    # See gmtsar_s1.py's _run_esdnet_unit() and _gmtsar_esd_network.py.
    esd_network: bool             = False
    esd_network_max_days: int     = 48   # longest pair to measure directly
    esd_network_max_conn: int     = 3    # forward links per date
    # ── HPC / SLURM (stack_mode only) ───────────────────────────────────────
    # Mirrors ISCE2_S1_Config's hpc_mode/max_concurrent_hpc/sbatch_options_per_step:
    # each stack stage (align_F<N>/intf_F<N>/merge) gets its own sliding-window
    # sbatch manager instead of _run_stack() running as a local background
    # thread. See gmtsar_s1.py's _submit_stack_hpc() for the pipeline.
    hpc_mode: bool                = False
    max_concurrent_hpc: int       = 12
    sbatch_options_per_step: dict = field(default_factory=dict)


def _aoi_bbox_from_folder(folder) -> list[float] | None:
    """(W, S, E, N) from a job folder's saved downloader ``intersectsWith``.

    The downloader records the exact geometry it searched with in the folder's
    insarhub_config.json. Processors want a bbox, so the polygon is reduced to
    its bounds. Returns None -- never raises -- when the folder has no config,
    no intersectsWith, or an unparseable one; callers treat that as "not known
    yet" and fall back.
    """
    try:
        from insarhub.utils.config_io import read_insarhub_config
        from insarhub.utils.tool import _to_wkt
        from shapely import wkt as _wkt

        raw = (read_insarhub_config(folder)
               .get("downloader", {}).get("config", {}).get("intersectsWith"))
        if not raw:
            return None
        w, s, e, n = _wkt.loads(_to_wkt(raw)).bounds
        return [round(float(w), 6), round(float(s), 6),
                round(float(e), 6), round(float(n), 6)]
    except Exception:                                            # noqa: BLE001
        return None


@dataclass
class ISCE3_Burst_Config:
    """ISCE3 + COMPASS burst-based Sentinel-1 processor.

    Consumes the .SAFE directories S1_Burst assembles, and follows the COMPASS
    stack workflow: geocode each burst independently onto a common UTM grid
    (CSLC), then form interferograms directly in map coordinates.

    Note what is absent compared with ISCE2_S1 and GMTSAR_S1: there is no
    reference scene, no coregistration and no misregistration estimate. Each
    burst is geocoded to ABSOLUTE coordinates via orbit + DEM + timing LUTs, so
    two acquisitions land on the same grid because both were geocoded there --
    not because one was warped onto the other. Accuracy therefore depends on
    absolute geolocation, and cannot degrade with distance from a master.

    Attributes:
        workdir: Processing root. Holds slc/ (scenes + orbits), dem/, tec/,
            cslc/, configs/, interferograms/ -- all lowercase.
        slc_dir: .SAFE directories (from S1_Burst). Defaults to workdir/slc.
        orbit_dir: Precise orbits (.EOF). Defaults to slc_dir -- S1_Burst writes
            .EOF alongside the scenes, the same layout S1_SLC produces, so one
            directory holds both.
        dem_path: Copernicus DEM GeoTIFF. Defaults to workdir/dem/cop_dem.tif.
        tec_dir: IONEX global ionosphere maps. Defaults to workdir/tec.
        cslc_dir: Geocoded burst SLCs. Defaults to workdir/cslc.
        burst_db_path: OPERA burst-ID database (sqlite3) used by
            s1_geocode_stack.py to resolve burst IDs. Defaults to
            workdir/../s1-burst-db/opera-burst-bbox-only.sqlite3, matching the
            notebook; downloaded on demand if absent.
        AOI: (W, S, E, N) in degrees. Drives the DEM footprint and the CSLC
            bounding box.
        dem_buffer_deg: Degrees of padding around AOI for the DEM. The notebook
            uses 2 -- the DEM must cover every downloaded burst, not just the
            AOI, because geocoding reaches beyond the target area.
        dem_source: sardem source; COP = Copernicus GLO-30.
        water_mask: Also fetch the NASADEM water-body mask, used to blank water
            before unwrapping.
        tec_sol_code: IONEX analysis centre ('jpl', 'cod', 'igs', ...).
        x_posting/y_posting: CSLC output pixel spacing in metres. COMPASS
            defaults are 5/10; the notebooks use 10/20 to cut runtime.
        rglks/azlks: Multilooking applied to the stitched interferogram.
        filt_strength: Goldstein filter alpha.
        n_connections: Sequential pairing -- connect each date to its next N.
            Maps onto dolphin's Network(max_bandwidth=N).
        max_temporal_baseline: Pair by days instead of neighbour count. When
            set it replaces n_connections (dolphin honours one or the other).
        crop_buffer_deg: Padding around AOI when cropping CSLC bursts, so
            filtering and unwrapping do not begin at a nodata edge.
        coh_window: Window in pixels for coherence estimation. Estimated after
            Goldstein filtering, following the COMPASS notebook -- so it is a
            filtered-phase quality measure, comparable to ISCE's filt_fine.cor,
            not a raw normalised cross-correlation.
        unwrap_nlooks: Effective look count handed to snaphu; controls how
            strongly it trusts the coherence.
        unwrap_tiles: snaphu tiles per side (2 = 2x2).
        unwrap_cost/unwrap_init_method: snaphu cost mode and initialisation.
        max_workers: Concurrent s1_cslc.py / interferogram jobs.
    """
    name: str                     = "ISCE3_Burst_Config"
    workdir: str | None           = None
    slc_dir: str | None           = None
    orbit_dir: str | None         = None
    dem_path: str | None          = None
    tec_dir: str | None           = None
    cslc_dir: str | None          = None
    burst_db_path: str | None     = None

    AOI: list[float] | None       = None
    # Seeded from the downloader's intersectsWith in __post_init__, so AOI is
    # normally already filled. Tick this to process the whole downloaded burst
    # extent instead, ignoring AOI entirely.
    process_full_extent: bool     = False

    # ── DEM ────────────────────────────────────────────────────────────────
    # 0.5 deg, not the notebook's 2.0. The buffer only has to cover how far
    # geocoding reaches past the AOI -- roughly one burst, ~0.2 deg along
    # track -- and 2.0 produced a 1.55 GB DEM for a 0.36 x 0.24 deg AOI that
    # every one of ~200 cslc child jobs then had to read.
    dem_buffer_deg: float         = 0.5
    dem_source: str               = "COP"
    water_mask: bool              = True

    # ── ionosphere ─────────────────────────────────────────────────────────
    tec_sol_code: str             = "jpl"
    # Tried in order when tec_sol_code has no map for a date. CDDIS coverage is
    # uneven per centre -- JPL has a real hole from 2023-08-11 to 2023-10-10
    # that only UPC fills.
    tec_fallback_codes: list[str] = field(
        default_factory=lambda: ["igs", "cod", "esa", "upc"])

    # ── CSLC geocoding ─────────────────────────────────────────────────────
    x_posting: float              = 10.0
    y_posting: float              = 20.0
    common_bursts_only: bool      = True

    # ── interferogram ──────────────────────────────────────────────────────
    rglks: int                    = 4
    azlks: int                    = 2
    filt_strength: float          = 0.5
    n_connections: int            = 3
    max_temporal_baseline: float | None = None
    crop_buffer_deg: float        = 0.05
    coh_window: int               = 11

    # ── wrapped-phase estimator ────────────────────────────────────────────
    # phase_link, not network. Every pair contributes to the estimate rather
    # than a chosen subset, which on the Hawaii test stack gave one connected
    # component at 83% coverage against three at 55%, and cut closure error
    # from 0.157 to 0.067 rad. It costs more compute (full covariance per
    # ministack) but the pairwise network remains one field away.
    ifg_mode: str                 = "phase_link"
    pl_ifg_network: str           = "single_reference"
    pl_half_window_y: int         = 7
    pl_half_window_x: int         = 14
    pl_ministack_size: int        = 15
    pl_shp_method: str            = "glrt"
    pl_shp_alpha: float           = 0.001
    pl_use_evd: bool              = False
    pl_beta: float                = 0.0
    pl_baseline_lag: int | None   = None

    # ── unwrapping ─────────────────────────────────────────────────────────
    unwrap_nlooks: float          = 8.0
    unwrap_tiles: int             = 2
    unwrap_cost: str              = "smooth"
    unwrap_init_method: str       = "mcf"

    # ── execution ──────────────────────────────────────────────────────────
    max_workers: int              = 3
    hpc_mode: bool                = False
    max_concurrent_hpc: int       = 12
    sbatch_options_per_step: dict = field(default_factory=dict)
    # Container image (Apptainer .sif path or Docker image ref) to re-invoke
    # the whole pipeline inside, instead of running on the host. The image is
    # expected to have `insarhub` plus ISCE3/COMPASS installed.
    container: str | None         = None
    # Set by the CLI's --dry-run. Must exist as a real field: the CLI puts
    # dry_run into its overrides dict, but only keys that are actual dataclass
    # fields survive the filter into the config -- so without this, --dry-run
    # was silently discarded and _submit_hpc's `if config.dry_run` never fired.
    # The result was a "dry run" that really did sbatch every job.
    dry_run: bool                 = False

    def __post_init__(self):
        """Fill derived values in, so the panel shows what will actually be used.

        Both of these used to be left None and resolved deep inside the
        processor at run time, so the settings panel rendered empty boxes for
        values that were in fact well-defined -- and anyone typing into them had
        no idea what the default would have been.

        ``burst_db_path`` lands inside the job folder, beside the assembled
        .SAFE stack it describes, so a stack is self-contained and can be moved
        or archived whole. The trade is one ~100 MB copy of the OPERA burst-ID
        database per job folder; point several stacks at one shared path by
        hand if that matters. The processor still fetches it on demand.

        ``AOI`` is seeded from the downloader's ``intersectsWith`` in the same
        folder -- the geometry the search actually ran with -- converted to the
        (W, S, E, N) bbox this processor wants. That is almost always the right
        answer, and having it visible means it can be edited rather than
        guessed at. Set ``process_full_extent`` to process the whole downloaded
        extent regardless.
        """
        from pathlib import Path as _P

        if not self.workdir:
            return
        wd = _P(self.workdir).expanduser().resolve()

        if not self.burst_db_path:
            self.burst_db_path = str(
                wd / "s1-burst-db" / "opera-burst-bbox-only.sqlite3")

        if not self.AOI:
            self.AOI = _aoi_bbox_from_folder(wd)

    _ui_groups: ClassVar[list] = [
        {"label": "Paths",
         "fields": ["workdir", "slc_dir", "orbit_dir", "dem_path", "tec_dir",
                    "cslc_dir", "burst_db_path"]},
        {"label": "DEM & water mask",
         "fields": ["AOI", "process_full_extent", "dem_buffer_deg", "dem_source", "water_mask"]},
        {"label": "Ionosphere",
         "fields": ["tec_sol_code"]},
        {"label": "CSLC geocoding",
         "fields": ["x_posting", "y_posting", "common_bursts_only"]},
        {"label": "Interferogram",
         "fields": ["ifg_mode", "rglks", "azlks", "filt_strength",
                    "n_connections", "max_temporal_baseline",
                    "crop_buffer_deg", "coh_window"]},
        {"label": "Phase linking (ifg_mode = phase_link)",
         "fields": ["pl_ifg_network", "pl_half_window_y", "pl_half_window_x", "pl_ministack_size",
                    "pl_shp_method", "pl_shp_alpha", "pl_use_evd", "pl_beta",
                    "pl_baseline_lag"]},
        {"label": "Unwrapping",
         "fields": ["unwrap_nlooks", "unwrap_tiles", "unwrap_cost",
                    "unwrap_init_method"]},
        {"label": "Execution",
         "fields": ["max_workers"]},
        {"label": "HPC (SLURM)",
         "fields": ["hpc_mode", "max_concurrent_hpc"]},
    ]
    _ui_fields: ClassVar[dict] = {
        "AOI":            {"type": "bbox", "hint": "W S E N in degrees. Drives the DEM footprint and CSLC bbox. Pre-filled from this folder's downloader intersectsWith; edit freely, or tick Process full extent to ignore it."},
        "dem_buffer_deg": {"type": "number", "step": 0.5,
                           "hint": "Padding around AOI for the DEM. Must cover every downloaded burst, not just the AOI -- geocoding reaches beyond it."},
        "dem_source":     {"type": "select", "options": ["COP", "NASA", "SRTM"],
                           "hint": "sardem source. COP = Copernicus GLO-30."},
        "water_mask":     {"type": "bool", "hint": "Also fetch the NASADEM water-body mask (blanks water before unwrapping)."},
        "tec_sol_code":   {"type": "select", "options": ["jpl", "cod", "igs", "esa", "upc"],
                           "hint": "IONEX analysis centre for the ionospheric correction."},
        "x_posting":      {"type": "number", "step": 1, "hint": "CSLC easting pixel spacing, m (COMPASS default 5)."},
        "y_posting":      {"type": "number", "step": 1, "hint": "CSLC northing pixel spacing, m (COMPASS default 10)."},
        "common_bursts_only": {"type": "bool",
                           "hint": "Keep only bursts present on EVERY date. Guarantees a rectangular stack; silently narrows coverage if a date is missing a burst."},
        "rglks":          {"type": "number", "step": 1, "hint": "Range looks on the stitched interferogram."},
        "azlks":          {"type": "number", "step": 1, "hint": "Azimuth looks on the stitched interferogram."},
        "filt_strength":  {"type": "number", "step": 0.1, "hint": "Goldstein filter alpha (0 = off)."},
        "n_connections":  {"type": "number", "step": 1, "hint": "Sequential pairing: connect each date to its next N. Ignored under user_defined. In phase_link mode this shapes the network formed AFTER estimation, which no longer decides what data is combined."},
        "process_full_extent": {"type": "bool", "hint": "Process the entire downloaded burst extent, ignoring AOI. Leave off to use the AOI above, which is pre-filled from the downloader's intersectsWith for this folder. Only applies once bursts are geocoded -- the dem and cslc stages run before that and always use AOI."},
        "ifg_mode":       {"type": "select", "options": ["network", "phase_link", "user_defined"],
                           "hint": "network = pairwise interferograms on a rule-based graph (n_connections / max_temporal_baseline). user_defined = form exactly the pairs in this folder's stack_*.json, as chosen by select_pairs or the network editor. phase_link = estimate the phase history from the full covariance (every pair contributes), then form interferograms from the result."},
        "pl_ifg_network": {"type": "select", "options": ["single_reference", "bandwidth"],
                           "hint": "Network formed AFTER phase linking. single_reference (N-1 ifgs) is what OPERA ships -- the estimator already used every pair, so extra pairs add no wrapped-phase information. 'bandwidth' uses n_connections instead, costing more unwrapping in exchange for redundancy that can catch unwrapping errors."},
        "pl_half_window_y": {"type": "number", "step": 1,
                           "hint": "Half-height of the phase-linking neighbourhood; the window is 2*y+1 rows. This is the real multilook, and it is adaptive."},
        "pl_half_window_x": {"type": "number", "step": 1,
                           "hint": "Half-width of the phase-linking neighbourhood; the window is 2*x+1 columns."},
        "pl_ministack_size": {"type": "number", "step": 1,
                           "hint": "Dates estimated jointly per block; compressed SLCs carry information across blocks. A stack shorter than this is a single ministack."},
        "pl_shp_method":  {"type": "select", "options": ["glrt", "ks", "rect"],
                           "hint": "How statistically homogeneous pixels are chosen inside the window. glrt/ks are adaptive; rect averages the whole rectangle blindly. glrt needs the amplitude statistics, computed automatically."},
        "pl_shp_alpha":   {"type": "number", "step": 0.001,
                           "hint": "Significance level for the SHP test. Smaller = stricter = fewer pixels accepted as homogeneous."},
        "pl_use_evd":     {"type": "bool", "hint": "Eigenvalue decomposition instead of the default MLE/EMI estimator."},
        "pl_beta":        {"type": "number", "step": 0.01,
                           "hint": "Regularization when inverting the coherence matrix: (1-beta)*Gamma + beta*I. Raise if the solution is unstable at low coherence."},
        "pl_baseline_lag": {"type": "number", "step": 1,
                           "hint": "StBAS: use only the nearest-N off-diagonals of the covariance. Empty = use every pair. This is the phase-linking analogue of n_connections."},
        "max_temporal_baseline": {"type": "number", "step": 6,
                           "hint": "Pair by days instead of neighbour count. Overrides n_connections when set."},
        "crop_buffer_deg": {"type": "number", "step": 0.01,
                           "hint": "Padding around AOI when cropping CSLC bursts, so filtering and unwrapping do not start at a nodata edge."},
        "coh_window":     {"type": "number", "step": 2,
                           "hint": "Window (px) for coherence estimation. Estimated AFTER filtering, so it measures filtered-phase quality, not raw correlation."},
        "unwrap_nlooks":  {"type": "number", "step": 1,
                           "hint": "Effective looks reported to snaphu; sets how much it trusts the coherence."},
        "unwrap_tiles":   {"type": "number", "step": 1,
                           "hint": "snaphu tiling per side (2 = 2x2). Raise for large scenes, at the cost of tile-seam risk."},
        "unwrap_cost":    {"type": "select", "options": ["smooth", "defo", "topo", "p-norm"],
                           "hint": "snaphu cost mode. 'smooth' suits deformation stacks."},
        "unwrap_init_method": {"type": "select", "options": ["mcf", "mst"],
                           "hint": "snaphu initialisation. MCF is slower but usually unwraps better."},
        "burst_db_path":  {"type": "text", "hint": "OPERA burst-ID sqlite3 DB for s1_geocode_stack.py. Downloaded on demand when empty."},
    }


# ---------------------------------------------------------------------------
# Analyzer configurations
# ---------------------------------------------------------------------------

@dataclass
class Mintpy_SBAS_Base_Config:
    '''
    Dataclass containing all configuration options for Mintpy SBAS jobs.

    UI metadata is stored in ``_ui_groups`` / ``_ui_fields`` and consumed
    by the API layer to auto-generate the settings panel.
    '''

    # ── UI metadata consumed by the API / settings panel ─────────────────────
    _ui_groups: ClassVar[list] = [
        {"label": "Compute Resources",
         "fields": ["compute_maxMemory", "compute_cluster", "compute_numWorker", "compute_config"]},
        {"label": "Load Data",
         "fields": ["load_processor", "load_autoPath", "load_updateMode", "load_compression",
                    "load_metaFile", "load_baselineDir",
                    "load_unwFile", "load_corFile", "load_connCompFile", "load_intFile", "load_magFile",
                    "load_ionUnwFile", "load_ionCorFile", "load_ionConnCompFile",
                    "load_azOffFile", "load_rgOffFile", "load_azOffStdFile", "load_rgOffStdFile", "load_offSnrFile",
                    "load_demFile", "load_lookupYFile", "load_lookupXFile",
                    "load_incAngleFile", "load_azAngleFile", "load_shadowMaskFile", "load_waterMaskFile", "load_bperpFile",
                    "subset_yx", "subset_lalo",
                    "multilook_method", "multilook_ystep", "multilook_xstep"]},
        {"label": "Modify Network",
         "fields": ["network_tempBaseMax", "network_perpBaseMax", "network_connNumMax",
                    "network_startDate", "network_endDate", "network_excludeDate", "network_excludeDate12",
                    "network_excludeIfgIndex", "network_referenceFile",
                    "network_coherenceBased", "network_minCoherence",
                    "network_areaRatioBased", "network_minAreaRatio",
                    "network_keepMinSpanTree", "network_maskFile", "network_aoiYX", "network_aoiLALO"]},
        {"label": "Reference Point",
         "fields": ["reference_yx", "reference_lalo", "reference_maskFile",
                    "reference_coherenceFile", "reference_minCoherence"]},
        {"label": "Unwrap Error Correction",
         "fields": ["unwrapError_method", "unwrapError_waterMaskFile", "unwrapError_connCompMinArea",
                    "unwrapError_numSample", "unwrapError_ramp", "unwrapError_bridgePtsRadius"]},
        {"label": "Network Inversion",
         "fields": ["networkInversion_weightFunc", "networkInversion_waterMaskFile",
                    "networkInversion_minNormVelocity", "networkInversion_maskDataset",
                    "networkInversion_maskThreshold", "networkInversion_minRedundancy",
                    "networkInversion_minTempCoh", "networkInversion_minNumPixel", "networkInversion_shadowMask"]},
        {"label": "Solid Earth Tides",
         "fields": ["solidEarthTides"]},
        {"label": "Ionosphere Correction",
         "fields": ["ionosphericDelay_method", "ionosphericDelay_excludeDate", "ionosphericDelay_excludeDate12"]},
        {"label": "Troposphere Correction",
         "fields": ["troposphericDelay_method", "troposphericDelay_weatherModel", "troposphericDelay_weatherDir",
                    "troposphericDelay_polyOrder", "troposphericDelay_looks", "troposphericDelay_minCorrelation",
                    "troposphericDelay_gacosDir"]},
        {"label": "Deramp",
         "fields": ["deramp", "deramp_maskFile"]},
        {"label": "Topography Correction",
         "fields": ["topographicResidual", "topographicResidual_polyOrder", "topographicResidual_phaseVelocity",
                    "topographicResidual_stepDate", "topographicResidual_excludeDate",
                    "topographicResidual_pixelwiseGeometry"]},
        {"label": "Residual RMS",
         "fields": ["residualRMS_maskFile", "residualRMS_deramp", "residualRMS_cutoff"]},
        {"label": "Reference Date",
         "fields": ["reference_date"]},
        {"label": "Velocity",
         "fields": ["timeFunc_startDate", "timeFunc_endDate", "timeFunc_excludeDate",
                    "timeFunc_polynomial", "timeFunc_periodic", "timeFunc_stepDate",
                    "timeFunc_exp", "timeFunc_log",
                    "timeFunc_uncertaintyQuantification", "timeFunc_timeSeriesCovFile",
                    "timeFunc_bootstrapCount"]},
        {"label": "Geocode",
         "fields": ["geocode", "geocode_SNWE", "geocode_laloStep", "geocode_interpMethod", "geocode_fillValue"]},
        {"label": "Google earth",
         "fields": ["save_kmz"]},
        {"label": "Hdfeos5",
         "fields": ["save_hdfEos5", "save_hdfEos5_update", "save_hdfEos5_subset"]},
        {"label": "Plot",
         "fields": ["plot", "plot_dpi", "plot_maxMemory"]},
        {"label": "HPC (SLURM)",
         "fields": ["hpc_mode"]},
        {"label": "Container",
         "fields": ["container"]},
    ]
    _ui_fields: ClassVar[dict] = {
        # Compute Resources
        "compute_maxMemory":   {"type": "number", "min": 1, "max": 512, "step": 1,
                                "default": _env['memory'],
                                "hint": "Maximum memory size in GB for each dask worker"},
        "compute_cluster":     {"type": "select",
                                "options": ["local", "slurm", "pbs", "lsf", "oar", "sge", "none"],
                                "hint": "Cluster type for parallel processing (local = dask LocalCluster)"},
        "compute_numWorker":   {"type": "number", "min": 1, "max": 64, "step": 1,
                                "default": _env['cpu'],
                                "hint": "Number of workers for parallel processing"},
        "compute_config":      {"type": "text",
                                "hint": "Configuration file for dask distributed cluster"},
        # Load Data
        "load_processor":      {"type": "select",
                                "options": ["auto", "isce", "aria", "hyp3", "gmtsar", "snap", "gamma", "roipac"],
                                "hint": "SAR processor of the input dataset"},
        "load_autoPath":       {"type": "text",
                                "hint": "Auto-detect input file paths based on processor type (auto)"},
        "load_updateMode":     {"type": "select", "options": ["auto", "yes", "no"],
                                "hint": "Skip re-loading if file already exists with same dataset and metadata"},
        "load_compression":    {"type": "select", "options": ["auto", "lzf", "gzip", "no"],
                                "hint": "Data compression for HDF5 files"},
        "load_metaFile":       {"type": "text",
                                "hint": "Metadata file path (ISCE only), e.g. reference/IW1.xml"},
        "load_baselineDir":    {"type": "text",
                                "hint": "Baseline directory (ISCE only), e.g. baselines"},
        "load_unwFile":        {"type": "text",
                                "hint": "Unwrapped interferogram file(s), e.g. ./../pairs/*/filt*.unw"},
        "load_corFile":        {"type": "text",
                                "hint": "Coherence file(s), e.g. ./../pairs/*/filt*.cor"},
        "load_connCompFile":   {"type": "text",
                                "hint": "Connected components file(s), e.g. ./../pairs/*/filt*.unw.conncomp"},
        "load_intFile":        {"type": "text",
                                "hint": "Wrapped interferogram file(s), e.g. ./../pairs/*/filt*.int"},
        "load_magFile":        {"type": "text",
                                "hint": "Interferogram magnitude file(s), e.g. ./../pairs/*/filt*.int"},
        "load_ionUnwFile":     {"type": "text", "hint": "Unwrapped ionospheric phase file(s)"},
        "load_ionCorFile":     {"type": "text", "hint": "Ionospheric coherence file(s)"},
        "load_ionConnCompFile":{"type": "text", "hint": "Ionospheric connected component file(s)"},
        "load_azOffFile":      {"type": "text", "hint": "Azimuth offset file(s)"},
        "load_rgOffFile":      {"type": "text", "hint": "Range offset file(s)"},
        "load_azOffStdFile":   {"type": "text", "hint": "Azimuth offset standard deviation file(s)"},
        "load_rgOffStdFile":   {"type": "text", "hint": "Range offset standard deviation file(s)"},
        "load_offSnrFile":     {"type": "text", "hint": "Offset SNR file(s)"},
        "load_demFile":        {"type": "text",
                                "hint": "DEM file in radar/geo coordinates, e.g. ./inputs/geometryRadar.h5"},
        "load_lookupYFile":    {"type": "text",
                                "hint": "Lookup table lat/y file, e.g. ./inputs/geometryGeo.h5"},
        "load_lookupXFile":    {"type": "text", "hint": "Lookup table lon/x file"},
        "load_incAngleFile":   {"type": "text", "hint": "Incidence angle file"},
        "load_azAngleFile":    {"type": "text", "hint": "Azimuth angle file"},
        "load_shadowMaskFile": {"type": "text", "hint": "Shadow/layover mask file"},
        "load_waterMaskFile":  {"type": "text", "hint": "Water mask file"},
        "load_bperpFile":      {"type": "text", "hint": "Perpendicular baseline file"},
        "subset_yx":           {"type": "text", "hint": "Subset in row/column, e.g. 1200:2000,0:2000"},
        "subset_lalo":         {"type": "text", "hint": "Subset in lat/lon, e.g. 37.5:38.5,-118.5:-117.5"},
        "multilook_method":    {"type": "select", "options": ["auto", "mean", "nearest", "no"],
                                "hint": "Multilook method: mean, nearest, or no for skip"},
        "multilook_ystep":     {"type": "auto_number", "hint": "Multilook factor in y/azimuth direction"},
        "multilook_xstep":     {"type": "auto_number", "hint": "Multilook factor in x/range direction"},
        # Modify Network
        "network_tempBaseMax":     {"type": "auto_number", "hint": "Maximum temporal baseline in days"},
        "network_perpBaseMax":     {"type": "auto_number", "hint": "Maximum perpendicular baseline in meters"},
        "network_connNumMax":      {"type": "auto_number", "hint": "Maximum number of nearest-neighbor connections"},
        "network_startDate":       {"type": "text", "hint": "Start date in YYYYMMDD format"},
        "network_endDate":         {"type": "text", "hint": "End date in YYYYMMDD format"},
        "network_excludeDate":     {"type": "text", "hint": "Date(s) to exclude in YYYYMMDD, separated by space"},
        "network_excludeDate12":   {"type": "text",
                                    "hint": "Interferogram date pairs to exclude, e.g. 20150115_20150127"},
        "network_excludeIfgIndex": {"type": "text",
                                    "hint": "Index(es) of interferograms to exclude, e.g. 2 8 230"},
        "network_referenceFile":   {"type": "text",
                                    "hint": "Reference network file (pairs in date12_list.txt format)"},
        "network_coherenceBased":  {"type": "select", "options": ["auto", "yes", "no"],
                                    "hint": "Enable coherence-based network modification"},
        "network_minCoherence":    {"type": "number", "min": 0, "max": 1, "step": 0.05,
                                    "hint": "Minimum coherence threshold for coherence-based modification"},
        "network_areaRatioBased":  {"type": "select", "options": ["auto", "yes", "no"],
                                    "hint": "Enable area-ratio-based network modification (ECR method)"},
        "network_minAreaRatio":    {"type": "auto_number",
                                    "hint": "Minimum area ratio for area-ratio-based modification"},
        "network_keepMinSpanTree": {"type": "select", "options": ["auto", "yes", "no"],
                                    "hint": "Keep the minimum spanning tree of the network"},
        "network_maskFile":        {"type": "text",
                                    "hint": "Mask file for coherence-based network modification"},
        "network_aoiYX":           {"type": "text",
                                    "hint": "AOI in row/column for coherence calculation, e.g. 100:200,300:400"},
        "network_aoiLALO":         {"type": "text",
                                    "hint": "AOI in lat/lon for coherence calculation, e.g. 37.5:38.0,-118.0:-117.5"},
        # Reference Point
        "reference_yx":            {"type": "text", "hint": "Reference point in row/column, e.g. 257 151"},
        "reference_lalo":          {"type": "text", "hint": "Reference point in lat/lon, e.g. 37.65 -118.45"},
        "reference_maskFile":      {"type": "text", "hint": "Mask file for reference point selection"},
        "reference_coherenceFile": {"type": "text", "hint": "Coherence file for reference point selection"},
        "reference_minCoherence":  {"type": "auto_number",
                                    "hint": "Minimum coherence for reference point selection"},
        # Unwrap Error
        "unwrapError_method":          {"type": "select",
                                        "options": ["auto", "bridging", "phase_closure",
                                                    "bridging+phase_closure", "no"],
                                        "hint": "Phase unwrapping error correction method"},
        "unwrapError_waterMaskFile":   {"type": "text", "hint": "Water mask file for bridging method"},
        "unwrapError_connCompMinArea": {"type": "auto_number",
                                        "hint": "Minimum area in pixels for a connected component"},
        "unwrapError_numSample":       {"type": "auto_number",
                                        "hint": "Number of randomly sampled triplets for phase_closure method"},
        "unwrapError_ramp":            {"type": "select", "options": ["auto", "linear", "quadratic", "no"],
                                        "hint": "Remove ramp before bridging"},
        "unwrapError_bridgePtsRadius": {"type": "auto_number",
                                        "hint": "Radius in pixels to search for bridge points"},
        # Network Inversion
        "networkInversion_weightFunc":      {"type": "select", "options": ["auto", "var", "fim", "no"],
                                             "hint": "var = spatial variance, fim = Fisher info matrix, no = uniform"},
        "networkInversion_waterMaskFile":   {"type": "text", "hint": "Water mask file applied before inversion"},
        "networkInversion_minNormVelocity": {"type": "select", "options": ["auto", "yes", "no"],
                                             "hint": "Minimize L2-norm of velocity (vs. timeseries) in SBAS inversion"},
        "networkInversion_maskDataset":     {"type": "text",
                                             "hint": "Dataset for masking, e.g. coherence or connectComponent"},
        "networkInversion_maskThreshold":   {"type": "number", "min": 0, "max": 1, "step": 0.05,
                                             "hint": "Threshold for maskDataset to mask unwrapped phase"},
        "networkInversion_minRedundancy":   {"type": "auto_number",
                                             "hint": "Minimum redundancy of interferograms per pixel"},
        "networkInversion_minTempCoh":      {"type": "auto_number",
                                             "hint": "Minimum temporal coherence for pixel masking"},
        "networkInversion_minNumPixel":     {"type": "auto_number",
                                             "hint": "Minimum number of coherent pixels to proceed"},
        "networkInversion_shadowMask":      {"type": "select", "options": ["auto", "yes", "no"],
                                             "hint": "Use shadow mask from geometry"},
        # Solid Earth Tides
        "solidEarthTides":  {"type": "select", "options": ["auto", "yes", "no"],
                             "hint": "Correct for solid earth tides using pysolid"},
        # Ionosphere
        "ionosphericDelay_method":       {"type": "select", "options": ["auto", "split_spectrum", "no"],
                                          "hint": "Ionospheric delay correction method"},
        "ionosphericDelay_excludeDate":  {"type": "text",
                                          "hint": "Dates to exclude from ionospheric correction, e.g. 20180202 20180414"},
        "ionosphericDelay_excludeDate12":{"type": "text",
                                          "hint": "Interferogram date pairs to exclude from ionospheric correction"},
        # Troposphere
        "troposphericDelay_method":         {"type": "select",
                                             "options": ["auto", "pyaps", "gacos", "height_correlation", "no"],
                                             "hint": "Tropospheric delay correction method"},
        "troposphericDelay_weatherModel":   {"type": "select",
                                             "options": ["auto", "ERA5", "ERA5T", "MERRA", "NARR"],
                                             "hint": "Weather model for pyaps (ERA5 recommended)"},
        "troposphericDelay_weatherDir":     {"type": "text",
                                             "hint": "Directory of downloaded weather data files for pyaps"},
        "troposphericDelay_polyOrder":      {"type": "auto_number",
                                             "hint": "Polynomial order for height-correlation method"},
        "troposphericDelay_looks":          {"type": "auto_number",
                                             "hint": "Extra multilook factor for height-correlation estimation"},
        "troposphericDelay_minCorrelation": {"type": "auto_number",
                                             "hint": "Minimum correlation between height and phase"},
        "troposphericDelay_gacosDir":       {"type": "text", "hint": "Directory of GACOS delay files"},
        # Deramp
        "deramp":          {"type": "select", "options": ["auto", "linear", "quadratic", "no"],
                            "hint": "Remove phase ramp in x/y direction"},
        "deramp_maskFile": {"type": "text", "hint": "Mask file for ramp estimation"},
        # Topography
        "topographicResidual":                 {"type": "select", "options": ["auto", "yes", "no"],
                                                "hint": "Correct topographic residuals (DEM error)"},
        "topographicResidual_polyOrder":       {"type": "auto_number",
                                                "hint": "Polynomial order for DEM error estimation"},
        "topographicResidual_phaseVelocity":   {"type": "select", "options": ["auto", "yes", "no"],
                                                "hint": "Minimize phase velocity (not phase) in DEM error inversion"},
        "topographicResidual_stepDate":        {"type": "text",
                                                "hint": "Step function date(s) for co-seismic jumps, e.g. 20140911"},
        "topographicResidual_excludeDate":     {"type": "text",
                                                "hint": "Dates to exclude in DEM error inversion"},
        "topographicResidual_pixelwiseGeometry":{"type": "select", "options": ["auto", "yes", "no"],
                                                 "hint": "Use pixel-wise geometry in DEM error estimation"},
        # Residual RMS
        "residualRMS_maskFile": {"type": "text", "hint": "Mask file for residual phase quality assessment"},
        "residualRMS_deramp":   {"type": "select", "options": ["auto", "linear", "quadratic", "no"],
                                 "hint": "Remove ramp before RMS calculation"},
        "residualRMS_cutoff":   {"type": "auto_number",
                                 "hint": "Cutoff value in RMS threshold for outlier date detection"},
        # Reference Date
        "reference_date": {"type": "text",
                           "hint": "Reference date in YYYYMMDD; 'auto' = first date with full coherence"},
        # Velocity
        "timeFunc_startDate":                {"type": "text", "hint": "Start date of the time function fit"},
        "timeFunc_endDate":                  {"type": "text", "hint": "End date of the time function fit"},
        "timeFunc_excludeDate":              {"type": "text",
                                              "hint": "Date(s) to exclude from time function fitting"},
        "timeFunc_polynomial":               {"type": "auto_number",
                                              "hint": "Polynomial order: 1 = linear velocity, 2 = acceleration"},
        "timeFunc_periodic":                 {"type": "text",
                                              "hint": "Periodic periods in years, e.g. 1.0 0.5 for annual+semi-annual"},
        "timeFunc_stepDate":                 {"type": "text",
                                              "hint": "Step function date(s), e.g. 20161231 for co-seismic jump"},
        "timeFunc_exp":                      {"type": "text",
                                              "hint": "Exponential decay: onset_date char_time, e.g. 20181026 60"},
        "timeFunc_log":                      {"type": "text",
                                              "hint": "Logarithmic relaxation: onset_date char_time, e.g. 20181026 60"},
        "timeFunc_uncertaintyQuantification":{"type": "select", "options": ["auto", "bootstrap", "residue"],
                                              "hint": "Method for velocity uncertainty quantification"},
        "timeFunc_timeSeriesCovFile":        {"type": "text",
                                              "hint": "Time-series covariance file for uncertainty propagation"},
        "timeFunc_bootstrapCount":           {"type": "auto_number",
                                              "hint": "Number of bootstrap iterations"},
        # Geocode
        "geocode":              {"type": "select", "options": ["auto", "yes", "no"],
                                 "hint": "Geocode datasets in radar coordinates to geo coordinates"},
        "geocode_SNWE":         {"type": "text",
                                 "hint": "Bounding box: south north west east, e.g. 31 40 -115 -100"},
        "geocode_laloStep":     {"type": "text",
                                 "hint": "Output pixel size in lat/lon, e.g. -0.000833 0.000833 (≈90 m)"},
        "geocode_interpMethod": {"type": "select", "options": ["auto", "nearest", "linear"],
                                 "hint": "Interpolation method for geocoding"},
        "geocode_fillValue":    {"type": "text",
                                 "hint": "Fill value for pixels outside coverage, e.g. nan or 0"},
        # Google Earth
        "save_kmz":            {"type": "select", "options": ["auto", "yes", "no"],
                                "hint": "Save geocoded velocity to Google Earth KMZ file"},
        # HDF-EOS5
        "save_hdfEos5":        {"type": "select", "options": ["auto", "yes", "no"],
                                "hint": "Save time-series to HDF-EOS5 format"},
        "save_hdfEos5_update": {"type": "select", "options": ["auto", "yes", "no"],
                                "hint": "Update HDF-EOS5 file if already exists"},
        "save_hdfEos5_subset": {"type": "select", "options": ["auto", "yes", "no"],
                                "hint": "Save subset of HDF-EOS5 file"},
        # Plot
        "plot":                {"type": "select", "options": ["auto", "yes", "no"],
                                "hint": "Plot results during processing"},
        "plot_dpi":            {"type": "auto_number", "hint": "Figure DPI for saved plots"},
        "plot_maxMemory":      {"type": "auto_number",
                                "hint": "Maximum memory in GB for plot_smallbaseline.py"},
        "hpc_mode":            {"type": "bool",
                                "hint": "Submit the full MintPy run as a single sbatch job. "
                                        "SLURM resources come from sbatch_options.json (step \"17\": \"SBAS\") "
                                        "in the workdir, generated automatically on first use."},
        "container":           {"type": "text",
                                "hint": "Path to a .sif/Apptainer image or a Docker image reference with insarhub "
                                        "installed — re-runs this command inside the container instead of on the "
                                        "host. Not remembered between runs; pass again for subsequent runs."},
    }
    # ─────────────────────────────────────────────────────────────────────────

    name: str = "Mintpy_SBAS_Base_Config"
    workdir: Path | str = field(default_factory=lambda: Path.cwd())
    debug: bool = False
    hpc_mode: bool = False
    container: str | None = None

    ## computing resource configuration
    compute_maxMemory : float | int = _env['memory']
    compute_cluster : str = 'local' # Mintpy's slurm parallel processing is buggy, so we will handle parallel processing with dask instead. Switch to none to turn off parallel processing to save memory.
    compute_numWorker : int = _env['cpu']
    compute_config: str = 'none'

    ## Load data
    load_processor: str = 'auto'
    load_autoPath: str = 'auto' 
    load_updateMode: str = 'no'
    load_compression: str = 'auto'
    ##---------for ISCE only:
    load_metaFile: str = 'auto'
    load_baselineDir: str = 'auto'
    ##---------interferogram stack:
    load_unwFile: str = 'auto'
    load_corFile: str = 'auto'
    load_connCompFile: str = 'auto'
    load_intFile: str = 'auto'
    load_magFile: str = 'auto'
    ##---------ionosphere stack (optional):
    load_ionUnwFile: str = 'auto'
    load_ionCorFile: str = 'auto'
    load_ionConnCompFile: str = 'auto'
    ##---------offset stack (optional):
    load_azOffFile: str = 'auto'
    load_rgOffFile: str = 'auto'
    load_azOffStdFile: str = 'auto'
    load_rgOffStdFile: str = 'auto'
    load_offSnrFile: str = 'auto'
    ##---------geometry:
    load_demFile: str = 'auto'
    load_lookupYFile: str = 'auto'
    load_lookupXFile: str = 'auto'
    load_incAngleFile: str = 'auto'
    load_azAngleFile: str = 'auto'
    load_shadowMaskFile: str = 'auto'
    load_waterMaskFile: str = 'auto'
    load_bperpFile: str = 'auto'
    ##---------subset (optional):
    subset_yx: str = 'auto'
    subset_lalo: str = 'auto'
    ##---------multilook (optional):
    multilook_method: str = 'auto'
    multilook_ystep: str | int = 'auto'
    multilook_xstep: str | int= 'auto'

    # 2. Modify Network
    network_tempBaseMax: str | float = 'auto'
    network_perpBaseMax: str | float = 'auto'
    network_connNumMax: str | int = 'auto'
    network_startDate: str = 'auto'
    network_endDate: str = 'auto'
    network_excludeDate: str = 'auto'
    network_excludeDate12: str = 'auto'
    network_excludeIfgIndex: str = 'auto'
    network_referenceFile: str = 'auto'
    ## 2) Data-driven network modification
    network_coherenceBased: str = 'auto'
    network_minCoherence: str |float = 'auto'
    ## b - Effective Coherence Ratio network modification = (threshold + MST) by default
    network_areaRatioBased: str = 'auto'
    network_minAreaRatio: str |float= 'auto'
    ## Additional common parameters for the 2) data-driven network modification
    network_keepMinSpanTree: str = 'auto'
    network_maskFile: str = 'auto'
    network_aoiYX: str = 'auto'
    network_aoiLALO: str = 'auto'

    # 3. Reference Point
    reference_yx: str = 'auto'
    reference_lalo: str = 'auto'
    reference_maskFile: str = 'auto'
    reference_coherenceFile: str = 'auto'
    reference_minCoherence: str |float = 'auto'

    # 4. Correct Unwrap Error
    unwrapError_method: str = 'auto'
    unwrapError_waterMaskFile: str = 'auto'
    unwrapError_connCompMinArea: str |float = 'auto'
    ## phase_closure options:
    unwrapError_numSample: str | int= 'auto'
    ## bridging options:
    unwrapError_ramp: str = 'auto'
    unwrapError_bridgePtsRadius: str | int= 'auto'

    # 5. Invert Network
    networkInversion_weightFunc: str = 'auto'
    networkInversion_waterMaskFile: str = 'auto'
    networkInversion_minNormVelocity: str = 'auto'
    ## mask options for unwrapPhase of each interferogram before inversion (recommend if weightFunct=no):
    networkInversion_maskDataset: str = 'auto'
    networkInversion_maskThreshold: str | float = 'auto'
    networkInversion_minRedundancy: str | float = 'auto'
    ## Temporal coherence is calculated and used to generate the mask as the reliability measure
    networkInversion_minTempCoh: str | float = 'auto'
    networkInversion_minNumPixel: str | int = 'auto'
    networkInversion_shadowMask: str = 'auto'

    # 6. Correct SET (Solid Earth Tides)
    solidEarthTides: str = 'auto'

    # 7. Correct Ionosphere
    ionosphericDelay_method: str = 'auto'
    ionosphericDelay_excludeDate: str = 'auto'
    ionosphericDelay_excludeDate12: str = 'auto'

    # 8. Correct Troposphere
    troposphericDelay_method: str = 'auto'
    ## Notes for pyaps:
    troposphericDelay_weatherModel: str = 'auto'
    troposphericDelay_weatherDir: str = 'auto'
    
    ## Notes for height_correlation:
    troposphericDelay_polyOrder: str | int = 'auto'
    troposphericDelay_looks: str | int = 'auto'
    troposphericDelay_minCorrelation: str | float = 'auto'
    ## Notes for gacos:
    troposphericDelay_gacosDir: str = 'auto'

    # 9. Deramp
    deramp: str = 'auto'
    deramp_maskFile: str = 'auto'

    # 10. Correct Topography
    topographicResidual: str = 'auto'
    topographicResidual_polyOrder: str = 'auto'
    topographicResidual_phaseVelocity: str = 'auto'
    topographicResidual_stepDate: str = 'auto'
    topographicResidual_excludeDate: str = 'auto'
    topographicResidual_pixelwiseGeometry: str = 'auto'

    # 11.1 Residual RMS
    residualRMS_maskFile: str = 'auto'
    residualRMS_deramp: str = 'auto'
    residualRMS_cutoff: str | float = 'auto'

    # 11.2 Reference Date
    reference_date: str = 'auto'

    # 12. Velocity
    timeFunc_startDate: str = 'auto'
    timeFunc_endDate: str = 'auto'
    timeFunc_excludeDate: str = 'auto'
    ## Fit a suite of time functions
    timeFunc_polynomial: str | int = 'auto'
    timeFunc_periodic: str = 'auto'
    timeFunc_stepDate: str = 'auto'
    timeFunc_exp: str = 'auto'
    timeFunc_log: str = 'auto'
    ## Uncertainty quantification methods:
    timeFunc_uncertaintyQuantification: str = 'auto'
    timeFunc_timeSeriesCovFile: str = 'auto'
    timeFunc_bootstrapCount: str | int = 'auto'

    # 13.1 Geocode
    geocode: str = 'auto'
    geocode_SNWE: str = 'auto'
    geocode_laloStep: str = 'auto'
    geocode_interpMethod: str = 'auto'
    geocode_fillValue: str | float = 'auto'

    # 13.2 Google Earth
    save_kmz: str = 'auto'

    # 13.3 HDFEOS5
    save_hdfEos5: str = 'auto'
    save_hdfEos5_update: str = 'auto'
    save_hdfEos5_subset: str = 'auto'

    # 13.4 Plot
    plot: str = 'auto'
    plot_dpi: str | int = 'auto'
    plot_maxMemory: str | int = 'auto'

    def __post_init__(self):
        if isinstance(self.workdir, str):
            self.workdir = Path(self.workdir).expanduser().resolve()
    
    def write_mintpy_config(self, outpath: Union[Path, str]):
        """
        Writes the dataclass to a mintpy .cfg file, excluding operational 
        parameters that MintPy doesn't recognize.
        """
        outpath = Path(outpath).expanduser().resolve()
        outpath.parent.mkdir(parents=True, exist_ok=True)
        exclude_fields = ['name', 'workdir', 'debug']
        # InSARHub stores these space-separated (e.g. "37.84 -112.82",
        # matching --reference_lalo CLI input), but MintPy's own template
        # reader does value.split(',') -- it requires "lat,lon"/"y,x".
        comma_join_fields = ['reference_yx', 'reference_lalo']

        with open(outpath, 'w') as f:
            f.write("## MintPy Config File Generated via InSARHub\n")

            for key, value in asdict(self).items():
                if key in exclude_fields:
                    continue

                if key in comma_join_fields and isinstance(value, str) and ',' not in value:
                    parts_val = value.split()
                    if len(parts_val) == 2:
                        value = ",".join(parts_val)

                parts = key.split('_')
                if len(parts) > 1:
                    mintpy_key = f"mintpy.{parts[0]}.{'.'.join(parts[1:])}"
                else:
                    mintpy_key = f"mintpy.{parts[0]}"

                f.write(f"{mintpy_key:<40} = {value}\n")

        return Path(outpath).resolve()


@dataclass
class Hyp3_SBAS_Config(Mintpy_SBAS_Base_Config):
    name: str = "Hyp3_SBAS_Config"
    load_processor: str = "hyp3"
    deramp: str = 'linear'
    troposphericDelay_method: str = 'pyaps'
    networkInversion_maskDataset: str = 'coherence'
    networkInversion_maskThreshold: str | float = 0.5
    network_coherenceBased : str = 'yes'
    network_minCoherence : str| float = 0.7
    plot : str = 'yes'
    save_kmz: str = 'no'

@dataclass
class ISCE_SBAS_Config(Mintpy_SBAS_Base_Config):
    """MintPy SBAS config pre-wired for ISCE2 stackSentinel outputs.

    File paths are set automatically by ``ISCE_SBAS.prep_data()`` from
    ``workdir/isce/``.  You can override any field if your layout differs.
    MintPy output is always written to ``workdir/mintpy/``.
    """
    name: str                         = "ISCE_SBAS_Config"
    load_processor: str               = "isce"
    # These are populated by ISCE_SBAS.prep_data() — left empty here so
    # write_mintpy_config() skips them when they are still unset.
    load_metaFile: str                = "auto"
    load_baselineDir: str             = "auto"
    load_unwFile: str                 = "auto"
    load_corFile: str                 = "auto"
    load_connCompFile: str            = "auto"
    load_demFile: str                 = "auto"
    load_lookupYFile: str             = "auto"
    load_lookupXFile: str             = "auto"
    load_incAngleFile: str            = "auto"
    load_azAngleFile: str             = "auto"
    load_waterMaskFile: str           = "auto"
    deramp: str                       = "linear"
    troposphericDelay_method: str     = "pyaps"
    networkInversion_maskDataset: str = "coherence"
    networkInversion_maskThreshold: str | float = 0.5
    network_coherenceBased: str       = "yes"
    network_minCoherence: str | float = 0.7
    plot: str                         = "yes"
    save_kmz: str                     = "no"


@dataclass
class GMTSAR_SBAS_Config:
    """Config for GMTSAR-native SBAS time-series inversion (GMTSAR_SBAS).

    Consumes a stack produced by GMTSAR_S1 with stack_mode=True (under
    workdir/gmtsar/: intf.in, baseline_table.dat, intf/<pair>/). Runs
    GMTSAR's own prep_sbas + sbas C binary -- no MintPy involved. Output
    (disp_*.grd, vel.grd) lands in workdir/gmtsar_sbas/.
    """

    _ui_groups: ClassVar[list] = [
        {"label": "Inversion",
         "fields": ["smooth", "wavelength", "incidence", "range_dist",
                    "atm_iters", "rms", "dem_err"]},
        {"label": "Stack input",
         "fields": ["phase_grd", "corr_grd"]},
        {"label": "Network pruning",
         "fields": ["auto_prune", "max_nan_fraction"]},
        {"label": "GMTSAR",
         "fields": ["gmtsar_root", "gmtsar_env_bin"]},
    ]
    _ui_fields: ClassVar[dict] = {
        "smooth":      {"type": "number", "step": 0.1, "default": 0, "hint": "sbas -smooth smoothing factor"},
        "wavelength":  {"type": "number", "step": 0.001, "default": 0.0554658, "hint": "Radar wavelength (m). Default S1 C-band."},
        "incidence":   {"type": "number", "step": 1, "default": 37, "hint": "sbas -incidence incidence angle (deg)"},
        "range_dist":  {"type": "number", "step": 1000, "default": 866000, "hint": "sbas -range radar-to-scene-center range (m)"},
        "atm_iters":   {"type": "number", "step": 1, "default": 0, "hint": "sbas -atm atmospheric-correction iterations (0 = skip)"},
        "rms":         {"type": "bool", "hint": "sbas -rms: output velocity uncertainty grids"},
        "dem_err":     {"type": "bool", "hint": "sbas -dem: output DEM-error grid"},
        "auto_prune":  {"type": "bool", "hint": "Drop decorrelated pairs (and any date left orphaned) before inversion. GMTSAR's sbas needs every pixel valid in ALL interferograms, so one badly decorrelated pair can null out the whole velocity map -- GMTSAR itself does no such filtering."},
        "max_nan_fraction": {"type": "number", "step": 0.05, "min": 0, "max": 1, "default": 0.5,
                             "hint": "auto_prune threshold: drop a pair whose unwrapped phase is more than this fraction NaN"},
        "phase_grd":   {"type": "text", "hint": "Per-pair unwrapped phase grid name inside intf/<pair>/ (radar coords)"},
        "corr_grd":    {"type": "text", "hint": "Per-pair coherence grid name inside intf/<pair>/"},
        "gmtsar_root":    {"type": "text", "hint": "GMTSAR repo root ($GMTSAR) -- its bin/ is prepended to subprocess PATH"},
        "gmtsar_env_bin": {"type": "text", "hint": "bin/ of the conda env providing `gmt` + the sbas binary"},
    }

    name: str                          = "GMTSAR_SBAS_Config"
    workdir: Path | str                = field(default_factory=lambda: Path.cwd())
    # Defaults follow GMTSAR's own Sentinel-1 time-series recipe (§12b):
    #   sbas intf.tab scene.tab N S xdim ydim -range … -incidence 40 \
    #        -wavelength 0.0554658 -smooth 5.0 -rms -dem
    smooth: float                      = 5.0
    wavelength: float                  = 0.0554658   # Sentinel-1 C-band
    # FALLBACK ONLY, like range_dist above: GMTSAR_SBAS._resolve_geometry()
    # derives the real mid-swath incidence per run from the super-master PRM
    # (earth_radius, SC_height) and the grid extent. 40 rather than sbas's own
    # default of 37 because 37 is a generic value, not a Sentinel-1 one -- a
    # real 3-subswath frame computes to 39.53 deg mid-swath, which 37 misses
    # by ~6% on the DEM-error term (the only thing -incidence feeds) while the
    # recipe's 40 is within ~1%.
    incidence: float                   = 40
    # FALLBACK ONLY. -range is the radar-to-scene-centre distance and is
    # frame-specific; GMTSAR_SBAS._resolve_range() computes it per run from
    # the super-master PRM (rng_samp_rate, near_range) and the grid extent,
    # exactly as the recipe's formula does, and only falls back to this value
    # (with a warning) if the PRM can't be read. It is not a sane universal
    # constant: the recipe's own scene works out to 901,085 m.
    range_dist: float                  = 866000
    atm_iters: int                     = 0
    rms: bool                          = True        # recipe passes -rms
    dem_err: bool                      = True        # recipe passes -dem
    auto_prune: bool                   = True
    max_nan_fraction: float            = 0.5
    phase_grd: str                     = "unwrap.grd"
    corr_grd: str                      = "corr.grd"
    gmtsar_root: Path | str | None     = None
    gmtsar_env_bin: Path | str | None  = None


@dataclass
class GMTSAR_MINTPY_SBAS_Config(Mintpy_SBAS_Base_Config):
    """MintPy SBAS config pre-wired for GMTSAR stack_mode output.

    File paths are set automatically by GMTSAR_MINTPY_SBAS.prep_data() from
    workdir/gmtsar/ (the stack GMTSAR_S1 stack_mode produces), then handed to
    MintPy's prep_gmtsar.py + smallbaselineApp. MintPy output → workdir/mintpy/.
    """
    name: str                         = "GMTSAR_MINTPY_SBAS_Config"
    load_processor: str               = "gmtsar"
    # Populated by GMTSAR_MINTPY_SBAS.prep_data() — left "auto" so
    # write_mintpy_config() skips them while unset.
    load_metaFile: str                = "auto"
    load_baselineDir: str             = "auto"
    load_unwFile: str                 = "auto"
    load_corFile: str                 = "auto"
    load_demFile: str                 = "auto"
    load_lookupYFile: str             = "auto"
    load_lookupXFile: str             = "auto"
    load_incAngleFile: str            = "auto"
    deramp: str                       = "linear"
    troposphericDelay_method: str     = "pyaps"
    networkInversion_maskDataset: str = "coherence"
    networkInversion_maskThreshold: str | float = 0.5
    network_coherenceBased: str       = "yes"
    network_minCoherence: str | float = 0.7
    plot: str                         = "yes"
    save_kmz: str                     = "no"
    # GMTSAR runtime for the prep_gmtsar shell-out
    gmtsar_root: Path | str | None    = None
    gmtsar_env_bin: Path | str | None = None


@dataclass
class Dolphin_SBAS_Config:
    """Config for the dolphin time-series inversion (Dolphin_SBAS).

    Consumes an ISCE3_Burst stack: unwrapped interferograms plus their
    connected-component labels. Deliberately has **no** "SBAS vs phase linking"
    switch -- that choice was made by the processor's ``ifg_mode``, and the
    inversion is identical either way. The analyzer reads the mode from the
    stack's ``ifg_manifest.json`` so the two can never disagree; a config field
    here would just be a second, silently-divergent source of truth.

    What the mode actually changes is the quality raster used to pick the
    reference point: phase linking leaves stitched temporal coherence on disk,
    while a pairwise network has only per-pair correlations, so those are
    reduced to a temporal average.

    Attributes:
        workdir: ISCE3_Burst processing root.
        unwrap_dir/quality_file: Override auto-discovery from the manifest.
        method: L1 is robust to unwrapping errors and is the default; L2 is
            plain least squares and is faster but trusts every pair equally.
        wavelength: Radar wavelength in metres; converts radians to displacement.
            Sentinel-1 C-band = 0.055465763.
        run_velocity: Also fit a linear velocity over the stack.
        correlation_threshold: Drop pixels below this quality before inverting.
        reference_point: (row, col) to hold fixed. Auto-selected from the
            quality raster when unset.
        los_projection: 'none' keeps line-of-sight. 'vertical' divides by the
            LOS up-component -- which assumes the horizontal motion is zero,
            so it is a modelling choice, not a coordinate change. Needs the
            processor's 'static' stage.
        num_threads/block_shape: Inversion parallelism.
    """

    _ui_groups: ClassVar[list] = [
        {"label": "Inversion",
         "fields": ["method", "wavelength", "run_velocity",
                    "correlation_threshold", "reference_point"]},
        {"label": "Geometry",
         "fields": ["los_projection"]},
        {"label": "Stack input",
         "fields": ["workdir", "unwrap_dir", "quality_file", "output_dir"]},
        {"label": "Execution",
         "fields": ["num_threads"]},
        {"label": "HPC (SLURM)",
         "fields": ["hpc_mode"]},
    ]
    _ui_fields: ClassVar[dict] = {
        "method": {"type": "select", "options": ["L1", "L2"],
                   "hint": "L1 is robust to unwrapping errors (recommended). L2 is plain least squares."},
        "wavelength": {"type": "number", "step": 0.001,
                       "hint": "Radar wavelength in m. Sentinel-1 C-band = 0.055465763."},
        "run_velocity": {"type": "bool", "hint": "Also fit a linear velocity map over the stack."},
        "correlation_threshold": {"type": "number", "step": 0.05,
                       "hint": "Mask pixels whose quality is below this before inverting."},
        "reference_point": {"type": "text",
                       "hint": "'row,col' to hold fixed. Auto-selected from the quality raster when empty."},
        "los_projection": {"type": "select", "options": ["none", "vertical"],
                       "hint": "'vertical' divides LOS by the up-component of the LOS unit vector, which ASSUMES zero horizontal motion. Requires the processor's 'static' stage."},
        "unwrap_dir": {"type": "text", "hint": "Override; normally discovered from the stack."},
        "quality_file": {"type": "text", "hint": "Override; normally temporal coherence (phase_link) or a temporal average of correlation (network)."},
        "output_dir": {"type": "text", "hint": "Where the time series is written. Defaults to workdir/timeseries; set it per ifg_mode to keep two runs apart."},
        "num_threads": {"type": "number", "step": 1, "hint": "Threads for the block-wise inversion."},
        "hpc_mode": {"type": "bool",
                     "hint": "Submit the Dolphin_SBAS run as a single sbatch job. "
                             "SLURM resources come from sbatch_options.json (step \"sbas\") "
                             "in the workdir, generated automatically on first use. "
                             "num_threads is auto-derived from cpus_per_task."},
    }

    name: str                         = "Dolphin_SBAS_Config"
    workdir: str | None               = None
    debug: bool                       = False
    unwrap_dir: str | None            = None
    quality_file: str | None          = None
    output_dir: str | None            = None

    method: str                       = "L1"
    wavelength: float                 = 0.055465763
    run_velocity: bool                = True
    correlation_threshold: float      = 0.0
    reference_point: str | None       = None

    los_projection: str               = "none"

    num_threads: int                  = 4
    block_shape: tuple                = (256, 256)
    hpc_mode: bool                    = False
    container: str | None             = None
