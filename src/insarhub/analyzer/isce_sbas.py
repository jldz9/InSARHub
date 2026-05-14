# -*- coding: utf-8 -*-
"""
ISCE_SBAS — MintPy SBAS analyzer for ISCE2 stackSentinel outputs.

prep_data() auto-discovers outputs from isce/ and wires MintPy load paths.
run() writes all MintPy results to workdir/mintpy/.

Expected layout (produced by ISCE_S1 / stackSentinel):

    p{path}_f{frame}/
      isce/
        merged/
          interferograms/
            {ref}_{sec}/
              filt_fine.unw.geo
              filt_fine.cor.geo
              filt_fine.unw.conncomp.geo
          geometry/
            lat.rdr.geo   lon.rdr.geo   hgt.rdr.geo
            incLocal.rdr.geo   azimuthAngle.rdr.geo
            waterMask.geo  (optional)
        baselines/
          {secondary_date}/  ...
        reference/
          IW*.xml  (or {ref_date}/ subdir)
      mintpy/   ← MintPy output always here
"""

from __future__ import annotations

from pathlib import Path

from colorama import Fore
from mintpy.smallbaselineApp import TimeSeriesAnalysis

from insarhub.config.defaultconfig import ISCE_SBAS_Config
from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer


class ISCE_SBAS(Mintpy_SBAS_Base_Analyzer):
    """SBAS time-series analysis of ISCE2 stackSentinel outputs using MintPy.

    Usage::

        from insarhub import Analyzer

        az = Analyzer.create('ISCE_SBAS', workdir='/data/p64_f468')
        az.prep_data()   # auto-wires MintPy paths, writes mintpy/.mintpy.cfg
        az.run()         # writes all output to workdir/mintpy/
    """

    name                 = "ISCE_SBAS"
    description          = "SBAS time-series analysis of ISCE2 stackSentinel outputs using MintPy."
    compatible_processor = "ISCE_S1"
    default_config       = ISCE_SBAS_Config

    def __init__(self, config: ISCE_SBAS_Config | None = None):
        super().__init__(config)
        self.isce_dir   = self.workdir / "isce"
        self.mintpy_dir = self.workdir / "mintpy"
        # Write config inside mintpy/ so MintPy finds it next to its outputs
        self.cfg_path   = self.mintpy_dir / ".mintpy.cfg"

    # ── Public entry points ───────────────────────────────────────────────────

    def prep_data(self) -> None:
        """Auto-discover stackSentinel outputs and write the MintPy config."""
        if not self.isce_dir.exists():
            raise FileNotFoundError(
                f"ISCE processing directory not found: {self.isce_dir}. "
                "Run ISCE_S1 and wait for all steps to complete."
            )
        ifg_dir = self.isce_dir / "merged" / "interferograms"
        pairs = sorted(d for d in ifg_dir.iterdir() if d.is_dir()) if ifg_dir.exists() else []
        if not pairs:
            raise FileNotFoundError(
                f"No interferogram directories in {ifg_dir}. "
                "ISCE_S1 processing must reach the interferogram stage first."
            )
        print(f"{Fore.CYAN}Found {len(pairs)} interferogram pair(s). "
              f"Configuring MintPy load paths…{Fore.RESET}")
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
        self._set_load_parameters()
        super().prep_data()   # writes self.cfg_path

    def run(self, steps=None):
        """Run MintPy, writing all output to workdir/mintpy/."""
        self.mintpy_dir.mkdir(parents=True, exist_ok=True)
        if self.config.troposphericDelay_method == "pyaps" and (steps is None or "correct_troposphere" in steps):
            self._cds_authorize()
        run_steps = steps or [
            "load_data", "modify_network", "reference_point", "quick_overview",
            "invert_network", "correct_LOD", "correct_SET", "correct_ionosphere",
            "correct_troposphere", "deramp", "correct_topography", "residual_RMS",
            "reference_date", "velocity", "geocode", "google_earth", "hdfeos5",
        ]
        from colorama import Style
        print(f"{Style.BRIGHT}{Fore.MAGENTA}Running MintPy Analysis…{Fore.RESET}")
        app = TimeSeriesAnalysis(self.cfg_path.as_posix(), str(self.mintpy_dir))
        app.open()
        app.run(steps=run_steps)
        if 'geocode' in run_steps:
            self._geocode_diagnostic_files(self.mintpy_dir)

    # ── Path discovery ────────────────────────────────────────────────────────

    def _set_load_parameters(self) -> None:
        """Wire all MintPy load_* fields from the stackSentinel output layout.

        Handles both geocoded (.geo suffix) and radar-coordinate (no suffix)
        variants, and both merged/geometry/ and merged/geom_reference/ layouts.
        """
        isce   = self.isce_dir
        merged = isce / "merged"

        # ── interferogram files: prefer geocoded, fall back to radar-coordinate ──
        ifg_base = merged / "interferograms"
        sample_pair = next((d for d in ifg_base.iterdir() if d.is_dir()), None) if ifg_base.exists() else None

        def _ifg_file(stem: str) -> str:
            if sample_pair and (sample_pair / f"{stem}.geo").exists():
                return str(ifg_base / "*" / f"{stem}.geo")
            return str(ifg_base / "*" / stem)

        self.config.load_unwFile      = _ifg_file("filt_fine.unw")
        self.config.load_corFile      = _ifg_file("filt_fine.cor")
        self.config.load_connCompFile = _ifg_file("filt_fine.unw.conncomp")

        # ── geometry: prefer merged/geometry/, fall back to merged/geom_reference/ ──
        geo = merged / "geometry" if (merged / "geometry").exists() else merged / "geom_reference"

        def _geo(name_geo: str, name_rdr: str) -> str:
            f = geo / name_geo
            return str(f) if f.exists() else str(geo / name_rdr)

        self.config.load_demFile      = _geo("hgt.rdr.geo",       "hgt.rdr")
        self.config.load_incAngleFile = _geo("incLocal.rdr.geo",   "incLocal.rdr")
        self.config.load_lookupYFile  = _geo("lat.rdr.geo",        "lat.rdr")
        self.config.load_lookupXFile  = _geo("lon.rdr.geo",        "lon.rdr")

        # azimuth angle: dedicated file or fall back to los.rdr (band 2)
        az_file = geo / "azimuthAngle.rdr.geo"
        if not az_file.exists():
            az_file = geo / "azimuthAngle.rdr"
        if not az_file.exists():
            az_file = geo / "los.rdr"   # band 2 = azimuth angle
        self.config.load_azAngleFile = str(az_file)

        # shadow/layover mask
        for sn in ("shadowMask.rdr.geo", "shadowMask.rdr"):
            sf = geo / sn
            if sf.exists():
                self.config.load_shadowMaskFile = str(sf)
                break

        # water mask (pre-existing only)
        for wn in ("waterMask.geo", "waterMask.rdr.geo", "waterMask.rdr"):
            wf = geo / wn
            if wf.exists():
                self.config.load_waterMaskFile = str(wf)
                break
        else:
            self.config.load_waterMaskFile = "no"

        self.config.load_baselineDir = str(isce / "baselines")
        self.config.load_metaFile    = self._find_meta_file()

        print(f"{Fore.GREEN}  unwFile      : {self.config.load_unwFile}")
        print(f"  corFile      : {self.config.load_corFile}")
        print(f"  demFile      : {self.config.load_demFile}")
        print(f"  geometry dir : {geo}")
        print(f"  metaFile     : {self.config.load_metaFile}")
        print(f"  baselineDir  : {self.config.load_baselineDir}{Fore.RESET}")

    def _find_meta_file(self) -> str:
        """Locate the reference IW*.xml metadata file in the stackSentinel tree."""
        isce = self.isce_dir

        # isce/reference/IW*.xml  (most common stackSentinel layout)
        ref_dir = isce / "reference"
        if ref_dir.exists():
            xmls = sorted(ref_dir.glob("IW*.xml"))
            if xmls:
                return str(xmls[0])
            # isce/reference/{date}/IW*.xml
            for sub in sorted(ref_dir.iterdir()):
                if sub.is_dir():
                    xmls = sorted(sub.glob("IW*.xml"))
                    if xmls:
                        return str(xmls[0])

        # isce/merged/SLC/{date}/*.xml  (stackSentinel merged SLC layout)
        slc_merged = isce / "merged" / "SLC"
        if slc_merged.exists():
            for date_dir in sorted(slc_merged.iterdir()):
                if date_dir.is_dir():
                    xmls = sorted(date_dir.glob("*.slc.full.xml"))
                    if not xmls:
                        xmls = sorted(date_dir.glob("*.xml"))
                    if xmls:
                        return str(xmls[0])

        # fall back: pass the reference directory and let MintPy scan it
        return str(ref_dir)
