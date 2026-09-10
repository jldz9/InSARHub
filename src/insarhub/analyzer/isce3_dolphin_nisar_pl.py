"""Dolphin time-series (phase linking) over a NISAR GSLC stack.

Consumes what the ``ISCE3_NISAR`` processor writes and inverts it with dolphin's
``timeseries.run`` -- the same inversion and the same products as the Sentinel-1
analyzer, so everything comes from
:class:`~insarhub.analyzer.dolphin_base.Dolphin_PL_Base_Analyzer`. What changes
is the config bound to it and how the wavelength is obtained: NISAR is L-band
and its two frequency groups differ, so the value is read from the GSLC metadata
instead of being pinned to a constant.
"""

from __future__ import annotations

from pathlib import Path

from insarhub.config import ISCE3_Dolphin_NISAR_PL_Config

from .dolphin_base import Dolphin_PL_Base_Analyzer


class ISCE3_Dolphin_NISAR_PL(Dolphin_PL_Base_Analyzer):

    """Dolphin time-series over a NISAR GSLC stack (ISCE3_NISAR).

    Identical inversion to the Sentinel-1 analyzer -- same dolphin
    ``timeseries.run``, same products -- so everything comes from the shared
    Dolphin_PL_Base_Analyzer. What changes is the config bound to it and how
    the wavelength is obtained:
    NISAR is L-band and its two frequency groups differ, so the value is read
    from the GSLC metadata instead of being pinned to a constant.
    """
    name = "ISCE3_Dolphin_NISAR_PL"
    #: Only this class's OWN former name. Registry.register() maps every alias to
    #: the registering class, so listing the Sentinel-1 analyzer's legacy names
    #: ("Dolphin_SBAS", "Dolphin_TS", "ISCE3_Dolphin_TS", "ISCE3_Dolphin_PL")
    #: here would silently re-point them at this NISAR class -- an old S1 config
    #: invoked by a legacy name would then be built with the NISAR config and
    #: fail on a missing wavelength. Those belong to ISCE3_Dolphin_S1_PL.
    #: Inheriting from the sensor-neutral base rather than from the S1 analyzer
    #: is what keeps that separation structural instead of a comment.
    aliases = ("ISCE3_Dolphin_PL_NISAR",)
    description = ("Time-series inversion of an ISCE3_NISAR (NISAR GSLC) stack "
                   "with dolphin: cumulative displacement per date, velocity, residuals.")
    compatible_processor = "ISCE3_NISAR"
    default_config = ISCE3_Dolphin_NISAR_PL_Config

    #: Where a NISAR GSLC keeps its centre frequency, in preference order.
    #: ``{f}`` is the frequency group letter from the config.
    _CENTER_FREQ_PATHS = (
        "/science/LSAR/GSLC/grids/frequency{f}/centerFrequency",
        "/science/LSAR/GSLC/grids/frequency{f}/processingInformation/centerFrequency",
        "/science/LSAR/identification/centerFrequency",
    )

    _C = 299792458.0        # m/s

    def _gslc_files(self) -> list[Path]:
        """The stack's GSLC granules. Mirrors ISCE3_NISAR's own gslc_dir default
        (workdir/slc) so the analyzer looks where the processor wrote."""
        d = getattr(self.config, "gslc_dir", None)
        d = Path(d).expanduser() if d else self.workdir / "slc"
        return sorted(d.glob("*GSLC*.h5"))

    def _wavelength(self) -> float:
        """Config value if set, else c / centreFrequency from the GSLC metadata.

        Taking the Sentinel-1 C-band constant here would scale every
        displacement by roughly 4.3x -- L-band is ~0.24 m against C-band's
        0.055 m -- and nothing downstream would flag it, so this reads the
        actual product rather than swapping in a second hardcoded number.
        Frequency A and B have different centre frequencies, which is the other
        reason not to pin a constant.
        """
        w = getattr(self.config, "wavelength", None)
        if w:
            return float(w)

        import h5py
        import numpy as np

        freq = str(getattr(self.config, "nisar_frequency", "A")).upper()
        files = self._gslc_files()
        if not files:
            raise ValueError(
                f"{self.name}: no *GSLC*.h5 found to read the wavelength from. "
                f"Set `wavelength` (metres) explicitly, or point `gslc_dir` at "
                f"the granules the ISCE3_NISAR processor used.")

        tried = [q.format(f=freq) for q in self._CENTER_FREQ_PATHS]
        with h5py.File(files[0], "r") as h5:
            for path in tried:
                if path not in h5:
                    continue
                hz = float(np.ravel(h5[path][()])[0])
                if hz <= 0:
                    continue
                lam = self._C / hz
                print(f"[{self.name}] wavelength   : {lam:.6f} m "
                      f"(frequency {freq}, {hz / 1e6:.1f} MHz, from {files[0].name})")
                return lam

        raise ValueError(
            f"{self.name}: no centre frequency in {files[0].name}; looked at "
            f"{', '.join(tried)}. Set `wavelength` (metres) explicitly.")

