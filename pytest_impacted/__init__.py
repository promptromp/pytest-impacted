"""pytest-impacted: selectively run tests impacted by code changes.

The public names, and the submodules, load on first use (PEP 562): the plugin is a pytest11 entry point,
imported by every pytest run, with --impacted or without, and ``import pytest_impacted.plugin`` imports this
package first. Eager imports here made every pytest run load networkx, astroid and GitPython.
"""

import importlib as _importlib
import typing as _typing


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

#: The module each public name lives in (``tests/test_public_api.py`` holds it to ``__all__``).
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


def __getattr__(name: str) -> _typing.Any:
    """A public name, or a submodule (``pytest_impacted.strategies`` after a bare ``import pytest_impacted``)."""
    if name in _EXPORTS:
        value = getattr(_importlib.import_module(_EXPORTS[name]), name)
    else:
        try:
            value = _importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as err:
            if err.name != f"{__name__}.{name}":
                raise  # the submodule exists, and something it imports does not
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value  # once: later lookups find it directly
    return value


def __dir__() -> list[str]:
    return sorted({*__all__, *(name for name in globals() if name.startswith("__"))})


if _typing.TYPE_CHECKING:  # for type checkers and editors, which do not run __getattr__
    from pytest_impacted.extensions import ConfigOption as ConfigOption, StrategyProtocol as StrategyProtocol
    from pytest_impacted.graph import resolve_impacted_tests as resolve_impacted_tests
    from pytest_impacted.parsing import parse_file_imports as parse_file_imports
    from pytest_impacted.strategies import ImpactStrategy as ImpactStrategy
    from pytest_impacted.traversal import (
        ProjectModules as ProjectModules,
        discover_project_modules as discover_project_modules,
        discover_submodules as discover_submodules,
    )
