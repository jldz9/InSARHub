"""NISAR L1 RSLC (radar-coordinate SLC) search + download via ASF.

RSLC is the rawest InSAR input -- the L1 radar SLC (both frequency A and B).
GMTSAR's NISAR path (pre_proc_nsr / p2p_processing_nsr, SAT=NSR_A) turns it into
interferograms. Search/grouping/download come from ASF_Base_Downloader; no orbit
download (NISAR carries its own state vectors). Filters mirror the ASF website's
NISAR facets (flight direction, main/side-band polarization, range bandwidth,
frame coverage, path/frame).
"""
from insarhub.config import NISAR_RSLC_Config
from .asf_base import ASF_Base_Downloader


class NISAR_RSLC(ASF_Base_Downloader):
    name = "NISAR_RSLC"
    description = "NISAR L1 RSLC (radar SLC) search and download via ASF."
    default_config = NISAR_RSLC_Config
    product_label = "RSLCs"

    search_filter_schema = [
        {"name": "flightDirection", "label": "Flight Direction", "kind": "select",
         "group": "Additional Filters", "choices": ["ASCENDING", "DESCENDING"]},
        {"name": "mainBandPolarization", "label": "Polarization (main band)",
         "kind": "select", "group": "Additional Filters",
         "choices": ["HH+HV", "HH+VV", "HH+HV+VH+VV", "VV", "VV+VH", "RH+RV", "LH+LV"]},
        {"name": "sideBandPolarization", "label": "Polarization (side band)",
         "kind": "select", "group": "Additional Filters",
         "choices": ["HH+HV", "HH+VV", "HH+HV+VH+VV", "VV", "VV+VH", "RH+RV", "LH+LV"]},
        {"name": "rangeBandwidth", "label": "Range Bandwidth (MHz)",
         "kind": "select", "group": "Additional Filters",
         "choices": ["80+5", "40+5", "20+5", "5"]},
        {"name": "frameCoverage", "label": "Frame Coverage", "kind": "select",
         "group": "Additional Filters", "choices": ["FULL", "PARTIAL"]},
        {"name": "relativeOrbit", "label": "Path", "kind": "range",
         "group": "Path and Frame Filters"},
        {"name": "frame", "label": "Frame", "kind": "range",
         "group": "Path and Frame Filters"},
    ]
