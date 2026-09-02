"""NISAR L2 GUNW (geocoded unwrapped interferograms) search + download via ASF.

GUNW is a ready-made geocoded, unwrapped interferogram PAIR product -- the NISAR
analog of a HyP3 Sentinel-1 GUNW. It goes straight into MintPy through its
``prep_nisar`` loader; no processor is needed. GUNW is single-band (the main band
only), so there is no side-band-polarization facet. Search/grouping/download come
from ASF_Base_Downloader (no orbit download). Filters mirror the ASF website's
NISAR GUNW facets.
"""
from insarhub.config import NISAR_GUNW_Config
from .asf_base import ASF_Base_Downloader


class NISAR_GUNW(ASF_Base_Downloader):
    name = "NISAR_GUNW"
    description = "NISAR L2 GUNW (geocoded unwrapped interferograms) via ASF -> MintPy."
    default_config = NISAR_GUNW_Config
    product_label = "GUNWs"

    search_filter_schema = [
        {"name": "flightDirection", "label": "Flight Direction", "kind": "select",
         "group": "Additional Filters", "choices": ["ASCENDING", "DESCENDING"]},
        {"name": "mainBandPolarization", "label": "Polarization", "kind": "select",
         "group": "Additional Filters", "choices": ["HH", "HV", "VH", "VV"]},
        # GUNW is single-band (the interferogram is formed on the main band), so
        # its range bandwidth is a single value, not the dual "40+5" of GSLC/RSLC.
        {"name": "rangeBandwidth", "label": "Range Bandwidth (MHz)",
         "kind": "select", "group": "Additional Filters",
         "choices": ["80", "40", "20", "5"]},
        {"name": "frameCoverage", "label": "Frame Coverage", "kind": "select",
         "group": "Additional Filters", "choices": ["FULL", "PARTIAL"]},
        {"name": "relativeOrbit", "label": "Path", "kind": "range",
         "group": "Path and Frame Filters"},
        {"name": "frame", "label": "Frame", "kind": "range",
         "group": "Path and Frame Filters"},
    ]
