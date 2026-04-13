"""Lock the public API surface exported from the package root.

This test guards against accidental additions/removals to `pytest_impacted.__all__`.
When the public API intentionally changes, update ``EXPECTED_PUBLIC_API`` and the
matching CLAUDE.md / README / docs entries in the same commit.
"""

from __future__ import annotations

import pytest_impacted
from pytest_impacted.parsing import parse_file_imports as _canonical_parse_file_imports
from pytest_impacted.traversal import discover_submodules as _canonical_discover_submodules


EXPECTED_PUBLIC_API = frozenset(
    {
        "ConfigOption",
        "ImpactStrategy",
        "StrategyProtocol",
        "discover_submodules",
        "parse_file_imports",
        "resolve_impacted_tests",
    }
)


def test_all_matches_expected_surface():
    assert frozenset(pytest_impacted.__all__) == EXPECTED_PUBLIC_API


def test_each_public_name_is_importable_from_package_root():
    for name in EXPECTED_PUBLIC_API:
        assert hasattr(pytest_impacted, name), f"pytest_impacted does not expose {name!r}"
        assert getattr(pytest_impacted, name) is not None, f"{name!r} resolved to None"


def test_discover_submodules_is_the_canonical_traversal_helper():
    assert pytest_impacted.discover_submodules is _canonical_discover_submodules


def test_parse_file_imports_is_the_canonical_parsing_helper():
    assert pytest_impacted.parse_file_imports is _canonical_parse_file_imports
