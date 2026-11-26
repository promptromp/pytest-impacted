"""pytest-impacted: selectively run tests impacted by code changes.

The public names load on first use (PEP 562): the plugin is a pytest11 entry point, imported by every pytest
run, with --impacted or without, and ``import pytest_impacted.plugin`` imports this package first. Eager
imports here made every pytest run load networkx, astroid and GitPython.
"""

import importlib
from typing import TYPE_CHECKING, Any


#: Each public name, and the module it lives in.
_EXPORTS = {
    "ConfigOption": "pytest_impacted.extensions",
    "ImpactStrategy": "pytest_impacted.strategies",
    "ProjectModules": "pytest_impacted.traversal",
    "StrategyProtocol": "pytest_impacted.extensions",
    "discover_project_modules": "pytest_impacted.traversal",
    "discover_submodules": "pytest_impacted.traversal",
    "parse_file_imports": "pytest_impacted.parsing",
    "resolve_impacted_tests": "pytest_impacted.graph",
}

__all__ = [
    "ConfigOption",
    "ImpactStrategy",
    "ProjectModules",
    "StrategyProtocol",
    "discover_project_modules",
    "discover_submodules",
    "parse_file_imports",
    "resolve_impacted_tests",
]


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(_EXPORTS[name]), name)
    globals()[name] = value  # once: later lookups find it directly
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})


if TYPE_CHECKING:  # for type checkers and editors, which do not run __getattr__
    from pytest_impacted.extensions import ConfigOption as ConfigOption, StrategyProtocol as StrategyProtocol
    from pytest_impacted.graph import resolve_impacted_tests as resolve_impacted_tests
    from pytest_impacted.parsing import parse_file_imports as parse_file_imports
    from pytest_impacted.strategies import ImpactStrategy as ImpactStrategy
    from pytest_impacted.traversal import (
        ProjectModules as ProjectModules,
        discover_project_modules as discover_project_modules,
        discover_submodules as discover_submodules,
    )
