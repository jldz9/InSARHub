"""Network-based ESD misregistration for GMTSAR TOPS stacks.

Why this exists
---------------
GMTSAR's ``preproc_batch_tops[_esd].csh`` aligns every scene to the super-master
in a *single* step: the azimuth misregistration for a scene 144 days from the
master is measured directly across that 144-day gap, where coherence is poor and
the burst-overlap ESD estimate is unreliable. The residual error shows up as
phase discontinuities at burst boundaries, and it grows with temporal distance
from the master.

ISCE's topsStack avoids this by never measuring a long baseline directly:

    run_07_pairs_misreg     ESD on a network of SHORT pairs (12-36 days here)
    run_08_timeseries_misreg  invertMisreg.py -> per-date corrections

Each measurement is made where it is reliable, and the long-baseline correction
is *derived* by inverting the network. On the p100_f466 stack ISCE's residual
misregistration is flat at ~0.0005 px from 12 to 144 days out; GMTSAR's grows.

This module reproduces that topology on top of GMTSAR's own tooling. Nothing
here re-implements the physics -- ``spectral_diversity`` (GMTSAR's ESD) does the
measuring, exactly as ``preproc_batch_tops_esd.csh`` calls it. What changes is
*which pairs get measured* and *how the per-date correction is derived*.

How it plugs into GMTSAR
------------------------
``preproc_batch_tops_esd.csh`` applies its correction at one line::

    gmt grdmath a.grd $res_shift ADD = tmp.grd   # a.grd = azimuth shift table
    make_s1a_tops $file.xml $file.tiff $stem 1 r.grd a.grd

``$res_shift`` there is the direct master->scene ESD estimate. This module
computes a network-inverted value for the same quantity and applies it the same
way, so the downstream chain (stitch_tops, intf_tops, merge) is untouched.

Prerequisites on disk, all produced by a normal align pass:
  ``<stem>.PRM/.LED/.SLC``, ``<stem>_r.grd``, ``<stem>_a.grd``, plus the source
  ``.xml``/``.tiff``. The master has no shift grids -- it is the reference and is
  never resampled, so its correction is 0 by construction.

Limitation: assumes one slice per date (the co-framed stack case, which is what
``organize_files_tops`` produces). Raises if slice counts differ across dates,
rather than silently mis-pairing.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# GMTSAR's ESD filter, same one preproc_batch_tops_esd.csh uses.
_ESD_FILTER = "filters/gauss25x7"

# spectral_diversity stdout:
#   spectral_spectrationXdta = 0.123456
#   residual_shift = 0.001234567890
_RE_SPEC_SEP = re.compile(r"spectral_spectrationXdta\s*=\s*([-\d.eE+]+)")
_RE_RESIDUAL = re.compile(r"residual_shift\s*=\s*([-\d.eE+]+)")


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------

@dataclass
class Scene:
    """One acquisition date's on-disk state within a subswath's raw/ dir."""
    date: str                 # YYYYMMDD
    stem: str                 # per-slice, e.g. S1_20210520_133502_F2
    all_stem: str             # stitched,  e.g. S1_20210520_ALL_F2
    xml: Path
    tiff: Path
    orbit: str                # EOF filename, from data.in
    r_grd: Path | None        # None for the master (never resampled)
    a_grd: Path | None

    @property
    def is_master(self) -> bool:
        return self.a_grd is None


@dataclass
class PairMeasurement:
    ref: str
    sec: str
    shift: float | None       # azimuth pixels, sec relative to ref
    spec_sep: float | None
    nsamples: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.shift is not None and math.isfinite(self.shift)


@dataclass
class NetworkResult:
    corrections: dict[str, float] = field(default_factory=dict)  # date -> px
    measurements: list[PairMeasurement] = field(default_factory=list)
    rank: int = 0
    full_rank: bool = False
    rmse: float = float("nan")
    n_used: int = 0
    n_failed: int = 0


# ---------------------------------------------------------------------------
# network construction
# ---------------------------------------------------------------------------

def build_network(dates: list[str], max_days: int = 48,
                  max_conn: int = 3) -> list[tuple[str, str]]:
    """Short-baseline pair network, mirroring topsStack's overlap pairs.

    Each date connects forward to up to ``max_conn`` neighbours within
    ``max_days``. Defaults give a 12-48 day network for 12-day S1 sampling,
    comparable to the 12-36 day network ISCE built for this stack.

    Connectivity matters more than count: the inversion needs the network to be
    connected, otherwise the design matrix is rank deficient and dates in a
    detached component cannot be tied to the master.
    """
    ds = sorted(set(dates))
    dt = {d: datetime.strptime(d, "%Y%m%d") for d in ds}
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(ds):
        n = 0
        for b in ds[i + 1:]:
            if (dt[b] - dt[a]).days > max_days:
                break
            pairs.append((a, b))
            n += 1
            if n >= max_conn:
                break
    return pairs


def _connected(dates: list[str], pairs: list[tuple[str, str]]) -> bool:
    """Union-find over the measured pairs."""
    parent = {d: d for d in dates}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return len({find(d) for d in dates}) == 1


# ---------------------------------------------------------------------------
# scene discovery
# ---------------------------------------------------------------------------

def discover_scenes(raw: Path) -> list[Scene]:
    """Find per-date stems in a subswath raw/ dir after an align pass.

    Uses the non-"_ALL_" per-slice stems, because the shift grids
    (``<stem>_r.grd``/``_a.grd``) and ``make_s1a_tops`` both operate per slice.
    """
    scenes: list[Scene] = []
    by_date: dict[str, list[Path]] = {}
    for prm in sorted(raw.glob("S1_*.PRM")):
        if "_ALL_" in prm.name:
            continue
        m = re.match(r"S1_(\d{8})_", prm.name)
        if m:
            by_date.setdefault(m.group(1), []).append(prm)

    multi = {d: len(v) for d, v in by_date.items() if len(v) > 1}
    if multi:
        raise ValueError(
            "ESD network requires exactly one slice per date; found multiple "
            f"for {multi}. Re-run organize_files_tops so each date is one "
            "co-framed slice, or extend this module to pair per slice.")

    # data.in maps each slice to its orbit: "<slice_stem>:<EOF>". The name there
    # is NOT necessarily the name on disk: InSARHub stages orbits alongside the
    # SLC and names them after the SLC stem, while data.in keeps the original
    # S1A_OPER_AUX_POEORB_... name. Passing the data.in name straight to
    # ext_orb_s1a fails with "Couldn't open xml file", which previously left a
    # scene half-restitched -- its SLC already moved to the _ALL_ stem but its
    # PRM missing the orbital keys. Resolve against what is actually there.
    orbits: dict[str, str] = {}
    din = raw / "data.in"
    if din.exists():
        for line in din.read_text().splitlines():
            if ":" not in line:
                continue
            slc, _, eof = line.strip().partition(":")
            m = re.search(r"(\d{8})t\d{6}", slc, re.IGNORECASE)
            if not m:
                continue
            date = m.group(1)
            candidates = [eof, f"{slc}.EOF"]
            found = next((c for c in candidates if c and (raw / c).exists()), None)
            if found is None:
                # last resort: any EOF in raw/ carrying this acquisition date
                hits = [p.name for p in raw.glob("*.EOF")
                        if date.lower() in p.name.lower()]
                found = hits[0] if hits else None
            if found is None:
                logger.warning("ESD network: no orbit file on disk for %s "
                               "(data.in says %s); ext_orb_s1a will be skipped",
                               date, eof)
            else:
                orbits[date] = found

    for date, prms in sorted(by_date.items()):
        stem = prms[0].stem
        m = re.match(r"S1_\d{8}_\d{6}_(F\d)", stem)
        all_stem = f"S1_{date}_ALL_{m.group(1)}" if m else stem
        xmls = sorted(raw.glob(f"*{date.lower()}t*.xml")) or \
               sorted(raw.glob(f"*{date}t*.xml"))
        tifs = sorted(raw.glob(f"*{date.lower()}t*.tiff")) or \
               sorted(raw.glob(f"*{date}t*.tiff"))
        if not xmls or not tifs:
            logger.warning("ESD network: no xml/tiff for %s, skipping", date)
            continue
        r = raw / f"{stem}_r.grd"
        a = raw / f"{stem}_a.grd"
        scenes.append(Scene(date=date, stem=stem, all_stem=all_stem,
                            xml=xmls[0], tiff=tifs[0],
                            orbit=orbits.get(date, ""),
                            r_grd=r if r.exists() else None,
                            a_grd=a if a.exists() else None))
    return scenes


def restitch(raw: Path, sc: Scene, env: dict | None, log,
             master_all_stem: str | None = None) -> bool:
    """Promote a regenerated per-slice product to the stitched ``_ALL_`` stem.

    Replicates ``preproc_batch_tops_esd.csh``'s single-slice branch verbatim::

        cp  <slice>.PRM <ALL>.PRM ;  cp <slice>.LED <ALL>.LED
        mv  <slice>.SLC <ALL>.SLC
        update_PRM <ALL>.PRM input_file/SLC_file/led_file
        ext_orb_s1a <ALL>.PRM <orbit> <ALL>

    This matters: everything downstream (stitch, intf_tops, merge) reads the
    ``_ALL_`` stem, and align *moves* the per-slice SLC there -- so a corrected
    per-slice SLC that is not promoted has no effect on the interferograms at
    all.
    """
    slice_slc = raw / f"{sc.stem}.SLC"
    if not slice_slc.exists():
        logger.error("ESD network: %s.SLC missing after regeneration", sc.stem)
        return False
    try:
        shutil.copyfile(raw / f"{sc.stem}.PRM", raw / f"{sc.all_stem}.PRM")
        shutil.copyfile(raw / f"{sc.stem}.LED", raw / f"{sc.all_stem}.LED")
        shutil.move(str(slice_slc), str(raw / f"{sc.all_stem}.SLC"))
    except OSError as exc:                                    # noqa: BLE001
        logger.error("ESD network: promoting %s -> %s failed: %s",
                     sc.stem, sc.all_stem, exc)
        return False
    for key, val in (("input_file", f"{sc.all_stem}.raw"),
                     ("SLC_file", f"{sc.all_stem}.SLC"),
                     ("led_file", f"{sc.all_stem}.LED")):
        _run(["update_PRM", f"{sc.all_stem}.PRM", key, val], raw, env, log)
    if sc.orbit:
        p = _run(["ext_orb_s1a", f"{sc.all_stem}.PRM", sc.orbit, sc.all_stem],
                 raw, env, log)
        if p.returncode != 0:
            logger.error("ESD network: ext_orb_s1a failed for %s: %s",
                         sc.all_stem, (p.stdout or "")[-300:])
            return False
    else:
        logger.warning("ESD network: no orbit for %s in data.in; skipping "
                       "ext_orb_s1a -- the LED may be stale", sc.date)

    # Everything below replicates preproc_batch_tops_esd.csh's post-stitch block
    # VERBATIM (lines 284-313). Two earlier attempts each implemented half of it
    # and each broke a different thing:
    #
    #   resamp only        -> PRM lacked earth_radius/SC_vel/SC_height/fd1;
    #                         phasediff silently emitted no real.grd when the
    #                         scene was used as an interferogram REFERENCE.
    #   calc_dop_orb only  -> keys present, but the SLC kept its NATIVE line
    #                         count instead of the master's (20210321: 12200 vs
    #                         12196), so phasediff refused the pair with
    #                         "The dimensions of azimuth do not match".
    #
    # Both steps are required and the order matters: resamp puts the scene on
    # the master's GRID, calc_dop_orb then writes the orbital geometry onto the
    # resampled PRM.
    if master_all_stem and master_all_stem != sc.all_stem:
        mprm = raw / f"{master_all_stem}.PRM"
        if not mprm.exists():
            logger.error("ESD network: super-master PRM %s not found", mprm.name)
            return False
        # cp $stem.PRM $stem.PRM0
        shutil.copyfile(raw / f"{sc.all_stem}.PRM", raw / f"{sc.all_stem}.PRM0")
        # ashift/rshift zeroed: the geometric shift already lives in the r/a
        # grids make_s1a_tops applied, so resamp must not shift again.
        for key in ("ashift", "rshift"):
            _run(["update_PRM", f"{sc.all_stem}.PRM", key, "0"], raw, env, log)
        p = _run(["resamp", mprm.name, f"{sc.all_stem}.PRM",
                  f"{sc.all_stem}.PRMresamp", f"{sc.all_stem}.SLCresamp", "1"],
                 raw, env, log)
        if p.returncode != 0:
            logger.error("ESD network: resamp onto master failed for %s: %s",
                         sc.all_stem, (p.stdout or "")[-300:])
            return False
        try:
            shutil.move(str(raw / f"{sc.all_stem}.PRMresamp"), str(raw / f"{sc.all_stem}.PRM"))
            shutil.move(str(raw / f"{sc.all_stem}.SLCresamp"), str(raw / f"{sc.all_stem}.SLC"))
        except OSError as exc:                                    # noqa: BLE001
            logger.error("ESD network: resamp outputs missing for %s: %s",
                         sc.all_stem, exc)
            return False
        # fitoffset.csh 3 3 par_tmp.dat >> $stem.PRM -- resamp rewrote the PRM,
        # so the offset polynomial must be re-appended AFTER it, not before.
        restore_prm_fit(raw, sc.all_stem, src_stem=sc.stem)

    # calc_dop_orb, for every scene. Non-masters inherit the MASTER's
    # earth_radius (csh passes $earth_radius, not 0); letting each scene solve
    # its own would give the stack slightly inconsistent geometry.
    prm = raw / f"{sc.all_stem}.PRM"
    radius = "0"
    if master_all_stem and master_all_stem != sc.all_stem:
        radius = _prm_value(raw / f"{master_all_stem}.PRM", "earth_radius") or "0"
    j1, j2 = raw / f"{sc.all_stem}.dop1", raw / f"{sc.all_stem}.dop2"
    shutil.copyfile(prm, j1)
    p = _run(["calc_dop_orb", j1.name, j2.name, radius, "0"], raw, env, log)
    if p.returncode != 0 or not j2.exists():
        logger.error("ESD network: calc_dop_orb failed for %s: %s",
                     sc.all_stem, (p.stdout or "")[-300:])
        return False
    with prm.open("w") as out:
        out.write(j1.read_text())
        out.write(j2.read_text())
    j1.unlink(missing_ok=True)
    j2.unlink(missing_ok=True)

    # Verify BOTH invariants. Checking only the orbital keys is what let the
    # grid mismatch through last time.
    missing = _missing_orbital_keys(prm)
    if missing:
        logger.error("ESD network: %s.PRM lacks %s after calc_dop_orb",
                     sc.all_stem, ", ".join(missing))
        return False
    if master_all_stem and master_all_stem != sc.all_stem:
        mine = _prm_value(prm, "num_lines")
        theirs = _prm_value(raw / f"{master_all_stem}.PRM", "num_lines")
        if mine != theirs:
            logger.error("ESD network: %s num_lines=%s but master has %s -- "
                         "phasediff will reject every pair using this scene",
                         sc.all_stem, mine, theirs)
            return False
    return True


def _prm_value(prm: Path, key: str) -> str | None:
    """Last value of `key` in a PRM. GMTSAR appends duplicates and reads the last."""
    val = None
    try:
        for line in prm.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == key and v.strip():
                val = v.strip()
    except OSError:
        return None
    return val


_ORBITAL_KEYS = ("earth_radius", "SC_vel", "SC_height",
                 "SC_height_start", "SC_height_end", "fd1")


def _missing_orbital_keys(prm: Path) -> list[str]:
    """Orbital-geometry keys absent from a PRM.

    phasediff needs these on the interferogram's REFERENCE scene. Their absence
    is silent -- no error, just no output -- so this is checked explicitly
    rather than discovered three stages later.
    """
    try:
        present = {ln.split("=")[0].strip() for ln in prm.read_text().splitlines()
                   if "=" in ln}
    except OSError:
        return list(_ORBITAL_KEYS)
    return [k for k in _ORBITAL_KEYS if k not in present]


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------

# The 8 offset-polynomial keys fitoffset.csh appends to a PRM. They appear
# TWICE in an aligned PRM -- once as zeros in the base header, once appended
# by fitoffset; GMTSAR reads the last occurrence, so only the tail matters.
_FIT_KEYS = ("rshift", "sub_int_r", "stretch_r", "a_stretch_r",
             "ashift", "sub_int_a", "stretch_a", "a_stretch_a")


def _prm_fit_block(prm: Path) -> dict[str, str]:
    """Last value of each fitoffset key in a PRM -- i.e. the block that
    ``fitoffset.csh 3 3 offset.dat >> $stem.PRM`` appended during align."""
    out: dict[str, str] = {}
    try:
        for line in prm.read_text().splitlines():
            k, _, v = line.partition("=")
            k = k.strip()
            if k in _FIT_KEYS and v.strip():
                out[k] = v.strip()
    except OSError:
        pass
    return out


def backup_prm(raw: Path, stem: str) -> None:
    """Snapshot a PRM before make_s1a_tops overwrites it. Idempotent, so a
    re-run restores from the pristine post-align state rather than from an
    already-regenerated one."""
    prm = raw / f"{stem}.PRM"
    bak = raw / f"{stem}.PRM.presd"
    if prm.exists() and not bak.exists():
        shutil.copyfile(prm, bak)


def restore_prm_fit(raw: Path, stem: str, src_stem: str | None = None) -> bool:
    """Re-append the align pass's offset-polynomial block to a regenerated PRM.

    ``make_s1a_tops`` (modes 1 and 2 alike) rewrites the PRM from the source
    XML, dropping the fitoffset block that stitch_tops and intf_tops rely on.
    GMTSAR's own ESD script handles this by re-running
    ``fitoffset.csh 3 3 offset.dat >> $stem.PRM`` afterwards -- but offset.dat
    is a single scratch file overwritten once per scene during align, so it is
    gone by the time this stage runs. Restoring the saved block is equivalent:
    GMTSAR appends the fit from the ORIGINAL (uncorrected) offset.dat even when
    it has applied an ESD shift to a.grd, because the shift is carried by the
    resampled SLC, not by the polynomial.
    """
    # src_stem allows restoring onto a DIFFERENT PRM than the snapshot came
    # from: the snapshot is taken on the per-slice stem before make_s1a_tops
    # rewrites it, but after resamp the block must be re-appended to the
    # stitched _ALL_ PRM, which is what intf_tops actually reads.
    prm = raw / f"{stem}.PRM"
    bak = raw / f"{src_stem or stem}.PRM.presd"
    if not bak.exists() or not prm.exists():
        return False
    block = _prm_fit_block(bak)
    if len(block) < len(_FIT_KEYS):
        logger.warning("ESD network: %s.PRM.presd has only %d/%d fit keys; "
                       "not restoring a partial block", stem, len(block),
                       len(_FIT_KEYS))
        return False
    with prm.open("a") as fh:
        fh.write("".join(f"{k} = {block[k]}\n" for k in _FIT_KEYS))
    return True


def _run(cmd: list[str], cwd: Path, env: dict | None, log) -> subprocess.CompletedProcess:
    if log is not None:
        log.write(f"\n$ {' '.join(cmd)}\n")
        log.flush()
    return subprocess.run(cmd, cwd=str(cwd), env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def ensure_subbands(raw: Path, sc: Scene, env: dict | None, log) -> bool:
    """Generate ``<stem>.SLCH/.SLCL/.BB`` -- spectral_diversity's inputs.

    ``make_s1a_tops ... mode 2`` writes the high/low sub-band SLCs and the burst
    boundary table. The master is written with no shift grids (it defines the
    grid); every other scene must be written with the SAME shift grids the align
    pass used, so the sub-bands land on the master's geometry.
    """
    if (raw / f"{sc.stem}.BB").exists() and (raw / f"{sc.stem}.SLCH").exists():
        return True
    backup_prm(raw, sc.stem)          # mode 2 rewrites the PRM -- save the fit
    cmd = ["make_s1a_tops", sc.xml.name, sc.tiff.name, sc.stem, "2"]
    if sc.r_grd and sc.a_grd:
        cmd += [sc.r_grd.name, sc.a_grd.name]
    p = _run(cmd, raw, env, log)
    if p.returncode != 0:
        logger.warning("ESD network: sub-band generation failed for %s: %s",
                       sc.stem, (p.stdout or "")[-300:])
        return False
    return (raw / f"{sc.stem}.BB").exists()


def measure_pair(raw: Path, ref: Scene, sec: Scene, sharedir: Path,
                 env: dict | None, log) -> PairMeasurement:
    """Relative azimuth misregistration of ``sec`` w.r.t. ``ref``, in pixels.

    Runs GMTSAR's ``spectral_diversity`` exactly as preproc_batch_tops_esd.csh
    does, then reduces ``ddphase`` the same way its esd_mode=1 branch does::

        res_shift = median(ddphase[:,2]) / (2*pi*spec_sep)

    The median (rather than the mean that ``residual_shift`` reports) is what
    GMTSAR itself prefers for a constant shift, and it is robust to the
    decorrelated overlaps that motivated this whole exercise.
    """
    ddphase = raw / "ddphase"
    ddphase.unlink(missing_ok=True)
    p = _run(["spectral_diversity", ref.stem, sec.stem, "0",
              str(sharedir / _ESD_FILTER)], raw, env, log)
    # NOTE: spectral_diversity exits 1 even on a fully successful run (verified
    # against GMTSAR 6.x: exit 1 with a valid 42 MB / 1.19M-line ddphase). Its
    # return code carries no signal, so success is judged from the output it
    # actually produced -- treating non-zero as failure silently discards every
    # measurement and leaves the geometric alignment untouched.
    m = _RE_SPEC_SEP.search(p.stdout or "")
    if not m:
        return PairMeasurement(
            ref.date, sec.date, None, None,
            error=f"no spectral_spectrationXdta in output (rc={p.returncode}): "
                  f"{(p.stdout or '')[-160:]}")
    spec_sep = float(m.group(1))
    if spec_sep == 0.0:
        return PairMeasurement(ref.date, sec.date, None, spec_sep,
                               error="spec_sep == 0")

    if not ddphase.exists():
        # spectral_diversity degenerate branch: it still prints residual_shift
        r = _RE_RESIDUAL.search(p.stdout or "")
        if r:
            return PairMeasurement(ref.date, sec.date, float(r.group(1)),
                                   spec_sep, nsamples=0)
        return PairMeasurement(ref.date, sec.date, None, spec_sep,
                               error="no ddphase written")

    try:
        arr = np.loadtxt(ddphase, usecols=(2,))
    except Exception as exc:                                  # noqa: BLE001
        return PairMeasurement(ref.date, sec.date, None, spec_sep,
                               error=f"ddphase unreadable: {exc}")
    arr = np.atleast_1d(arr)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return PairMeasurement(ref.date, sec.date, None, spec_sep,
                               error="ddphase empty")
    shift = float(np.median(arr)) / (2.0 * math.pi * spec_sep)
    # keep a per-pair copy for auditing, mirroring GMTSAR's spec_div_output
    shutil.copyfile(ddphase, raw / f"ddphase_{ref.date}_{sec.date}")
    return PairMeasurement(ref.date, sec.date, shift, spec_sep,
                           nsamples=int(arr.size))


# ---------------------------------------------------------------------------
# inversion  (mirrors ISCE topsStack invertMisreg.py)
# ---------------------------------------------------------------------------

def invert_network(dates: list[str], measurements: list[PairMeasurement],
                   master: str) -> NetworkResult:
    """Least-squares inversion of pairwise shifts into per-date corrections.

    Same parameterisation as ISCE's ``invertMisreg.py``: solve for the per-
    interval *rate* with a pinv, then integrate::

        B[i, k1:k2] = dt over the intervals the pair spans
        dS          = pinv(B) @ L
        S           = [0, cumsum(dS * dt)]

    Solving for rates rather than raw offsets keeps the result smooth when the
    network is unevenly connected, and degrades gracefully if a date has only
    one link.

    ISCE references the result to ``dateList[0]``; GMTSAR's super-master is not
    necessarily the earliest date, so the result is re-referenced to ``master``
    (its correction is exactly 0 -- it is never resampled).
    """
    res = NetworkResult(measurements=measurements)
    used = [m for m in measurements if m.ok]
    res.n_used = len(used)
    res.n_failed = len(measurements) - len(used)
    if not used:
        logger.error("ESD network: no usable pair measurements")
        return res

    ds = sorted(set(dates))
    idx = {d: i for i, d in enumerate(ds)}
    t0 = datetime.strptime(ds[0], "%Y%m%d")
    tbase = np.array([(datetime.strptime(d, "%Y%m%d") - t0).days
                      for d in ds], dtype=float)
    dtbase = np.diff(tbase)

    n_int = len(ds) - 1
    B = np.zeros((len(used), n_int))
    L = np.zeros(len(used))
    for i, m in enumerate(used):
        k1, k2 = idx[m.ref], idx[m.sec]
        if k1 > k2:
            k1, k2 = k2, k1
            L[i] = -m.shift
        else:
            L[i] = m.shift
        B[i, k1:k2] = dtbase[k1:k2]

    res.rank = int(np.linalg.matrix_rank(B))
    res.full_rank = res.rank == n_int
    if not res.full_rank:
        logger.warning(
            "ESD network: design matrix rank %d of %d -- network is "
            "disconnected; dates in a detached component keep the geometric "
            "alignment. Increase max_days or max_conn.", res.rank, n_int)

    dS = np.linalg.pinv(B) @ L
    resid = L - B @ dS
    res.rmse = float(np.sqrt(np.sum(resid ** 2) / len(resid)))

    S = np.concatenate(([0.0], np.cumsum(dS * dtbase)))
    S = S - S[idx[master]]                    # master is the datum, exactly 0
    res.corrections = {d: float(S[idx[d]]) for d in ds}
    return res


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------

def apply_corrections(raw: Path, scenes: list[Scene], corrections: dict[str, float],
                      env: dict | None, log, min_shift: float = 1e-4,
                      master_all_stem: str | None = None) -> list[str]:
    """Add the per-date correction to each scene's azimuth shift table and
    regenerate its SLC -- the same two operations GMTSAR itself performs::

        gmt grdmath a.grd <delta> ADD = a.grd
        make_s1a_tops <xml> <tiff> <stem> 1 r.grd a.grd

    The original ``_a.grd`` is preserved as ``_a.grd.geom`` on first run so this
    is idempotent: re-running re-derives from the geometric table rather than
    stacking corrections on top of each other.

    Returns the stems whose SLC was rewritten.
    """
    done: list[str] = []
    for sc in scenes:
        delta = corrections.get(sc.date, 0.0)
        if sc.is_master:
            # The master is never resampled, but ensure_subbands() may still
            # have rewritten its PRM to produce the sub-bands.
            restore_prm_fit(raw, sc.stem)
            continue
        if not math.isfinite(delta) or abs(delta) < min_shift:
            logger.info("ESD network: %s correction %.5f px below threshold, "
                        "leaving geometric alignment", sc.date, delta)
            # Still regenerate: mode 2 clobbered the PRM and left .SLCH/.SLCL
            # in place of a usable .SLC state for downstream.
            _run(["make_s1a_tops", sc.xml.name, sc.tiff.name, sc.stem, "1",
                  sc.r_grd.name, sc.a_grd.name], raw, env, log)
            restore_prm_fit(raw, sc.stem)
            restitch(raw, sc, env, log, master_all_stem)
            continue

        base = sc.a_grd.with_suffix(".grd.geom")
        if not base.exists():
            shutil.copyfile(sc.a_grd, base)       # pristine geometric table

        p = _run(["gmt", "grdmath", base.name, f"{delta:.9f}", "ADD",
                  "=", sc.a_grd.name], raw, env, log)
        if p.returncode != 0:
            logger.error("ESD network: grdmath failed for %s: %s",
                         sc.stem, (p.stdout or "")[-300:])
            continue

        p = _run(["make_s1a_tops", sc.xml.name, sc.tiff.name, sc.stem, "1",
                  sc.r_grd.name, sc.a_grd.name], raw, env, log)
        if p.returncode != 0:
            logger.error("ESD network: SLC regeneration failed for %s: %s",
                         sc.stem, (p.stdout or "")[-300:])
            continue
        if not restore_prm_fit(raw, sc.stem):
            logger.error("ESD network: could not restore the offset polynomial "
                         "in %s.PRM -- stitch_tops/intf_tops would misbehave",
                         sc.stem)
            continue
        if not restitch(raw, sc, env, log, master_all_stem):
            continue
        done.append(sc.stem)
        logger.info("ESD network: %s corrected by %+.5f px -> %s",
                    sc.date, delta, sc.all_stem)
    return done


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def run_esd_network(raw: Path, sharedir: Path, master_date: str,
                    max_days: int = 48, max_conn: int = 3,
                    env: dict | None = None,
                    log_path: Path | None = None,
                    apply: bool = True) -> NetworkResult:
    """Measure a short-baseline ESD network and apply the inverted per-date
    azimuth corrections. Returns the inversion result for reporting.

    Runs *after* a normal align pass, on that pass's output in ``raw``.
    """
    raw = Path(raw)
    logf = open(log_path, "a") if log_path else None
    try:
        scenes = discover_scenes(raw)
        if len(scenes) < 3:
            logger.warning("ESD network: only %d scenes, nothing to invert",
                           len(scenes))
            return NetworkResult()
        by_date = {s.date: s for s in scenes}
        dates = sorted(by_date)
        if master_date not in by_date:
            raise ValueError(f"master {master_date} not among scenes {dates}")

        pairs = build_network(dates, max_days=max_days, max_conn=max_conn)
        logger.info("ESD network: %d scenes, %d pairs (<=%dd, <=%d links/date)",
                    len(dates), len(pairs), max_days, max_conn)

        needed = {d for p in pairs for d in p}
        for d in sorted(needed):
            ensure_subbands(raw, by_date[d], env, logf)

        measurements = []
        for a, b in pairs:
            m = measure_pair(raw, by_date[a], by_date[b], sharedir, env, logf)
            measurements.append(m)
            logger.info("ESD network: %s_%s  %s", a, b,
                        f"{m.shift:+.5f} px (n={m.nsamples})" if m.ok
                        else f"FAILED ({m.error})")

        good = [(m.ref, m.sec) for m in measurements if m.ok]
        if good and not _connected(dates, good):
            logger.warning("ESD network: measured pairs do not connect all "
                           "dates; inversion will be rank deficient")

        res = invert_network(dates, measurements, master_date)
        logger.info("ESD network: rank %d%s, RMSE %.5f px, %d used / %d failed",
                    res.rank, " (full)" if res.full_rank else " (DEFICIENT)",
                    res.rmse, res.n_used, res.n_failed)
        for d in dates:
            logger.info("ESD network:   %s  %+.5f px%s", d,
                        res.corrections.get(d, float("nan")),
                        "  <- master" if d == master_date else "")

        if apply and res.n_used:
            apply_corrections(raw, scenes, res.corrections, env, logf,
                              master_all_stem=by_date[master_date].all_stem)
        return res
    finally:
        if logf:
            logf.close()
