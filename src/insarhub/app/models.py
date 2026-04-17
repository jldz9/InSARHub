# -*- coding: utf-8 -*-
"""Pydantic request / response models for the InSARHub API."""

from typing import Any
from pydantic import BaseModel, Field


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
    flightDirection: str | None       = Field(default=None)
    pathStart:       int | None       = Field(default=None)
    pathEnd:         int | None       = Field(default=None)
    frameStart:      int | None       = Field(default=None)
    frameEnd:        int | None       = Field(default=None)
    granule_names:   list[str] | None = Field(default=None)


class DownloadRequest(BaseModel):
    session_id: str = Field(..., description="session_id returned by /api/search")
    workdir:    str = Field(..., example="/data/bryce")


class DownloadSceneRequest(BaseModel):
    url:      str        = Field(..., description="Direct ASF download URL")
    filename: str | None = Field(default=None)
    workdir:  str        = Field(default=".", example="/data/bryce")


class DownloadStackRequest(BaseModel):
    urls:    list[str] = Field(..., description="List of ASF download URLs")
    workdir: str       = Field(default=".", example="/data/bryce")


class AddJobRequest(BaseModel):
    workdir:         str
    relativeOrbit:   int
    frame:           int
    start:           str
    end:             str
    wkt:             str | None = None
    flightDirection: str | None = None
    platform:        str | None = None
    downloaderType:  str = "S1_SLC"


class DownloadByNameRequest(BaseModel):
    scene_names:   list[str] = Field(default=[])
    scene_file:    str | None = Field(default=None)
    workdir:       str = Field(default=".")
    downloaderType: str = Field(default="S1_SLC")


class JobResponse(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    status:   str       # "running" | "done" | "error"
    progress: int       # 0-100
    message:  str
    data:     Any = None


class SettingsUpdate(BaseModel):
    workdir:              str | None             = None
    max_download_workers: int | None             = None
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
    folder_path:   str
    dt_targets:    list[int] = Field(default=[6, 12, 24, 36, 48, 72, 96])
    dt_tol:        int   = 3
    dt_max:        int   = 120
    pb_max:        float = 150.0
    min_degree:    int   = 3
    max_degree:    int   = 5
    force_connect: bool  = True
    max_workers:   int   = 4


class SavePairsRequest(BaseModel):
    folder_path: str
    pairs:       dict[str, list[list[str]]]   # {"p100_f466": [["scene_a", "scene_b"], ...]}


class ProcessRequest(BaseModel):
    folder_path:      str
    processor_type:   str = "Hyp3_InSAR"
    processor_config: dict[str, Any] = {}
    dry_run:          bool = False


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
    processor_type: str = "Hyp3_InSAR"


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
