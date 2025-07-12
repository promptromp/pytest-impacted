"""Impact analysis strategies."""

from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any

import networkx as nx

from pytest_impacted.graph import build_dep_tree, resolve_impacted_tests
from pytest_impacted.parsing import is_test_module


@lru_cache(maxsize=8)
def _cached_build_dep_tree(ns_module: str, tests_package: str | None = None) -> nx.DiGraph:
    """Cached version of build_dep_tree to avoid redundant graph construction.

    Args:
        ns_module: The namespace module being analyzed
        tests_package: Optional tests package name

    Returns:
        NetworkX dependency graph

    Note:
        Using LRU cache with maxsize=8 to cache recent dependency trees while
        preventing unbounded memory growth. This optimizes the common case where
        the same ns_module/tests_package combination is used repeatedly within
        a single pytest run.
    """
    return build_dep_tree(ns_module, tests_package=tests_package)


def clear_dep_tree_cache() -> None:
    """Clear the dependency tree cache.

    This is useful for testing or when you want to ensure fresh analysis
    after code changes during development.
    """
    _cached_build_dep_tree.cache_clear()


class ImpactStrategy(ABC):
    """Abstract base class for impact analysis strategies."""

    @abstractmethod
    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
    ) -> list[str]:
        """Find test modules impacted by the given changed files and modules.

        Args:
            changed_files: List of file paths that have changed
            impacted_modules: List of Python modules corresponding to changed files
            ns_module: The namespace module being analyzed
            tests_package: Optional tests package name
            root_dir: Root directory of the repository
            session: Optional pytest session object

        Returns:
            List of impacted test module names
        """
        pass


class ASTImpactStrategy(ImpactStrategy):
    """Strategy that uses AST parsing and dependency graph analysis."""

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
    ) -> list[str]:
        """Find impacted tests using AST dependency graph analysis."""
        dep_tree = _cached_build_dep_tree(ns_module, tests_package=tests_package)
        return resolve_impacted_tests(impacted_modules, dep_tree)


class PytestImpactStrategy(ImpactStrategy):
    """Strategy that handles pytest-specific dependencies like conftest.py files."""

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
    ) -> list[str]:
        """Find impacted tests including pytest-specific dependencies."""
        # Start with AST-based analysis
        dep_tree = _cached_build_dep_tree(ns_module, tests_package=tests_package)
        impacted_tests = resolve_impacted_tests(impacted_modules, dep_tree)

        # Add conftest.py impact analysis
        conftest_impacted_tests = self._find_conftest_impacted_tests(changed_files, root_dir, dep_tree)

        # Combine and deduplicate
        all_impacted = list(set(impacted_tests + conftest_impacted_tests))
        return sorted(all_impacted)

    def _find_conftest_impacted_tests(
        self, changed_files: list[str], root_dir: Path | None, dep_tree: nx.DiGraph
    ) -> list[str]:
        """Find tests impacted by conftest.py changes."""
        if not root_dir:
            return []

        conftest_files = [f for f in changed_files if f.endswith("conftest.py")]
        if not conftest_files:
            return []

        impacted_tests = []

        for conftest_file in conftest_files:
            # Handle different path types - ensure we get a proper pathlib.Path
            try:
                if hasattr(conftest_file, "strpath"):
                    # This is likely a py.path.local.LocalPath object
                    conftest_path = Path(conftest_file.strpath)
                elif hasattr(conftest_file, "__fspath__"):
                    # This has a filesystem path protocol
                    conftest_path = Path(conftest_file.__fspath__())
                else:
                    # Fallback to string conversion
                    conftest_path = Path(str(conftest_file))

                if not conftest_path.is_absolute():
                    conftest_path = Path(root_dir) / conftest_path

                # Ensure conftest_path is definitely a pathlib.Path
                conftest_path = Path(conftest_path)

                # Find the directory containing the conftest.py
                conftest_dir = conftest_path.parent
            except Exception:
                # If all else fails, skip this conftest file
                continue

            # Find all test modules in subdirectories that could be affected
            for test_module in dep_tree.nodes:
                if is_test_module(test_module):
                    # Check if this test module is in a subdirectory of the conftest.py
                    if self._is_test_affected_by_conftest(test_module, conftest_dir, root_dir):
                        impacted_tests.append(test_module)

        return impacted_tests

    def _is_test_affected_by_conftest(self, test_module: str, conftest_dir: Path, root_dir: Path) -> bool:
        """Check if a test module is affected by a conftest.py change."""
        # Convert module name to file path
        module_parts = test_module.split(".")

        # Try to find the actual file path for this test module
        module_path = "/".join(module_parts)
        # Ensure root_dir is a proper Path object
        root_path = Path(root_dir)
        possible_paths = [
            root_path / (module_path + ".py"),
            root_path / module_path / "__init__.py",
        ]

        # If tests_package is separate, also check there
        for path in possible_paths:
            # Ensure path is also a proper Path object
            path = Path(path)
            if path.exists():
                try:
                    # Check if the test file is in the same directory or a subdirectory
                    # of where the conftest.py was changed
                    path.resolve().relative_to(conftest_dir.resolve())
                    return True
                except ValueError:
                    # path is not relative to conftest_dir
                    continue

        return False


class CompositeImpactStrategy(ImpactStrategy):
    """Strategy that combines multiple strategies."""

    def __init__(self, strategies: list[ImpactStrategy]):
        """Initialize with a list of strategies to apply."""
        self.strategies = strategies

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
    ) -> list[str]:
        """Find impacted tests by applying all strategies and combining results."""
        all_impacted = []

        for strategy in self.strategies:
            strategy_results = strategy.find_impacted_tests(
                changed_files=changed_files,
                impacted_modules=impacted_modules,
                ns_module=ns_module,
                tests_package=tests_package,
                root_dir=root_dir,
                session=session,
            )
            all_impacted.extend(strategy_results)

        # Remove duplicates and sort
        return sorted(list(set(all_impacted)))
