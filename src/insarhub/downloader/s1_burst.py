"""Sentinel-1 SLC-BURST downloader.

ASF distributes individual TOPS bursts as their own granules (dataset
``SLC-BURST``). A burst is roughly 1/9th of a full IW slice, so an AOI-limited
stack pulls far less data than the equivalent SLC search -- that is the entire
point of burst-based processing, and what makes the ISCE3/COMPASS workflow
practical over a small target area.

Bursts on their own are not consumable by SAFE-expecting tools, so
:meth:`S1_Burst.download` hands the selected granules to ``burst2safe``, which
assembles them into valid ``.SAFE`` directories -- merging the annotation,
calibration and noise XML and writing a manifest.

Relationship to the ``burst2stack`` CLI
---------------------------------------
The COMPASS notebook drives this as::

    burst2stack --rel-orbit 124 --pols VV --swaths IW2 IW3 \
                --start-date ... --end-date ... --extent W S E N --output-dir ...

which performs its *own* ASF search internally. This class instead reuses
:class:`ASF_Base_Downloader`'s search, so ``search`` -> ``filter`` -> ``summary``
-> ``footprint`` -> ``select_pairs`` all behave exactly as they do for
``S1_SLC``, and only the already-filtered granule list is passed to
``burst2safe(granules=...)``. One search, and the filters the user applied are
the ones that govern what gets assembled.
"""

from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

from colorama import Fore
from tqdm import tqdm

from insarhub.config import S1_Burst_Config

from .asf_base import ASF_Base_Downloader

logger = logging.getLogger(__name__)


class S1_Burst(ASF_Base_Downloader):
    name = "S1_Burst"
    description = "Sentinel-1 SLC-BURST search and download, assembled into .SAFE via burst2safe."
    default_config = S1_Burst_Config

    # These configure burst2safe assembly, not the ASF query -- forwarding them
    # to asf.search() makes the call fail (and the base retry loop then burns
    # ~17 minutes of backoff before reporting it).
    # frame/asfFrame are excluded deliberately, not just unused: ASF returns NO
    # frameNumber on SLC-BURST products, so a frame filter can only ever match
    # nothing. Worse, a burst stack is keyed by fullBurstID, and the base
    # download() persists its group key as {'frame': key[1]} -- which for a
    # burst is a STRING ("056_118970_IW2", or the "?_?" placeholder when ASF
    # omits burstIndex/subswath). That lands in the folder's insarhub_config
    # and the next search sends it to asf.search(frame=...), which parses frame
    # as an int range and dies with:
    #     Invalid int or range: invalid literal for int() with base 10: '?'
    # Dropping them here means the value can never reach the query no matter
    # what a stale or hand-edited config contains.
    _NON_SEARCH_FIELDS = ASF_Base_Downloader._NON_SEARCH_FIELDS | {
        "swaths", "mode", "min_bursts", "all_anns", "keep_files",
        "frame", "asfFrame"}

    # A burst stack key's second half is a fullBurstID, never a frame number --
    # calling it "frame" in the summary both misnames it and invites the user to
    # feed it back to --frame, which is exactly the int-parse crash described above.
    stack_key_label = "Burst_ID"
    product_label = "bursts"

    search_filter_schema = [
        {"name": "flightDirection", "label": "Flight Direction", "kind": "select",
         "group": "Additional Filters", "choices": ["ASCENDING", "DESCENDING"]},
        {"name": "platform", "label": "Platform", "kind": "select",
         "group": "Additional Filters",
         "choices": ["Sentinel-1A", "Sentinel-1B", "Sentinel-1C", "Sentinel-1D"]},
        {"name": "polarization", "label": "Polarization", "kind": "select",
         "group": "Additional Filters",
         "choices": ["VV", "VV+VH", "HH", "HH+HV"]},
        {"name": "relativeOrbit", "label": "Path", "kind": "range",
         "group": "Path and Frame Filters"},
        {"name": "fullBurstID", "label": "Burst ID", "kind": "text",
         "group": "Path and Frame Filters"},
    ]
    # NOTE: no "beamSwath" filter here, deliberately. ASF leaves beamSwath
    # EMPTY on SLC-BURST products, so passing any value matches nothing --
    # measured over a 3-burst AOI on 2024-01-01..2024-03-01: AOI alone -> 13
    # granules, AOI + beamSwath=IW2 -> 0. Offering it as a search filter meant
    # picking a subswath in the GUI silently returned an empty search.
    # Subswath selection is a client-side concern and lives in `swaths`
    # (Settings -> Burst Assembly), applied in _group_by_date below.

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _granule_of(result) -> str | None:
        """ASF granule name for a search result, tolerating shape differences."""
        props = getattr(result, "properties", None) or {}
        return props.get("fileID") or props.get("sceneName") or props.get("granuleName")

    @staticmethod
    def _swath_of(granule: str) -> str | None:
        """Subswath (IW1/IW2/IW3, EW1..EW5) parsed from the granule name.

        ASF does NOT populate ``beamSwath`` on SLC-BURST products -- it comes
        back None -- so the granule name is the only reliable source::

            S1_264306_IW3_20240915T043118_VV_F23F-BURST
                      ^^^
        """
        for tok in str(granule).split("_"):
            if len(tok) == 3 and tok[:2] in ("IW", "EW") and tok[2].isdigit():
                return tok
        return None

    @staticmethod
    def _date_of(result) -> str | None:
        """YYYYMMDD acquisition date, used to group bursts into one SAFE per date."""
        props = getattr(result, "properties", None) or {}
        start = props.get("startTime") or props.get("stopTime")
        if not start:
            return None
        # startTime is ISO-8601: 2024-09-15T16:12:34.000000Z
        return str(start)[:10].replace("-", "")

    @staticmethod
    def _flatten(results) -> list:
        """Flatten ``active_results``.

        ``ASF_Base_Downloader.active_results`` is a property returning a dict
        grouped by (path, frame) -- one entry per stack when an AOI spans more
        than one. Burst assembly is per-acquisition, so the grouping is redone
        by date here; flatten first.
        """
        if isinstance(results, dict):
            return [r for group in results.values() for r in group]
        return list(results)

    @staticmethod
    def _path_of(result) -> int | None:
        """Relative orbit (track). ASF leaves ``relativeOrbit`` empty on BURST
        products; the value lives in ``pathNumber``, and also as the first field
        of ``burst.fullBurstID`` ("124_264312_IW2")."""
        props = getattr(result, "properties", None) or {}
        p = props.get("pathNumber") or props.get("relativeOrbit")
        if p is not None:
            return int(p)
        fid = (props.get("burst") or {}).get("fullBurstID") or ""
        head = str(fid).split("_")[0]
        return int(head) if head.isdigit() else None

    def _get_group_key(self, result) -> tuple:
        """Group bursts by ``(path, fullBurstID)``: one fixed burst position.

        The base implementation groups by ``(pathNumber, frameNumber)``, but
        ASF burst granules carry NO ``frameNumber`` -- so every burst of a
        track collapsed onto ``(124, None)`` and the GUI rendered them all as
        "Path 0 · Frame 0". A burst stack is a burst position that repeats
        every revisit, uniquely identified by its OPERA ``fullBurstID``
        ("124_266256_IW3" = path_burstIndex_subswath).
        """
        props = result.properties
        path = props.get("pathNumber")
        if path is None:
            path = self._path_of(result)
        b = props.get("burst") or {}
        full = (props.get("fullBurstID") or b.get("fullBurstID")
                or props.get("relativeBurstID") or b.get("relativeBurstID"))
        if not full:
            # Last resort: the granule name always carries the subswath, and is
            # a real identifier. The previous fallback built "?_?" from missing
            # burstIndex/subswath -- a placeholder that reads as data, groups
            # every unidentifiable burst together, and (via the base download's
            # group-key persistence) ended up in configs as frame="?_?".
            g = self._granule_of(result) or ""
            full = g or f"unknown_{self._date_of(result) or 'nodate'}"
            logger.warning("S1_Burst: result carries no burst ID; grouping by "
                           "granule name %r instead", full)
        return (path, full)

    @staticmethod
    def _burst_id_parts(text) -> tuple[int, int, str] | None:
        """``"087_185682_IW2"`` -> ``(87, 185682, "IW2")``; None if not that shape.

        Path and index come back as ints so the zero-padded form ASF prints
        (``087_...``) and the bare form a user types (``87_...``) compare equal.
        """
        parts = str(text).strip().upper().split("_")
        if len(parts) != 3:
            return None
        path, index, swath = parts
        if not (path.isdigit() and index.isdigit()):
            return None
        return int(path), int(index), swath

    def _stack_key_matches(self, key: tuple, target: tuple) -> bool:
        """Match a burst stack against a user ``PATH:SELECTOR`` token.

        Burst stacks key on ``fullBurstID`` (see :meth:`_get_group_key`), so the
        second half of a ``--stacks`` token is not a number and the base class's
        plain-equality rule can never match it. Three spellings of one stack are
        accepted, widest last::

            124:124_264305_IW2   full burst ID, exactly as summary() prints it
            124:264305_IW2       burst index + subswath
            124:264305           bare burst index -- EVERY subswath at that index

        The bare index is deliberately one-to-many: ASF reuses an index across
        subswaths (``124_266256_IW2`` and ``124_266256_IW3`` both exist), so it
        selects both. Name the subswath to pin exactly one.

        A stack that fell back to grouping by granule name (no burst ID on the
        product at all) matches only on a verbatim string, since it has no index
        or subswath to compare against.
        """
        try:
            if int(key[0]) != int(target[0]):
                return False
        except (TypeError, ValueError):
            return False   # a stack with no path can only be reached by AOI, not by token

        selector = str(target[1]).strip().upper()
        full     = str(key[1]).strip().upper()
        if not selector:
            return False
        if selector == full:
            return True

        key_parts = self._burst_id_parts(full)
        if key_parts is None:
            return False            # granule-name fallback key: verbatim match only
        _, index, swath = key_parts

        sel_parts = self._burst_id_parts(selector)
        if sel_parts is not None:
            return sel_parts == key_parts

        bits = selector.split("_")
        if len(bits) == 2 and bits[0].isdigit():
            return int(bits[0]) == index and bits[1] == swath
        return selector.isdigit() and int(selector) == index

    @staticmethod
    def folder_name(path: int, subswath: str | None = None,
                    burst_id: int | None = None) -> str:
        """Job-folder name for a burst selection.

        A single burst is one fixed burst position -> a per-burst folder
        ``p<path>_iw<s>_b<id>``, where ``id`` is the OPERA relative burst ID
        (unique per subswath across the orbit, e.g. 266256). A whole track
        (several bursts merged) is one ``p<path>`` folder, because burst2safe
        assembles one SAFE per (date, path) anyway -- the track is the unit the
        ISCE3_Burst processor consumes.
        """
        if subswath and burst_id is not None:
            sw = str(subswath).lower()          # "IW3" -> "iw3"
            num = sw[2:] if sw[:2] in ("iw", "ew") else sw   # -> "3"
            return f"p{path}_iw{num}_b{burst_id}"
        return f"p{path}"

    def _group_by_date(self, results) -> dict[str, list[str]]:
        """{"YYYYMMDD" or "YYYYMMDD_pNNN": [granule, ...]} -- one SAFE per group.

        Grouped by acquisition date AND path. A SAFE is a single pass, and
        burst2safe enforces "all bursts must have the same absolute orbit"; a
        date-only key would merge two paths imaged on the same day into one
        call, which raises and -- because assembly failures are caught per group
        -- would silently drop BOTH paths for that date.

        Two paths on one date is uncommon for a small AOI (over Hawaii, paths
        14/87/124 all fall on different days) but is normal where passes
        converge at high latitude or where an AOI sees both ascending and
        descending on the same day. The key stays the bare date in the common
        single-path case so output names are unchanged.
        """
        # A bare string is tolerated: swaths round-trips through JSON configs
        # and a hand-edited "IW2" would otherwise iterate as {"I","W","2"} and
        # drop every granule.
        _sw = getattr(self.config, "swaths", None) or []
        want = {s.upper() for s in ([_sw] if isinstance(_sw, str) else _sw)}
        # s1reader derives the OPERA burst ID from the IW2 mid-burst sensing
        # time, so it opens the IW2 ANNOTATION unconditionally -- whichever
        # subswath you asked for:
        #     ValueError: burst iw2-slc-vv not in SAFE: <dir>
        #
        # But annotation is all it needs, not IW2 measurement data. Verified by
        # assembling an IW3-ONLY SAFE with all_anns=True (measurement: iw3;
        # annotation: iw1+iw2+iw3) -- s1reader loaded all 4 IW3 bursts fine.
        # So:
        #   all_anns=True  -> slice-level annotation for every subswath is
        #                     included anyway; adding IW2 DATA would roughly
        #                     double the download for nothing.
        #   all_anns=False -> only the requested subswaths' annotation is kept,
        #                     so IW2 must be pulled in or the SAFE is unreadable.
        if (want and str(getattr(self.config, "mode", "IW")).upper() == "IW"
                and "IW2" not in want
                and not bool(getattr(self.config, "all_anns", False))):
            print(f"[S1_Burst] adding IW2 to swaths {sorted(want)}: s1reader needs the "
                  f"IW2 annotation for the burst-ID reference time, and all_anns is "
                  f"off. Set all_anns=True to keep {sorted(want)} only and avoid "
                  f"downloading IW2 data.")
            want = want | {"IW2"}
        groups: dict[str, list[str]] = defaultdict(list)
        skipped = dropped = 0
        for r in self._flatten(results):
            g, d = self._granule_of(r), self._date_of(r)
            if not g or not d:
                skipped += 1
                continue
            # config.swaths must be applied HERE. It cannot be applied at search
            # time (ASF leaves beamSwath empty on BURST products), and it cannot
            # be delegated to burst2safe either: when burst2safe is given an
            # explicit granule list it assembles exactly those granules and
            # ignores its own `swaths` argument. Verified against a live
            # download -- swaths=["IW3"] still produced an IW2+IW3 SAFE.
            if want and self._swath_of(g) not in want:
                dropped += 1
                continue
            groups[(d, self._path_of(r))].append(g)
        if skipped:
            logger.warning("S1_Burst: %d result(s) lacked a granule name or start "
                           "time and were skipped", skipped)
        if dropped:
            print(f"[S1_Burst] swath filter {sorted(want)}: dropped {dropped} burst(s)")

        # Flatten (date, path) -> label. Keep the bare date when a date has only
        # one path (the normal case), so names match what callers already expect.
        per_date: dict[str, int] = defaultdict(int)
        for (d, _p) in groups:
            per_date[d] += 1
        out: dict[str, list[str]] = {}
        for (d, path), gr in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
            key = d if per_date[d] == 1 else f"{d}_p{path if path is not None else 'NA'}"
            out[key] = gr
        multi = [d for d, n in per_date.items() if n > 1]
        if multi:
            print(f"[S1_Burst] {len(multi)} date(s) span multiple paths; assembling "
                  f"one SAFE per path: {sorted(multi)}")
        return out

    # ------------------------------------------------------------------
    # pair selection
    # ------------------------------------------------------------------

    #: parent SLC granule embedded in every burst's download URL
    _PARENT_RE = re.compile(
        r"(S1[ABCD]_IW_SLC__\w{4}_\d{8}T\d{6}_\d{8}T\d{6}_\d{6}_\w{6}_\w{4})")

    def parent_slcs(self, results=None) -> dict[str, set[str]]:
        """{parent SLC granule: {YYYYMMDD, ...}} for the current burst results.

        ASF serves each burst from its parent slice, and names it in the URL::

            https://sentinel1-burst.asf.alaska.edu/S1A_IW_SLC__1SDV_2024...96A1/...

        That is the only link back to a product ASF actually publishes baselines
        for -- burst granules themselves carry ``perpendicularBaseline: None``.
        """
        out: dict[str, set[str]] = defaultdict(set)
        for r in self._flatten(self.active_results if results is None else results):
            m = self._PARENT_RE.search((getattr(r, "properties", {}) or {}).get("url") or "")
            d = self._date_of(r)
            if m and d:
                out[m.group(1)].add(d)
        return dict(out)

    def sequential_pairs(self, n_connections: int = 3, dates=None) -> list[tuple[str, str]]:
        """Tutorial-exact sequential pairing: each date to its next N neighbours.

        Byte-for-byte the rule in the COMPASS stack notebook's
        ``utils.generate_ifgram_pairs``::

            max_step = min(n_connections + 1, len(dates))
            for i in range(len(dates) - 1):
                for j in range(i + 1, min(i + max_step, len(dates))):
                    pairs.append((dates[i], dates[j]))

        Use this when the goal is to reproduce the tutorial exactly.
        :meth:`select_pairs` cannot: it is target-driven (``dt_targets``) rather
        than rule-driven, so the closest it gets on the 9-date Hawaii stack is a
        23-pair SUPERSET of these 21 (at ``pb_max=400, max_degree=6``), never
        the set itself.

        Note what this deliberately ignores: perpendicular baseline. On that
        same stack it emits ``20240927_20241009`` (12 d but 300 m dBperp) and
        ``20240903_20240915`` (12 d, 208 m), which :meth:`select_pairs` rejects
        as geometrically decorrelated. That is the tutorial's behaviour, not a
        defect here -- but it is the reason the two disagree.

        Args:
            n_connections: Neighbours ahead to connect each date to.
            dates: Explicit YYYYMMDD list; defaults to the dates in the current
                search results.

        Returns:
            Sorted ``[(YYYYMMDD, YYYYMMDD), ...]``.
        """
        if dates is None:
            dates = sorted({d for d in (self._date_of(r)
                                        for r in self._flatten(self.active_results)) if d})
        else:
            dates = sorted(set(dates))
        if len(dates) < 2:
            print(f"[S1_Burst] only {len(dates)} date(s); no pairs to form")
            return []
        max_step = min(n_connections + 1, len(dates))
        pairs = [(dates[i], dates[j])
                 for i in range(len(dates) - 1)
                 for j in range(i + 1, min(i + max_step, len(dates)))]
        print(f"[S1_Burst] sequential pairing (tutorial rule): {len(dates)} dates, "
              f"n_connections={n_connections} -> {len(pairs)} pairs")
        return sorted(pairs)

    def select_pairs(self, *args, **kwargs):
        """Baseline-aware pair selection for bursts, computed burst-natively.

        Why this override exists: ASF publishes NO baseline metadata for
        SLC-BURST products -- ``perpendicularBaseline``, ``temporalBaseline``
        and ``insarStackId`` are all None -- so the inherited implementation
        (which reads state vectors / baselines off each product) selects
        against data that does not exist. Measured on a 9-date Hawaii stack:
        the naive path returned 16 pairs instead of 21, silently dropping two
        12-day pairs (the highest-coherence ones) and leaving the final date
        with "0 / 3 connections available".

        The burst-native path in :func:`insarhub.utils.select_pairs` treats
        each acquisition **date** as the pairing node (one date = one stitched
        SAFE after burst2safe): temporal baseline comes from the burst's
        ``startTime`` and perpendicular baseline from per-date orbit state
        vectors (assembled ``.SAFE`` annotation, local ``.EOF``, or POEORB by
        date+mission). No parent-SLC lookup is performed.

        Returns:
            The base implementation's structure, with scene names replaced by
            ``YYYYMMDD`` acquisition dates -- the identifier that is meaningful
            for a burst stack (a date has many bursts, so no single burst
            granule can stand for it).
        """
        # Any configured orbit sources from the current workdir (post-download).
        workdir = getattr(self.config, "workdir", None)
        safe_dir = eof_dir = None
        if workdir:
            from pathlib import Path as _Path
            _w = _Path(workdir)
            slc = _w / "slc"
            if slc.is_dir():
                safe_dir = str(slc)
                eof_dir = str(slc)      # S1_Burst writes .EOF beside the SAFEs
        kwargs.setdefault("burst", True)
        if safe_dir and "safe_dir" not in kwargs:
            kwargs["safe_dir"] = safe_dir
        if eof_dir and "eof_dir" not in kwargs:
            kwargs["eof_dir"] = eof_dir
        kwargs.setdefault("poeorb_cache", _Path.home() / ".insarhub" / "poeorb")
        return super().select_pairs(*args, **kwargs)

    @staticmethod
    def write_ifgram_list(pairs, path) -> Path:
        """Write pairs as ``ifgram_list.txt`` (one ``YYYYMMDD_YYYYMMDD`` per line).

        This is the format the COMPASS stack notebook's
        ``generate_ifgram_pairs`` produces and section 3.3 consumes, so a
        baseline-aware selection can be dropped in place of the purely
        sequential one.
        """
        flat = ([p for v in pairs.values() for p in v]
                if isinstance(pairs, dict) else list(pairs))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            fh.write("# date12\n")
            for a, b in sorted(set(flat)):
                fh.write(f"{a}_{b}\n")
        print(f"[S1_Burst] wrote {len(set(flat))} pair(s) -> {path}")
        return path

    # ------------------------------------------------------------------
    # download
    # ------------------------------------------------------------------

    def download(self, save_path: str | None = None, max_workers: int | None = None,
                 download_orbit: bool = False, force_cdse: bool = False,
                 stop_event=None, on_progress=None, merge: bool = False):
        """Download the selected bursts and assemble them into .SAFE directories.

        Unlike :class:`S1_SLC`, this does not stream files straight from ASF --
        ``burst2safe`` owns the download so it can also fetch each burst's
        annotation/calibration/noise XML and merge them into a coherent product.
        ``max_workers`` is therefore accepted for interface parity but not used;
        burst2safe manages its own concurrency.

        Args:
            save_path: Destination root. Defaults to the configured workdir.
            max_workers: Accepted for parity with S1_SLC; unused (see above).
            download_orbit: Also fetch the matching precise orbits (.EOF), which
                every downstream processor needs.
            force_cdse: Fetch orbits from CDSE instead of ASF.
            stop_event: threading.Event to cancel between date groups.
            on_progress: callback(message, pct) after each date group.
            merge: Assemble every stack into one directory instead of per-stack
                subfolders.

        Returns:
            list[Path]: the assembled .SAFE directories.
        """
        try:
            from burst2safe.burst2safe import burst2safe
        except ImportError as exc:                                  # noqa: BLE001
            raise ImportError(
                "S1_Burst requires the 'burst2safe' package, which provides the "
                "burst -> SAFE assembly step. Install it with:\n"
                "    conda install -c conda-forge burst2safe\n"
                f"(original error: {exc})") from exc

        results = self._flatten(self.active_results)   # property, dict-grouped
        if not results:
            print("[S1_Burst] No search results to download. Run search() first.")
            return []

        # Default to <workdir>/slc, the same layout S1_SLC produces: scenes and
        # their .EOF orbits together in one lowercase "slc" directory.
        out_dir = (Path(save_path) if save_path
                   else Path(getattr(self.config, "workdir", ".") or ".") / "slc")
        out_dir.mkdir(parents=True, exist_ok=True)

        groups = self._group_by_date(results)
        if not groups:
            print("[S1_Burst] No usable burst granules in the current results.")
            return []

        cfg = self.config
        pols = cfg.polarization
        if isinstance(pols, str):
            pols = [pols]
        pols = list(pols) if pols else None

        # --worker N (CLI) / max_workers param overrides the config field;
        # otherwise fall back to cfg.max_workers (GUI-set), then 3.
        nw = max(1, int(max_workers or getattr(self.config, "max_workers", None) or 3))

        n_bursts = sum(len(v) for v in groups.values())
        # Match S1_SLC's summary line. This override never calls
        # super().download(), so the base's banner (asf_base.download) never
        # printed for bursts and the two downloaders reported differently.
        print(f"Downloading {n_bursts} burst(s) across {len(groups)} date group(s)"
              f" ({nw} concurrent)...\n")
        print(f"[S1_Burst] assembling .SAFE in {out_dir}")

        # A date normally maps to exactly one group. It splits when the same
        # date carries more than one relative orbit -- real at high latitude or
        # where an AOI sees both passes, but also what happens when _path_of()
        # cannot parse a granule and returns None, which silently invents a
        # 'pNA' group. Either way the group count then exceeds the scene count,
        # so say which it is rather than leaving an unexplained off-by-N.
        _split = sorted(k for k in groups if "_p" in k)
        if _split:
            _na = [k for k in _split if k.endswith("_pNA")]
            print(f"[S1_Burst] {len(_split)} date(s) split across paths: "
                  f"{', '.join(_split)}")
            if _na:
                logger.warning(
                    "S1_Burst: %d group(s) have an unparseable relative orbit "
                    "(%s). These come from granules whose path could not be read "
                    "and will assemble separately, inflating the group count "
                    "above the number of dates.", len(_na), ", ".join(_na))

        # Same bookkeeping the base download() does for S1_SLC: a stack folder
        # is identified by its insarhub_config.json + workflow marker, and
        # without them the folder is not recognised as a stack by the GUI, the
        # CLI, or the processors. S1_Burst writes its SAFEs into <stack>/slc,
        # so the stack folder is out_dir's parent.
        self._mark_stack_dir(out_dir.parent if out_dir.name == "slc" else out_dir)
        # Authorize the ASF session once before the worker threads race on it.
        _ = self.session
        safes: list[Path] = []
        failed: list[str] = []

        # --worker N runs up to N dates in parallel. Each worker downloads its
        # date's bursts (one per-burst bar at its own position) then assembles
        # the SAFE; the shared assembly bar below counts completed dates.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        date_tasks = [
            (idx, date_str, granules,
             [r for r in results if self._granule_of(r) in set(granules)])
            for idx, (date_str, granules) in enumerate(groups.items())
        ]
        with tqdm(total=len(groups), desc="[S1_Burst] assembling SAFE",
                  unit="SAFE", leave=True, colour="green", position=0) as pbar:
            with ThreadPoolExecutor(max_workers=nw) as ex:
                futures = {
                    ex.submit(self._date_worker, idx, date_str, granules,
                              prods, out_dir, cfg, pols, pbar,
                              stop_event, on_progress, len(groups), nw):
                        (date_str, granules)
                    for idx, date_str, granules, prods in date_tasks
                }
                for fut in as_completed(futures):
                    date_str, granules = futures[fut]
                    try:
                        safe = fut.result()
                    except Exception as exc:                    # noqa: BLE001
                        logger.error("S1_Burst: worker failed for %s: %s",
                                     date_str, exc)
                        safe = None
                    if safe is not None:
                        safes.append(safe)
                    else:
                        failed.append(date_str)
                    if stop_event is not None and stop_event.is_set():
                        for f in futures:
                            f.cancel()
                        break
        if failed:
            print(f"[S1_Burst] {len(failed)} date(s) failed to assemble: "
                  f"{', '.join(sorted(failed))}")
        print(f"[S1_Burst] assembled {len(safes)} / {len(groups)} .SAFE directory(ies).")
        self._report_burst_consistency(groups)

        if download_orbit and safes:
            # Orbits land ALONGSIDE the .SAFE directories, matching S1_SLC,
            # which writes .EOF into the same slc/ folder as the scenes rather
            # than a sibling orbits/ dir.
            self.download_orbit_for_safes(safes, force_cdse=force_cdse)
        return safes

    @staticmethod
    def _burst_of(granule: str) -> str | None:
        """Burst position ``<relativeBurstID>_<subswath>`` from a granule name.

        ``S1_118970_IW2_20240102T131002_VV_A124-BURST`` -> ``118970_IW2``.
        """
        toks = str(granule).split("_")
        for i, t in enumerate(toks):
            if len(t) == 3 and t[:2] in ("IW", "EW") and t[2].isdigit():
                return f"{toks[i - 1]}_{t}" if i else t
        return None

    def _report_burst_consistency(self, groups: dict) -> None:
        """Warn about dates that lack a burst other dates have.

        Runs after assembly, once every date's real content is known. A date
        missing one of the stack's burst positions still assembles -- with
        ``min_bursts=1`` it becomes a short SAFE covering less ground -- and
        nothing downstream flags it. It surfaces much later, and obscurely: the
        burst stacks end up with different date lists, so the interferogram
        network built from one burst prescribes pairs another cannot form, and
        those pairs silently stitch from a single burst into half-width
        products.

        Reported here because this is the first point where the answer is
        knowable and still cheap to act on -- before geocoding hours of data.
        """
        by_date: dict[str, set[str]] = {}
        for key, granules in groups.items():
            date = key[0] if isinstance(key, tuple) else key
            for g in granules:
                b = self._burst_of(g)
                if b:
                    by_date.setdefault(str(date), set()).add(b)
        if not by_date:
            return

        all_bursts = set().union(*by_date.values())
        short = {d: sorted(all_bursts - b) for d, b in by_date.items()
                 if all_bursts - b}
        if not short:
            print(f"[S1_Burst] burst coverage consistent: all "
                  f"{len(by_date)} date(s) have the same {len(all_bursts)} "
                  f"burst position(s)")
            return

        print(f"{Fore.YELLOW}[S1_Burst] WARNING: {len(short)} of "
              f"{len(by_date)} date(s) are missing a burst that other dates "
              f"have. ASF has no data for those burst/date combinations.")
        for d in sorted(short):
            print(f"    {d}  missing {', '.join(short[d])}")
        print(f"  These assemble as SHORT SAFEs (min_bursts="
              f"{getattr(self.config, 'min_bursts', 1)}) covering less ground. "
              f"Downstream, ISCE3_Burst excludes them from the interferogram "
              f"network so it stays formable on every burst -- so they cost "
              f"you those dates. Set min_bursts to {len(all_bursts)} to skip "
              f"them at download time instead.{Fore.RESET}")
        logger.warning("S1_Burst: %d date(s) with incomplete burst coverage: %s",
                       len(short), ", ".join(sorted(short)))

    def _stream_burst_download(self, file_id: str, url: str, dst: Path,
                               expected_bytes: int | None, position: int,
                               stop_event=None) -> bool:
        """Stream one burst's ``.tiff`` with its own tqdm bar.

        Saves to ``<out_dir>/<fileID>.tiff`` — the exact location burst2safe
        expects for its data files, so it skips them during assembly.
        Returns True on success; partial files are removed on failure.
        """
        from asf_search.download.download import _try_get_response
        import asf_search as asf

        if dst.exists() and expected_bytes and dst.stat().st_size >= expected_bytes:
            return True
        thread_session = asf.ASFSession()
        thread_session.cookies.update(self.session.cookies)
        thread_session.headers.update(self.session.headers)
        thread_session.verify = getattr(self.config, "ssl_verify", True)
        try:
            response = _try_get_response(session=thread_session, url=url)
            total = int(response.headers.get("content-length", expected_bytes or 0))
            # desc/format match ASF_Base_Downloader.download's per-file bar, but
            # leave=False is deliberate and differs from S1_SLC: S1_SLC has no
            # aggregate bar, so its per-file bars can persist harmlessly. Here
            # the "assembling SAFE" bar is pinned at position 0, and every
            # left-behind inner bar pushes it down the screen until it scrolls
            # away. Transient inner bars keep the aggregate at the top.
            with tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024,
                      desc=f"[Worker {position}] {file_id}", leave=False,
                      position=position, colour="green",
                      bar_format="{desc:<60}{percentage:3.0f}%|{bar:25}{r_bar}") as bar:
                with open(dst, "wb") as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if stop_event is not None and stop_event.is_set():
                            response.close()
                            raise InterruptedError("Download cancelled by user.")
                        if chunk:
                            f.write(chunk)
                            bar.update(len(chunk))
            return True
        except InterruptedError:
            dst.unlink(missing_ok=True)
            raise
        except Exception:                                        # noqa: BLE001
            dst.unlink(missing_ok=True)
            return False

    def _date_worker(self, idx: int, date_str: str, granules: list[str],
                     prods, out_dir: Path, cfg, pols, asm_bar,
                     stop_event, on_progress, n_dates: int, n_workers: int = 1):
        """Download one date's bursts (sequentially) and assemble its SAFE.

        Runs in its own thread (one per active date, ``--worker N`` = N dates
        in parallel). Each burst gets a per-burst bar on this worker's own
        terminal line; the shared ``asm_bar`` at position 0 counts completed
        dates.

        The bar position is the worker SLOT (``idx % n_workers``), not the date
        index. tqdm reserves one terminal line per position, so using the date
        index asked for as many lines as there are dates -- 112 on a real stack
        -- while only ``n_workers`` are ever live, leaving the bars scattered
        down the screen with large gaps. S1_SLC wraps the same way
        (``i % max_workers`` in ASF_Base_Downloader.download).
        """
        from burst2safe.burst2safe import burst2safe

        try:
            for r in prods:
                props = r.properties
                file_id = self._granule_of(r)
                url = props.get("url")
                if not file_id or not url:
                    continue
                if stop_event is not None and stop_event.is_set():
                    return None
                self._stream_burst_download(
                    file_id, url, out_dir / f"{file_id}.tiff",
                    props.get("bytes"),
                    position=(idx % max(1, n_workers)) + 1,
                    stop_event=stop_event)

            buf = io.StringIO()
            safe, exc = None, None
            try:
                # NOTE: no swaths= here. burst2safe ignores it when given an
                # explicit granule list; the filter is already applied in
                # _group_by_date(), so `granules` is exactly what we want.
                with redirect_stdout(buf):
                    safe = burst2safe(
                        granules=granules,
                        polarizations=pols,
                        mode=getattr(cfg, "mode", "IW"),
                        min_bursts=int(getattr(cfg, "min_bursts", 1)),
                        all_anns=bool(getattr(cfg, "all_anns", False)),
                        keep_files=bool(getattr(cfg, "keep_files", False)),
                        work_dir=out_dir,
                    )
            except Exception as e:                              # noqa: BLE001
                # One bad date should not lose the rest of the stack. The
                # granule-list path re-searches ASF by name and reads each
                # product's UMM "InputGranules"; when that field is missing
                # (a burst2safe/asf_search fragility) it raises KeyError. Try
                # the orbit+extent group path as a fallback first.
                exc = e
                with redirect_stdout(buf):
                    safe = self._try_group_assembly(
                        date_str, granules, prods, out_dir, cfg)
            asm_bar.update(1)
            if safe is not None:
                asm_bar.set_postfix_str(f"{date_str} ✓ {Path(safe).name}")
                if exc is not None:
                    asm_bar.write(f"[S1_Burst] {date_str}: recovered via "
                                  f"group fallback -> {Path(safe).name}")
                if on_progress:
                    on_progress(f"assembled {date_str}",
                                int(100.0 * asm_bar.n / max(1, n_dates)))
                return Path(safe)
            tail = "\n".join((buf.getvalue() or "").strip().splitlines()[-8:])
            logger.error("S1_Burst: assembly failed for %s (%s): %s%s",
                         date_str, ", ".join(granules), exc,
                         f"\n  burst2safe output:\n{tail}" if tail else "")
            self._warn_failed_date(date_str, granules, prods, exc)
            self._cleanup_failed_date(date_str, granules, prods, out_dir, cfg)
            if on_progress:
                on_progress(f"assembly failed {date_str}", 0)
            return None
        except Exception as exc:                                # noqa: BLE001
            logger.error("S1_Burst: date %s raised: %s", date_str, exc)
            return None

    def _warn_failed_date(self, date_str: str, granules: list[str],
                          prods, exc: Exception) -> None:
        """Explain *why* a date's assembly failed, when the cause is known.

        burst2safe's error text is the source of truth for the common failure
        modes; this makes them legible in the log instead of a bare ``ValueError``:
        - non-consecutive burst IDs (a burst missing in the requested
          polarization) -> name the gap and offer the fix
        - ``InputGranules`` KeyError -> burst2safe/asf_search UMM fragility,
          already covered by the group fallback
        """
        msg = str(exc)
        if "consecutive burst IDs" in msg:
            # e.g. "All bursts must have consecutive burst IDs. Found: [118969, 118971]."
            found = re.findall(r"\d+", msg)
            ids = sorted(set(int(x) for x in found)) if found else []
            gap = ""
            if len(ids) >= 2:
                missing = [i for i in range(ids[0], ids[-1] + 1) if i not in set(ids)]
                if missing:
                    pol = getattr(self.config, "polarization", None)
                    if isinstance(pol, str):
                        pol = [pol]
                    pol_s = ",".join(sorted(pol)) if pol else "the selected polarization"
                    gap = (f" — burst(s) {missing} have no {pol_s} product on ASF, "
                           f"so the remaining bursts cannot form one SAFE. "
                           f"Drop that date, widen the AOI, or add the missing "
                           f"polarization.")
            logger.warning(
                "S1_Burst: %s skipped: bursts %s are not consecutive%s",
                date_str, ids, gap)
        elif "InputGranules" in msg:
            logger.warning(
                "S1_Burst: %s skipped: burst2safe could not resolve the parent "
                "SLC granules (UMM 'InputGranules' missing). The group fallback "
                "was already attempted and also failed.", date_str)
        else:
            logger.warning("S1_Burst: %s skipped: %s", date_str, msg.splitlines()[0])

    def _cleanup_failed_date(self, date_str: str, granules: list[str],
                             prods, out_dir: Path, cfg) -> None:
        """Remove a failed date's pre-downloaded per-burst ``.tiff`` files.

        On success burst2safe deletes its own data files (``keep_files=False``),
        but on failure the pre-downloaded ``<fileID>.tiff`` files stay behind and
        silently consume ~500 MB each. Remove them here so a failed date leaves
        no orphans; honour ``keep_files`` for users who explicitly asked to keep
        them.
        """
        if bool(getattr(cfg, "keep_files", False)):
            return
        removed: list[str] = []
        for r in prods:
            file_id = self._granule_of(r)
            if not file_id:
                continue
            tiff = out_dir / f"{file_id}.tiff"
            try:
                if tiff.exists():
                    tiff.unlink()
                    removed.append(tiff.name)
            except OSError as e:
                logger.warning("S1_Burst: could not remove %s: %s", tiff, e)
        if removed:
            print(f"[S1_Burst] {date_str}: removed {len(removed)} failed "
                  f"burst .tiff file(s): {', '.join(sorted(removed))}")

    def _try_group_assembly(self, date_str: str, granules: list[str],
                            prods, out_dir: Path, cfg) -> Path | None:
        """Fallback assembly via burst2safe's orbit + extent group path.

        The granule-list path (``burst2safe(granules=...)``) re-searches ASF by
        name and reads each product's UMM ``InputGranules`` to recover the
        parent SLC; when that field is absent on a product the whole call dies
        with ``KeyError('InputGranules')`` (a burst2safe/asf_search fragility)
        and the date's SAFE is silently skipped. The group path searches by
        ``absoluteOrbit`` + footprint instead -- a parameter search that returns
        complete products -- so it can assemble the same bursts even when the
        name-search UMM is incomplete.

        ``prods`` is the date's already-matched ASF products (from the original
        search), not the re-search.

        Returns the assembled SAFE path, or None on any failure.
        """
        try:
            from burst2safe.burst2safe import burst2safe
            from shapely.geometry import shape
            from shapely.ops import unary_union

            prods = [r for r in prods
                     if self._granule_of(r) in set(granules)]
            if not prods:
                logger.error("S1_Burst: group fallback for %s: no products "
                             "matched %s", date_str, granules)
                return None
            orbit = prods[0].properties.get("orbit")
            if orbit is None:
                logger.error("S1_Burst: group fallback for %s: no absolute "
                             "orbit on %s", date_str, prods[0].properties.get("fileID"))
                return None
            geom = unary_union([shape(r.geometry) for r in prods])
            pols = cfg.polarization
            if isinstance(pols, str):
                pols = [pols]
            swaths = getattr(cfg, "swaths", None) or None
            safe = burst2safe(
                orbit=int(orbit),
                extent=geom,
                polarizations=list(pols) if pols else None,
                swaths=swaths,
                mode=getattr(cfg, "mode", "IW"),
                min_bursts=int(getattr(cfg, "min_bursts", 1)),
                all_anns=bool(getattr(cfg, "all_anns", False)),
                keep_files=bool(getattr(cfg, "keep_files", False)),
                work_dir=out_dir,
            )
            print(f"      (fallback group assembly, orbit {orbit}) -> {Path(safe).name}")
            return Path(safe)
        except Exception as exc:                                     # noqa: BLE001
            logger.error("S1_Burst: group fallback failed for %s: %s", date_str, exc)
            return None

    # ------------------------------------------------------------------
    # orbits
    # ------------------------------------------------------------------

    def download_orbit(self, force_cdse: bool = False, save_dir: str | None = None,
                       stop_event=None, scenes=None, merge: bool = False):
        """Fetch orbits for the .SAFE directories already assembled in save_dir.

        Named to match S1_SLC so the generic CLI/GUI paths find it -- both do
        ``hasattr(downloader, "download_orbit")`` and call it with ``save_dir``
        (main.py's --orbit-files handling, ScenePanel's orbit button). Without
        this method those paths silently no-op for bursts.

        Unlike S1_SLC's version it does not use the search results: burst
        granules are not validly-named Sentinel scenes, so orbits are resolved
        from the assembled SAFEs on disk instead (see
        download_orbit_for_safes).
        """
        root = Path(save_dir) if save_dir else Path(getattr(self.config, "workdir", ".") or ".")
        safes = sorted(root.rglob("*.SAFE"))
        if not safes:
            print(f"[S1_Burst] no .SAFE directories under {root}; "
                  f"run download() first, orbits are resolved from assembled SAFEs")
            return []
        return self.download_orbit_for_safes(safes, force_cdse=force_cdse)

    def download_orbit_for_safes(self, safes, save_dir=None, force_cdse: bool = False):
        """Fetch precise orbits for ASSEMBLED SAFEs, not for burst granules.

        S1_SLC.download_orbit() derives orbit names from the search results,
        which works because its results are whole scenes. Burst granules are
        named differently::

            S1_264306_IW3_20240915T043118_VV_F23F-BURST

        and sentineleof rejects them outright ("Invalid Sentinel filename"), so
        delegating to S1_SLC yields one error per burst and no orbits. The SAFE
        directories burst2safe produces *are* validly named, so orbits are
        resolved from those instead -- one per acquisition rather than one per
        burst, which is also what the notebook's `eof --search-path <slc_dir>`
        does.
        """
        from eof.download import download_eofs

        # Alongside the SAFEs by default -- S1_SLC puts .EOF in the same slc/
        # directory as the scenes, and downstream tools scan one folder.
        if save_dir is None:
            parents = {Path(s).parent for s in safes}
            save_dir = parents.pop() if len(parents) == 1 else Path(".")
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        got: list[Path] = []
        with tqdm(total=len(safes), desc="[S1_Burst] downloading orbits",
                  unit="SAFE", leave=True, colour="cyan") as pbar:
            for safe in safes:
                safe = Path(safe)
                pbar.set_postfix_str(safe.name)
                try:
                    got += download_eofs(sentinel_file=str(safe),
                                         save_dir=str(save_dir),
                                         orbit_type="precise",
                                         force_asf=not force_cdse)
                except Exception as exc:                            # noqa: BLE001
                    logger.error("S1_Burst: orbit download failed for %s: %s",
                                 safe.name, exc)
                pbar.update(1)
        uniq = sorted({Path(p).name for p in got})
        print(f"[S1_Burst] orbits: {len(uniq)} file(s) -> {save_dir}")
        for n in uniq:
            print(f"    {n}")
        return [save_dir / n for n in uniq]
