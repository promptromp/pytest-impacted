"""Impact analysis strategies."""

from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

import networkx as nx

from pytest_impacted.graph import build_dep_tree, resolve_impacted_tests
from pytest_impacted.parsing import is_test_module, normalize_path
from pytest_impacted.traversal import discover_submodules


# Default dependency file basenames that trigger all tests when changed
DEFAULT_DEPENDENCY_FILE_PATTERNS: tuple[str, ...] = (
    "uv.lock",
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "setup.py",
    "setup.cfg",
)

# Glob-style patterns for matching nested dependency files (e.g. requirements/*.txt)
DEFAULT_DEPENDENCY_GLOB_PATTERNS: tuple[str, ...] = (
    "requirements/*.txt",
    "requirements/**/*.txt",
)


def _matches_dependency_file(
    file_path: str,
    patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_FILE_PATTERNS,
    glob_patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_GLOB_PATTERNS,
) -> bool:
    """Check if a file path matches any dependency file pattern."""
    basename = PurePosixPath(file_path).name
    if basename in patterns:
        return True
    return any(PurePosixPath(file_path).match(glob_pat) for glob_pat in glob_patterns)


def has_dependency_file_changes(
    changed_files: list[str],
    patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_FILE_PATTERNS,
    glob_patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_GLOB_PATTERNS,
) -> bool:
    """Check if any changed files are dependency/configuration files."""
    return any(_matches_dependency_file(f, patterns, glob_patterns) for f in changed_files)


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
    after code changes during development. Also clears discovery caches
    since stale submodule data would produce stale dependency trees.
    """
    _cached_build_dep_tree.cache_clear()
    discover_submodules.cache_clear()


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

        impacted_tests: list[str] = []

        for conftest_file in conftest_files:
            try:
                conftest_path = normalize_path(conftest_file)

                if not conftest_path.is_absolute():
                    conftest_path = normalize_path(root_dir) / conftest_path

                conftest_dir = conftest_path.parent
            except ValueError:
                # Skip files that can't be normalized to valid paths
                continue

            # Find all test modules in subdirectories that could be affected
            impacted_tests.extend(
                test_module
                for test_module in dep_tree.nodes
                if is_test_module(test_module)
                and self._is_test_affected_by_conftest(test_module, conftest_dir, root_dir)
            )

        return impacted_tests

    def _is_test_affected_by_conftest(self, test_module: str, conftest_dir: Path, root_dir: Path) -> bool:
        """Check if a test module is affected by a conftest.py change."""
        # Convert module name to file path
        module_parts = test_module.split(".")

        # Try to find the actual file path for this test module
        module_path = "/".join(module_parts)
        root_path = normalize_path(root_dir)
        possible_paths = [
            root_path / (module_path + ".py"),
            root_path / module_path / "__init__.py",
        ]

        # Check if any of the possible paths exist and are affected by conftest
        for path in possible_paths:
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


class DependencyFileImpactStrategy(ImpactStrategy):
    """Strategy that triggers all tests when dependency files change.

    When files like uv.lock, requirements.txt, pyproject.toml etc. are
    modified, any test could potentially be affected. This strategy
    conservatively marks all discovered test modules as impacted.
    """

    def __init__(
        self,
        patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_FILE_PATTERNS,
        glob_patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_GLOB_PATTERNS,
    ):
        self.patterns = patterns
        self.glob_patterns = glob_patterns

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
    ) -> list[str]:
        """Return all test modules if dependency files have changed."""
        if not has_dependency_file_changes(changed_files, self.patterns, self.glob_patterns):
            return []

        # Build the dep tree to discover all test modules
        dep_tree = _cached_build_dep_tree(ns_module, tests_package=tests_package)
        all_test_modules = sorted(node for node in dep_tree.nodes if is_test_module(node))

        dep_files = [f for f in changed_files if _matches_dependency_file(f, self.patterns, self.glob_patterns)]
        from pytest_impacted.display import notify  # noqa: PLC0415

        notify(
            f"Dependency file changes detected: {dep_files}. "
            f"Marking all {len(all_test_modules)} test modules as impacted.",
            session,
        )

        return all_test_modules


def get_default_strategies(*, watch_dep_files: bool = True) -> list[ImpactStrategy]:
    """Return the default strategy list for impact analysis.

    This centralizes the knowledge of which strategies form the default
    pipeline. Add new strategies here rather than in api.py.
    """
    strategies: list[ImpactStrategy] = [
        ASTImpactStrategy(),
        PytestImpactStrategy(),
    ]
    if watch_dep_files:
        strategies.append(DependencyFileImpactStrategy())
    return strategies


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
