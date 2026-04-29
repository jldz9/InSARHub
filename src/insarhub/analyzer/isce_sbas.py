# -*- coding: utf-8 -*-
"""
ISCE_SBAS — MintPy SBAS analyzer for ISCE2 topsApp outputs.

prep_data() auto-discovers the ISCE2 pair directories produced by
ISCE_S1 and wires the MintPy load paths so the user does not need to
set them manually.

Expected workdir layout (produced by ISCE_S1.download()):

    workdir/
      S1AB_20200101_20200113_VV_INT80_ISCE/
        merged/
          filt_topophase.unw.geo
          filt_topophase.unw.geo.xml
          filt_topophase.cor.geo
          filt_topophase.unw.conncomp.geo
          los.rdr.geo
        reference/
          IW1.xml  IW2.xml  IW3.xml
        baselines/
          20200113/
            IW1.xml ...
        topsApp.status   (SUCCEEDED)
      S1AB_20200113_20200125_VV_INT80_ISCE/
        ...
"""

from __future__ import annotations

from pathlib import Path

from colorama import Fore

from insarhub.config.defaultconfig import ISCE_SBAS_Config
from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer


_STATUS_FILE = "topsApp.status"
_SUCCEEDED   = "SUCCEEDED"


class ISCE_SBAS(Mintpy_SBAS_Base_Analyzer):
    """SBAS time-series analysis of ISCE2 topsApp outputs using MintPy.

    Usage::

        from insarhub import Analyzer

        az = Analyzer.create('ISCE_SBAS', workdir='/data/bryce')
        az.prep_data()   # auto-wires MintPy paths, writes .mintpy.cfg
        az.run()
    """

    name                 = "ISCE_SBAS"
    description          = "SBAS time-series analysis of ISCE2 topsApp outputs using MintPy."
    compatible_processor = "ISCE_S1"
    default_config       = ISCE_SBAS_Config

    def __init__(self, config: ISCE_SBAS_Config | None = None):
        super().__init__(config)

    # ── Public entry point ────────────────────────────────────────────────────

    def prep_data(self) -> None:
        """Auto-discover ISCE2 outputs and write the MintPy config file.

        Scans ``workdir`` for pair directories that contain a ``SUCCEEDED``
        status file, then sets all ``load_*`` parameters on the config before
        calling the base ``prep_data()`` which writes ``.mintpy.cfg``.

        Raises:
            FileNotFoundError: If no succeeded ISCE2 pair directories are found.
        """
        pair_dirs = self._find_succeeded_pair_dirs()
        if not pair_dirs:
            raise FileNotFoundError(
                f"No succeeded ISCE2 pair directories found in {self.workdir}. "
                "Run ISCE_S1.submit() and wait for jobs to complete."
            )

        print(f"{Fore.CYAN}Found {len(pair_dirs)} succeeded pair(s). "
              f"Configuring MintPy load paths…{Fore.RESET}")

        self._set_load_parameters(pair_dirs)
        super().prep_data()   # writes .mintpy.cfg

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _find_succeeded_pair_dirs(self) -> list[Path]:
        """Return all pair directories under workdir that finished successfully."""
        dirs = []
        for d in sorted(self.workdir.iterdir()):
            if not d.is_dir():
                continue
            status_file = d / _STATUS_FILE
            if status_file.exists() and status_file.read_text().strip() == _SUCCEEDED:
                dirs.append(d)
        return dirs

    # ── MintPy path wiring ────────────────────────────────────────────────────

    def _set_load_parameters(self, pair_dirs: list[Path]) -> None:
        """Set config.load_* fields from the discovered ISCE2 directory layout."""
        workdir = self.workdir

        # ── Unwrapped / coherence / connected-components glob patterns ────────
        # MintPy accepts shell globs.  Use a path relative to workdir so the
        # config is portable.
        self.config.load_unwFile     = str(workdir / "S1AB_*" / "merged" / "filt_topophase.unw.geo")
        self.config.load_corFile     = str(workdir / "S1AB_*" / "merged" / "filt_topophase.cor.geo")
        self.config.load_connCompFile = str(workdir / "S1AB_*" / "merged" / "filt_topophase.unw.conncomp.geo")

        # ── Geometry / LOS files ──────────────────────────────────────────────
        # topsApp writes per-pair geometry under merged/; use the first succeeded
        # pair as the reference geometry source (they are all co-registered).
        ref_dir = pair_dirs[0]
        merged  = ref_dir / "merged"

        los_file = merged / "los.rdr.geo"
        if los_file.exists():
            # MintPy reads incidence angle from los.rdr.geo band 1,
            # azimuth angle from band 2.
            self.config.load_incAngleFile = str(los_file)
            self.config.load_azAngleFile  = str(los_file)

        water_mask = merged / "waterMask.geo"
        if water_mask.exists():
            self.config.load_waterMaskFile = str(water_mask)
        else:
            self.config.load_waterMaskFile = "no"

        # Lookup table (geocoded data already in geo coordinates, use lat/lon)
        lat_file = merged / "lat.rdr.geo"
        lon_file = merged / "lon.rdr.geo"
        if lat_file.exists():
            self.config.load_lookupYFile = str(lat_file)
        if lon_file.exists():
            self.config.load_lookupXFile = str(lon_file)

        # DEM
        dem_file = merged / "dem.crop"
        if not dem_file.exists():
            # topsApp may write it as .dem or as geometryRadar.h5 after geocoding
            for candidate in (merged / "geometryRadar.h5",
                               merged / "geometryGeo.h5",
                               merged.parent / "DEM" / "srtm1.dem.wgs84"):
                if candidate.exists():
                    dem_file = candidate
                    break
        if dem_file.exists():
            self.config.load_demFile = str(dem_file)
        else:
            self.config.load_demFile = "auto"

        # ── Reference metadata (IW*.xml) ─────────────────────────────────────
        # MintPy needs one IW*.xml from the reference scene to get orbit/sensor info.
        ref_xml_dir = ref_dir / "reference"
        iw_xmls = sorted(ref_xml_dir.glob("IW*.xml")) if ref_xml_dir.exists() else []
        if iw_xmls:
            self.config.load_metaFile = str(iw_xmls[0])
        else:
            self.config.load_metaFile = "auto"

        # ── Baseline directory ────────────────────────────────────────────────
        # topsApp writes per-date baseline files into <pair_dir>/baselines/.
        # MintPy expects a single baselines/ directory with per-date subdirs.
        # Consolidate them under workdir/baselines/.
        consolidated_baselines = self._consolidate_baselines(pair_dirs)
        self.config.load_baselineDir = str(consolidated_baselines)

        print(f"{Fore.GREEN}  unwFile      : {self.config.load_unwFile}")
        print(f"  corFile      : {self.config.load_corFile}")
        print(f"  metaFile     : {self.config.load_metaFile}")
        print(f"  baselineDir  : {self.config.load_baselineDir}{Fore.RESET}")

    def _consolidate_baselines(self, pair_dirs: list[Path]) -> Path:
        """Merge per-pair baseline subdirs into a single workdir/baselines/ tree.

        ISCE2 writes baseline files as::

            <pair_dir>/baselines/<secondary_date>/IW*.xml

        MintPy expects them all under a single directory::

            <workdir>/baselines/<secondary_date>/IW*.xml

        This method symlinks (or copies as fallback) each secondary-date
        subdir into the consolidated location.  Already-present entries are
        left untouched.
        """
        import shutil

        baselines_dir = self.workdir / "baselines"
        baselines_dir.mkdir(exist_ok=True)

        for pair_dir in pair_dirs:
            src_bl = pair_dir / "baselines"
            if not src_bl.is_dir():
                continue
            for date_dir in sorted(src_bl.iterdir()):
                if not date_dir.is_dir():
                    continue
                dest = baselines_dir / date_dir.name
                if dest.exists():
                    continue
                try:
                    dest.symlink_to(date_dir.resolve())
                except (OSError, NotImplementedError):
                    shutil.copytree(date_dir, dest)

        return baselines_dir
