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
    ]
    _ui_fields: ClassVar[dict] = {
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
    name:str = "S1_Burst_Config"
    dataset: str | list[str] | None =  constants.DATASET.SENTINEL1
    instrument: str | None = constants.INSTRUMENT.C_SAR
    beamMode:str | None = constants.BEAMMODE.IW
    polarization: str|list[str] | None = field(default_factory=lambda: [constants.POLARIZATION.VV, constants.POLARIZATION.VV_VH])
    processingLevel: str | None = constants.PRODUCT_TYPE.BURST


# ---------------------------------------------------------------------------
# Processor configurations
# ---------------------------------------------------------------------------

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
         "fields": ["skip_existing", "submission_chunk_size"]},
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
    phase_filter_parameter :float=0.6


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
    }
    # ─────────────────────────────────────────────────────────────────────────

    name: str = "Mintpy_SBAS_Base_Config"
    workdir: Path | str = field(default_factory=lambda: Path.cwd())
    debug: bool = False 

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
        exclude_fields = ['name', 'workdir', 'debug']

        with open(outpath, 'w') as f:
            f.write("## MintPy Config File Generated via InSARHub\n")

            for key, value in asdict(self).items():
                if key in exclude_fields:
                    continue

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
class ISCE_S1_Config:
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
            is pre-downloaded using bbox.
        isce_home: ISCE2 installation root.  Falls back to $ISCE_HOME env var.
        bbox: Area of interest as [S, N, W, E] degrees.  Required when dem_path is None.
        num_overlap_connections: Connections used for NESD azimuth coregistration.
        reference_date: Stack reference date YYYYMMDD.  None = stackSentinel auto-selects.
        coregistration: 'NESD' (default, more accurate) or 'geometry' (faster).
        max_workers: Parallel commands within each run step.
    """

    _ui_groups: ClassVar[list] = [
        {"label": "Paths",
         "fields": ["workdir", "slc_dir", "orbit_dir", "aux_dir", "dem_path", "isce_home"]},
        {"label": "Network",
         "fields": ["num_overlap_connections", "reference_date",
                    "start_date", "end_date", "exclude_dates", "include_dates"]},
        {"label": "Area of interest",
         "fields": ["bbox", "swath_num"]},
        {"label": "Coregistration",
         "fields": ["coregistration", "esd_coherence_threshold", "snr_misreg_threshold"]},
        {"label": "Interferogram",
         "fields": ["workflow", "looks_range", "looks_azimuth", "filter_strength",
                    "rm_filter", "polarization", "unw_method", "virtual_merge"]},
        {"label": "Ionosphere",
         "fields": ["param_ion", "num_connections_ion"]},
        {"label": "Compute",
         "fields": ["use_gpu", "num_proc", "num_proc4topo", "text_cmd"]},
        {"label": "Job",
         "fields": ["max_workers", "skip_existing"]},
    ]
    _ui_fields: ClassVar[dict] = {
        "workdir":                  {"type": "text", "hint": "Processing root directory"},
        "slc_dir":                  {"type": "text", "hint": "Directory with Sentinel-1 SLC .SAFE files"},
        "orbit_dir":                {"type": "text", "hint": "Directory with .EOF orbit files"},
        "aux_dir":                  {"type": "text", "hint": "Directory with AUX_CAL files (leave blank to auto-download)"},
        "dem_path":                 {"type": "text", "hint": "ISCE2-format DEM path (leave blank to download GLO-30 using bbox)"},
        "isce_home":                {"type": "text", "hint": "ISCE2 installation root (or set $ISCE_HOME)"},
        "bbox":                     {"type": "text", "hint": "Bounding box S N W E, e.g. 33.0 38.0 -120.0 -115.0"},
        "swath_num":                {"type": "text", "hint": "Subswaths to process, e.g. '1 2 3' or '1 2'"},
        "num_overlap_connections":  {"type": "number", "min": 1, "max": 10, "step": 1, "default": 3,
                                     "hint": "Connections for NESD azimuth coregistration"},
        "reference_date":           {"type": "text", "hint": "Reference date YYYYMMDD (leave blank for auto)"},
        "start_date":               {"type": "text", "hint": "Start date YYYY-MM-DD (leave blank for all)"},
        "end_date":                 {"type": "text", "hint": "End date YYYY-MM-DD (leave blank for all)"},
        "exclude_dates":            {"type": "text", "hint": "Comma-separated dates to exclude, e.g. 20200102,20200126"},
        "include_dates":            {"type": "text", "hint": "Comma-separated dates to include (all others excluded)"},
        "coregistration":           {"type": "select", "options": ["NESD", "geometry"],
                                     "hint": "NESD = geometry + ESD refinement (recommended); geometry = orbit-only (faster)"},
        "esd_coherence_threshold":  {"type": "number", "min": 0, "max": 1, "step": 0.05, "default": 0.85,
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
        "use_gpu":                  {"type": "bool",
                                     "hint": "Use CUDA GPU acceleration (requires ISCE2 GPU build)"},
        "num_proc":                 {"type": "number", "min": 1, "max": 32, "step": 1, "default": 1,
                                     "hint": "Parallel processes per ISCE step (most steps benefit from 1–4)"},
        "num_proc4topo":            {"type": "number", "min": 1, "max": 32, "step": 1, "default": 1,
                                     "hint": "Parallel processes for the topo step specifically"},
        "text_cmd":                 {"type": "text",
                                     "hint": "Command prefix for each run-script line, e.g. 'mpirun -np 4'"},
        "max_workers":              {"type": "number", "min": 1, "max": 16, "step": 1, "default": 4,
                                     "hint": "Parallel commands within each run step"},
        "skip_existing":            {"type": "bool",
                                     "hint": "Skip steps that already completed successfully"},
    }

    name: str                             = "ISCE_S1_Config"
    workdir: Path | str                   = field(default_factory=lambda: Path.cwd())
    slc_dir: Path | str                   = field(default_factory=lambda: Path.cwd())
    orbit_dir: Path | str | None          = None
    aux_dir: Path | str | None            = None
    dem_path: Path | str | None           = None
    isce_home: Path | str | None          = None
    saved_job_path: Path | str | None     = None
    # Area / dates
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
    esd_coherence_threshold: float        = 0.85
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
    num_proc: int                         = 1
    num_proc4topo: int                    = 1
    text_cmd: str                         = ""
    # Job control
    max_workers: int                      = 4
    skip_existing: bool                   = True
    submission_chunk_size: int            = 1

    def __post_init__(self):
        for attr in ("workdir", "slc_dir", "orbit_dir", "aux_dir", "dem_path", "isce_home",
                     "saved_job_path"):
            val = getattr(self, attr)
            if isinstance(val, str):
                setattr(self, attr, Path(val).expanduser().resolve())


@dataclass
class ISCE_SBAS_Config(Mintpy_SBAS_Base_Config):
    """MintPy SBAS config pre-wired for ISCE2 topsApp outputs.

    File paths are set automatically by ``ISCE_SBAS.prep_data()`` based on
    the pair directories found in ``workdir``.  You can override any field
    manually if your directory layout differs.
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

