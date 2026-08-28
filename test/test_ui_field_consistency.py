"""Guard against config drift: every key in a config's ``_ui_fields`` (the UI
metadata the GUI/CLI render) must map to a real dataclass field, or the GUI can
show a control that the config never stores (the "empty field in the GUI" bug).

Only CONCRETE configs -- the ``default_config`` of a registered downloader /
processor / analyzer -- are checked. Abstract bases (e.g. ``GMTSAR_Base_Config``)
legitimately forward-reference fields their subclasses add, so they are not
instantiated directly and not checked here.
"""
import dataclasses

import pytest

from insarhub import Analyzer, Downloader, Processor


def _concrete_configs():
    out = {}
    for reg in (Downloader, Processor, Analyzer):
        for name, cls in getattr(reg, "_registry", {}).items():
            cfg = getattr(cls, "default_config", None)
            if cfg and dataclasses.is_dataclass(cfg) and hasattr(cfg, "_ui_fields"):
                out.setdefault(f"{name}:{cfg.__name__}", cfg)
    return out


_CONFIGS = _concrete_configs()


def test_registry_has_configs():
    # Sanity: the registries are populated (import side effects ran).
    assert _CONFIGS, "no registered configs with _ui_fields found"


@pytest.mark.parametrize("cfg", _CONFIGS.values(), ids=list(_CONFIGS))
def test_ui_fields_reference_real_dataclass_fields(cfg):
    fields = {f.name for f in dataclasses.fields(cfg)}
    extra = set(cfg._ui_fields) - fields
    assert not extra, (
        f"{cfg.__name__}._ui_fields references field(s) that are not on the "
        f"dataclass: {sorted(extra)}. Add them as dataclass fields, or remove "
        f"them from _ui_fields."
    )
