# -*- coding: utf-8 -*-
"""Stack file I/O utilities shared by CLI and GUI.

Centralises write_stack_file() and merge_db_status_into_stack() so neither
the CLI nor the GUI duplicates this logic.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

_DB_FILE = ".insarhub_pair_quality_db.json"

logger = logging.getLogger(__name__)


def write_stack_file(
    path: Path,
    pairs: list,
    baselines: dict,
    scenes: list,
) -> dict:
    """Write stack_p*_f*.json and return the stack_data dict.

    Parameters
    ----------
    path      : destination path, e.g. subdir / "stack_p64_f115.json"
    pairs     : list of (ref, sec) tuples
    baselines : {scene_name: bperp_m} from scene_bperp
    scenes    : list of all scene names in the stack
    """
    stack_data: dict = {
        "pairs":        [list(p) for p in pairs],
        "baselines":    {k: float(v) for k, v in baselines.items()},
        "scenes":       scenes,
        "pair_quality": {"status": {}, "factors": {}},
    }
    path.write_text(json.dumps(stack_data, indent=2, default=str))
    return stack_data


def merge_db_status_into_stack(
    stack_path: Path,
    stack_data: dict,
    folder: Path,
    selected_pairs: list,
) -> tuple[dict | None, dict | None]:
    """Read the DB verdicts, filter for selected_pairs, rewrite the stack file.

    Returns (pair_status, quality_factors), both None on failure.
    Caller keeps the return values for e.g. network plotting.
    """
    db_path = folder / _DB_FILE
    try:
        db_data     = json.loads(db_path.read_text())
        all_status  = db_data.get("status", {})
        all_factors = db_data.get("factors", {})
        pair_status:     dict = {}
        quality_factors: dict = {}
        for pair in selected_pairs:
            for k in (f"{pair[0]}:{pair[1]}", f"{pair[1]}:{pair[0]}"):
                if k in all_status:
                    pair_status[k]     = all_status[k]
                    quality_factors[k] = all_factors.get(k, {})
                    break
        stack_data["pair_quality"] = {"status": pair_status, "factors": quality_factors}
        stack_path.write_text(json.dumps(stack_data, indent=2, default=str))
        return pair_status, quality_factors
    except Exception as exc:
        logger.warning("Could not merge DB scores into stack %s: %s", stack_path.name, exc)
        return None, None


def finalize_stack(
    subdir: Path,
    stack_path: Path,
    pairs: list,
    scene_bperp: dict,
    stack_scenes: list,
    *,
    key: tuple,
    baselines: dict,
    title: str,
    save_path: Path | str | None,
    quality_check: bool = True,
    plot_network: bool = True,
) -> tuple[dict | None, dict | None]:
    """Write the stack file, then (optionally) judge every pair and plot.

    The shared "finalize" sequence (stack file -> PairQualityDB -> merge ->
    plot) used by the downloader's ``select_pairs()``, the CLI, and the GUI
    folder route, so the ordering never drifts between entry points.

    Returns ``(pair_status, quality_factors)`` — both ``None`` when
    ``quality_check`` is False, or when the judging step fails.
    """
    from insarhub.utils.pair_quality._db import PairQualityDB
    from insarhub.utils.tool import plot_pair_network

    stack_data = write_stack_file(stack_path, pairs, scene_bperp, stack_scenes)

    pair_status = quality_factors = None
    if quality_check:
        try:
            PairQualityDB(subdir).build(
                {key: stack_scenes},
                {key: {k: float(v) for k, v in scene_bperp.items()}},
            )
            pair_status, quality_factors = merge_db_status_into_stack(
                stack_path, stack_data, subdir, pairs
            )
        except Exception as exc:
            logger.warning("Pair quality failed for %s: %s — plotting without it",
                           stack_path.name, exc)

    if plot_network:
        plot_pair_network(
            pairs, baselines,
            scene_baselines=scene_bperp,
            title=title,
            save_path=save_path,
            pair_status=pair_status,
            quality_factors=quality_factors,
        )

    return pair_status, quality_factors
