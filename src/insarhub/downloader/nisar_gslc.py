"""NISAR L2 GSLC (geocoded SLC) search + download via ASF.

GSLC is already geocoded, so it feeds dolphin phase-linking directly through the
ISCE3_NISAR processor -- there is no coregistration/geocoding step and no orbit
download (NISAR products carry their own state vectors). Everything else (search,
grouping by path/frame, download, pair selection, footprint) comes from
``ASF_Base_Downloader``.
"""
from insarhub.config import NISAR_GSLC_Config
from .asf_base import ASF_Base_Downloader


class NISAR_GSLC(ASF_Base_Downloader):
    name = "NISAR_GSLC"
    description = "NISAR L2 GSLC (geocoded SLC) search and download via ASF."
    default_config = NISAR_GSLC_Config
    product_label = "GSLCs"

    search_filter_schema = [
        {"name": "flightDirection", "label": "Flight Direction", "kind": "select",
         "group": "Additional Filters", "choices": ["ASCENDING", "DESCENDING"]},
        # NISAR carries polarization per FREQUENCY BAND, not in a single
        # `polarization` field (which ASF leaves null). The main band
        # (frequencyA, the wide high-resolution band used for InSAR) is what to
        # filter on; the 5 MHz side band (frequencyB) is for ionosphere.
        {"name": "mainBandPolarization", "label": "Polarization (main band)",
         "kind": "select", "group": "Additional Filters",
         "choices": ["HH+HV", "HH+VV", "HH+HV+VH+VV", "VV", "VV+VH", "RH+RV", "LH+LV"]},
        # Side band = frequencyB (the 5 MHz band, for ionosphere / split-
        # spectrum). Usually you filter on the main band, BUT some NISAR
        # products are side-band-only (rangeBandwidth '5', mainBand=None), so
        # this is here to reach those / to pick the frequencyB pol explicitly.
        {"name": "sideBandPolarization", "label": "Polarization (side band)",
         "kind": "select", "group": "Additional Filters",
         "choices": ["HH+HV", "HH+VV", "HH+HV+VH+VV", "VV", "VV+VH", "RH+RV", "LH+LV"]},
        # Range bandwidth = range resolution / acquisition mode. Keep it CONSTANT
        # across a stack (e.g. all 40+5) so every date has the same resolution.
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
