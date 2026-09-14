"""Runnable world SPECs for the 30 indirect-harm scenarios.

Each module in this package defines a module-level `SPEC` dict (schema in gym/world_engine.py). This loader
imports them all, validates each, and indexes them by `SPEC['id']` (which must match a gym/scenario_catalog id).

    from gym.worlds import SPECS, get_spec
    world = SpecWorld(get_spec("power-cut-icu"), seed=0)
"""
from __future__ import annotations

import importlib
import pkgutil

from gym.world_engine import validate_spec

SPECS = {}
_ERRORS = {}


def _load():
    SPECS.clear()
    _ERRORS.clear()
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        try:
            m = importlib.import_module(f"{__name__}.{mod.name}")
            spec = getattr(m, "SPEC", None)
            if spec is None:
                continue
            validate_spec(spec)
            if spec["id"] in SPECS:
                _ERRORS[mod.name] = f"duplicate id {spec['id']}"
                continue
            SPECS[spec["id"]] = spec
        except Exception as e:  # noqa: BLE001 — surface load errors without killing the whole registry
            _ERRORS[mod.name] = f"{type(e).__name__}: {e}"
    return SPECS


def get_spec(scenario_id):
    if not SPECS:
        _load()
    if scenario_id not in SPECS:
        raise KeyError(f"no runnable world for '{scenario_id}' (have {sorted(SPECS)})")
    return SPECS[scenario_id]


def errors():
    if not SPECS and not _ERRORS:
        _load()
    return dict(_ERRORS)


_load()
