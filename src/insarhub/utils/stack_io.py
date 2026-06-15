# -*- coding: utf-8 -*-
"""Stack file I/O utilities shared by CLI and GUI.

Centralises write_stack_file() and merge_db_scores_into_stack() so neither
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
        "pair_quality": {"scores": {}, "factors": {}},
    }
    path.write_text(json.dumps(stack_data, indent=2, default=str))
    return stack_data


def merge_db_scores_into_stack(
    stack_path: Path,
    stack_data: dict,
    folder: Path,
    selected_pairs: list,
) -> tuple[dict | None, dict | None]:
    """Read DB scores, filter for selected_pairs, rewrite stack file.

    Returns (quality_scores, quality_factors), both None on failure.
    Caller keeps the return values for e.g. network plotting.
    """
    db_path = folder / _DB_FILE
    try:
        db_data     = json.loads(db_path.read_text())
        all_scores  = db_data.get("scores", {})
        all_factors = db_data.get("factors", {})
        quality_scores:  dict = {}
        quality_factors: dict = {}
        for pair in selected_pairs:
            for k in (f"{pair[0]}:{pair[1]}", f"{pair[1]}:{pair[0]}"):
                if k in all_scores:
                    quality_scores[k]  = all_scores[k]
                    quality_factors[k] = all_factors.get(k, {})
                    break
        stack_data["pair_quality"] = {"scores": quality_scores, "factors": quality_factors}
        stack_path.write_text(json.dumps(stack_data, indent=2, default=str))
        return quality_scores, quality_factors
    except Exception as exc:
        logger.warning("Could not merge DB scores into stack %s: %s", stack_path.name, exc)
        return None, None
