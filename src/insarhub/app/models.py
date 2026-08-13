# -*- coding: utf-8 -*-
"""Pydantic request / response models for the InSARHub API."""

from typing import Any
from pydantic import BaseModel, Field
from insarhub.utils.defaults import SELECT_PAIRS_DEFAULTS as _SP


class SearchRequest(BaseModel):
    west:  float = Field(..., example=-113.05)
    south: float = Field(..., example=37.74)
    east:  float = Field(..., example=-112.68)
    north: float = Field(..., example=38.00)
    wkt:   str | None = Field(default=None, description="WKT polygon — overrides bbox when provided")
    start: str | None = Field(default=None, example="2021-01-01")
    end:   str | None = Field(default=None, example="2022-01-01")
    workdir:    str       = Field(default=".")
    maxResults: int | None = Field(default=2000)
    beamMode:        str | None       = Field(default=None)
    polarization:    list[str] | None = Field(default=None)
    granule_names:   list[str] | None = Field(default=None)
    downloaderType:  str = Field(default="S1_SLC")
    overrides: dict[str, Any] | None = Field(
        default=None,
        description="Extra downloader-specific config field overrides, keyed by field "
                     "name from that downloader's search_filter_schema (see "
                     "GET /api/downloader-schema) — e.g. "
                     "{'flightDirection': 'ASCENDING', 'relativeOrbit': [100, 101]}.",
    )


class DownloadSceneRequest(BaseModel):
    url:      str        = Field(..., description="Direct ASF download URL")
    filename: str | None = Field(default=None)
    workdir:  str        = Field(default=".", example="/data/bryce")


class AddJobRequest(BaseModel):
    workdir:         str
    relativeOrbit:   int
    frame:           int | None = None
    start:           str
    end:             str
    wkt:             str | None = None
    flightDirection: str | None = None
    platform:        str | None = None
    downloaderType:  str = "S1_SLC"
    # Burst-only (S1_Burst): identify a single burst position. frame is
    # meaningless for bursts (ASF returns no frameNumber), so the folder is
    # named p<path>_iw<s>_b<id> from subswath + the OPERA relative burst ID.
    subswath: str | None = None
    burst_id: int | None = None


class MergedStackSpec(BaseModel):
    relativeOrbit:   int
    frame:           int | None = None
    start:           str
    end:             str
    wkt:             str | None = None
    flightDirection: str | None = None
    platform:        str | None = None
    # Burst-only: identify a single burst position when the merged selection
    # contains exactly one burst (see AddJobRequest).
    subswath: str | None = None
    burst_id: int | None = None


class DownloadMergedRequest(BaseModel):
    workdir:        str
    stacks:         list[MergedStackSpec]
    download_slc:   bool = True
    download_orbit: bool = True
    downloaderType: str = "S1_SLC"


class AddMergedJobRequest(BaseModel):
    workdir:        str
    stacks:         list[MergedStackSpec]
    downloaderType: str = "S1_SLC"


class JobResponse(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    status:   str       # "running" | "done" | "error"
    progress: int       # 0-100
    message:  str
    data:     Any = None


class SettingsUpdate(BaseModel):
    workdir:              str | None             = None
    downloader:           str | None             = None
    downloader_config:    dict[str, Any] | None  = None
    processor:            str | None             = None
    processor_config:     dict[str, Any] | None  = None
    analyzer:             str | None             = None
    analyzer_config:      dict[str, Any] | None  = None


class CredentialsBody(BaseModel):
    username: str | None = None
    password: str | None = None
    token:    str | None = None


class FolderDownloadRequest(BaseModel):
    folder_path: str


class SelectPairsRequest(BaseModel):
    folder_path:              str
    dt_targets:               list[int] = Field(default_factory=lambda: list(_SP["dt_targets"]))
    dt_tol:                   int   = _SP["dt_tol"]
    dt_max:                   int   = _SP["dt_max"]
    pb_max:                   float = _SP["pb_max"]
    min_degree:               int   = _SP["min_degree"]
    max_degree:               int   = _SP["max_degree"]
    force_connect:            bool  = _SP["force_connect"]
    max_workers:              int   = _SP["max_workers"]
    avoid_low_quality_days:   bool  = _SP["avoid_low_quality_days"]
    snow_threshold:           float = _SP["snow_threshold"]
    precip_mm_threshold:      float = _SP["precip_mm_threshold"]
    # No manual "merge" flag — auto-detected server-side from whether the
    # folder's saved config has `frame` as a list (see _run_folder_select_pairs).


class SavePairsRequest(BaseModel):
    folder_path: str
    pairs:       dict[str, list[list[str]]]   # {"p100_f466": [["scene_a", "scene_b"], ...]}


class ProcessRequest(BaseModel):
    folder_path:      str
    processor_type:   str = "Hyp3_S1"
    processor_config: dict[str, Any] = {}
    dry_run:          bool = False
    steps:            list[str] | None = None  # force-(re)run specific step(s); local processors only


class InitAnalyzerRequest(BaseModel):
    folder_path:   str
    analyzer_type: str


class RunAnalyzerRequest(BaseModel):
    folder_path:   str
    analyzer_type: str
    steps:         list[str]


class Hyp3ActionRequest(BaseModel):
    folder_path:    str
    job_file:       str
    action:         str   # "refresh" | "retry" | "download"
    processor_type: str = "Hyp3_S1"


class LocalActionRequest(BaseModel):
    folder_path:    str
    job_file:       str
    action:         str   # "refresh" | "retry" | "cancel" | "force_steps"
    processor_type: str = "ISCE2_S1"
    steps:          list[str] | None = None  # required for action == "force_steps"


class FolderConfigPatch(BaseModel):
    analyzer_config: dict[str, Any]


class ParseAoiRequest(BaseModel):
    filename: str
    data:     str   # base64-encoded file bytes


class PairQualityResponse(BaseModel):
    scores:       dict[str, float]  # "ref_scene:sec_scene" -> 0.0–1.0
    factors:      dict[str, Any]    # "ref_scene:sec_scene" -> factor breakdown
    ndvi_source:  str               # "modis" | "climatology" | "mixed"
    snow_fetched: int               # how many dates were fetched from Open-Meteo
    cached:       bool              # True when all data came from disk cache
