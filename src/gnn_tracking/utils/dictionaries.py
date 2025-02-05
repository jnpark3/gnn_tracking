from __future__ import annotations

import itertools
from copy import deepcopy
from typing import Any, Sequence, TypeVar

_P = TypeVar("_P")


def add_key_prefix(dct: dict[str, _P], prefix: str = "") -> dict[str, _P]:
    """Return a copy of the dictionary with the prefix added to all keys."""
    return {f"{prefix}{k}": v for k, v in dct.items()}


def add_key_suffix(dct: dict[str, _P], suffix: str = "") -> dict[str, _P]:
    """Return a copy of the dictionary with the suffix added to all keys."""
    return {f"{k}{suffix}": v for k, v in dct.items()}


def subdict_with_prefix_stripped(dct: dict[str, _P], prefix: str = "") -> dict[str, _P]:
    """Return a copy of the dictionary for all keys that start with prefix
    and with the prefix removed from all keys."""
    return {k[len(prefix) :]: v for k, v in dct.items() if k.startswith(prefix)}


def expand_grid(
    grid: dict[str, Sequence], fixed: dict[str, Sequence] = None
) -> list[dict[str, Any]]:
    """Expands a grid of parameters into a list of configurations."""
    if fixed is None:
        fixed = {}
    _configs = list(itertools.product(*grid.values()))
    configs: list[dict[str, Any]] = []
    for _c in _configs:
        c = deepcopy(fixed)
        c.update({k: v for k, v in zip(grid.keys(), _c)})
        configs.append(c)
    return configs
