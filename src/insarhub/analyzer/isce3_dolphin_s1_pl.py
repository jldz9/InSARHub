"""Dolphin time-series (phase linking) over a Sentinel-1 burst stack.

Consumes what the ``ISCE3_Burst`` processor writes (``unwrapped/`` +
``interferograms/``) and inverts it with dolphin's ``timeseries.run``. The
inversion itself lives in
:class:`~insarhub.analyzer.dolphin_base.Dolphin_PL_Base_Analyzer`; this module
only binds the Sentinel-1 identity and config to it.
"""

from __future__ import annotations

from insarhub.config import ISCE3_Dolphin_S1_PL_Config

from .dolphin_base import Dolphin_PL_Base_Analyzer


class ISCE3_Dolphin_S1_PL(Dolphin_PL_Base_Analyzer):
    """Dolphin time-series over an ISCE3_Burst (Sentinel-1) stack.

    Everything is inherited from the base: the C-band wavelength is a plain
    config default (``ISCE3_Dolphin_S1_PL_Config.wavelength``), so the base's
    :meth:`~insarhub.analyzer.dolphin_base.Dolphin_PL_Base_Analyzer._wavelength`
    finds it without an override. See
    :class:`~insarhub.analyzer.isce3_dolphin_NISAR_PL.ISCE3_Dolphin_NISAR_PL`
    for the case that does need one.
    """

    name = "ISCE3_Dolphin_S1_PL"
    #: Legacy names, kept resolvable so saved insarhub_config.json files and CLI
    #: commands from before the per-sensor split keep working. ``ISCE3_Dolphin_PL``
    #: belongs here rather than on the NISAR child: until the split it WAS the
    #: Sentinel-1 analyzer (C-band wavelength, water mask, LOS projection), so an
    #: old config invoked by that name must keep landing on this class.
    aliases = ("Dolphin_SBAS", "Dolphin_TS", "ISCE3_Dolphin_TS",
               "ISCE3_Dolphin_PL")
    description = ("Time-series inversion of an ISCE3_Burst (Sentinel-1) stack "
                   "with dolphin: cumulative displacement per date, velocity, residuals.")
    # One analyzer per upstream, each carrying its own config -- the same shape
    # as the Mintpy family (Hyp3_Mintpy_SBAS / ISCE2_Mintpy_SBAS / ...). This
    # used to be a single analyzer with a ("ISCE3_Burst", "ISCE3_NISAR") tuple
    # and one shared config, but default_config is a per-class binding and
    # nothing dispatches on the actual upstream, so a NISAR stack silently got
    # the Sentinel-1 C-band wavelength.
    compatible_processor = "ISCE3_Burst"
    default_config = ISCE3_Dolphin_S1_PL_Config
