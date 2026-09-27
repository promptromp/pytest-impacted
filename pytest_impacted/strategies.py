"""Impact analysis strategies."""

from __future__ import annotations
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from copy import deepcopy
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

import networkx as nx

from pytest_impacted.display import notify
from pytest_impacted.extensions import ConfigOption, StrategyProtocol
from pytest_impacted.graph import build_dep_tree, resolve_impacted_tests
from pytest_impacted.parsing import is_conftest_module, is_test_module, normalize_path
from pytest_impacted.traversal import canonical_root, clear_discovery_cache, discover_application_files


logger = logging.getLogger(__name__)


# Default dependency and configuration file basenames that trigger all tests when changed.
# Every file pytest reads its settings from is here too: addopts, markers and
# filterwarnings apply to every test. (The file pytest actually loaded — including one
# passed with ``-c`` — is matched as well; see DependencyFileImpactStrategy.)
DEFAULT_DEPENDENCY_FILE_PATTERNS: tuple[str, ...] = (
    # Lockfiles and project metadata. requirements.txt is also matched by a glob below,
    # but stays here so callers passing their own glob_patterns keep it.
    "uv.lock",
    "requirements.txt",
    "poetry.lock",
    "pdm.lock",
    "pixi.lock",
    "Pipfile",
    "Pipfile.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    # pytest configuration (pytest.toml and .pytest.toml since pytest 9)
    "pytest.ini",
    ".pytest.ini",
    "pytest.toml",
    ".pytest.toml",
    "tox.ini",
)

# Glob-style patterns (see matches_any_glob: right-anchored, so a bare-name glob matches
# at any depth, while ``**`` is *not* recursive) for files named by convention.
DEFAULT_DEPENDENCY_GLOB_PATTERNS: tuple[str, ...] = (
    "*requirements*.txt",  # requirements.txt, requirements-dev.txt, test-requirements.txt, ...
    "*requirements*.in",  # pip-tools inputs
    "requirements*.lock",  # rye
    "*constraints*.txt",  # constraints.txt, test-constraints.txt, ...
    "pylock*.toml",  # PEP 751
    "requirements/*.txt",
    "requirements/**/*.txt",
    "requirements/*.in",
    "requirements/**/*.in",
)


def matches_any_glob(file_path: str, glob_patterns: Iterable[str]) -> bool:
    """Check if a repo-relative file path matches any glob pattern.

    Matching is right-anchored (:meth:`PurePosixPath.match`): ``*.json``
    matches a JSON file at any depth, ``config/*.json`` matches JSON files
    directly under any ``config`` directory, and a bare filename matches
    that basename anywhere. This is the single matching convention for both
    built-in and user-supplied file patterns.
    """
    path = PurePosixPath(file_path)
    return any(path.match(glob_pat) for glob_pat in glob_patterns)


def matches_dependency_file(
    file_path: str,
    patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_FILE_PATTERNS,
    glob_patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_GLOB_PATTERNS,
) -> bool:
    """Check if a file path matches any dependency file pattern."""
    basename = PurePosixPath(file_path).name
    if basename in patterns:
        return True
    return matches_any_glob(file_path, glob_patterns)


def has_dependency_file_changes(
    changed_files: list[str],
    patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_FILE_PATTERNS,
    glob_patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_GLOB_PATTERNS,
) -> bool:
    """Check if any changed files match the dependency/configuration file *name* patterns.

    Name patterns only: :class:`DependencyFileImpactStrategy` also counts the
    config file the running pytest loaded, which a name cannot identify.
    """
    return any(matches_dependency_file(f, patterns, glob_patterns) for f in changed_files)


@lru_cache(maxsize=8)
def _cached_build_dep_tree(ns_module: str, tests_package: str | None, root: Path) -> nx.DiGraph:
    """Cached graph construction, keyed on the canonical *root*.

    Note:
        maxsize=8 keeps recent dependency trees without unbounded growth. The
        common case is the same ns_module/tests_package/root used repeatedly
        across runs in one process (in-process ``pytest.main``, pytester).
    """
    return build_dep_tree(ns_module, tests_package=tests_package, root_dir=root)


def cached_build_dep_tree(
    ns_module: str, tests_package: str | None = None, root_dir: str | Path | None = None
) -> nx.DiGraph:
    """Cached version of build_dep_tree to avoid redundant graph construction.

    Args:
        ns_module: The namespace module being analyzed
        tests_package: Optional tests package name
        root_dir: Project root the package paths are relative to; defaults to
            the current directory.

    Returns:
        NetworkX dependency graph. Do not mutate it — it is shared between
        runs; callers take a :func:`run_copy`.

    Note:
        The root is canonicalized *before* the cache lookup, so the default
        never collapses two projects onto one entry the way a bare ``None``
        key would.
    """
    return _cached_build_dep_tree(ns_module, tests_package, canonical_root(root_dir))


def run_copy(dep_tree: nx.DiGraph) -> nx.DiGraph:
    """A copy of *dep_tree* that a run may mutate without touching the cached graph.

    ``DiGraph.copy()`` copies nodes, edges and their attribute dicts, but shares
    graph-level values such as ``graph["aliases"]``, so those are copied too.
    """
    copy = dep_tree.copy()
    copy.graph = deepcopy(dep_tree.graph)
    return copy


def clear_dep_tree_cache() -> None:
    """Clear the dependency tree cache.

    This is useful for testing or when you want to ensure fresh analysis
    after code changes during development. Also clears discovery caches
    since stale submodule data would produce stale dependency trees.
    """
    _cached_build_dep_tree.cache_clear()
    clear_discovery_cache()


def _loaded_config_file(session: Any) -> Path | None:
    """The config file this pytest run loaded (``config.inipath``), or ``None``.

    ``None`` too when pytest found no config file, or when there is no pytest
    session at all (the standalone CLI).
    """
    inipath = getattr(getattr(session, "config", None), "inipath", None)
    return Path(inipath).resolve() if isinstance(inipath, str | os.PathLike) else None


def _resolve_changed_file(changed_file: str, root_dir: Path | None) -> Path | None:
    """Absolute, resolved path of a changed file; ``None`` if it is relative and there is no root."""
    path = Path(changed_file)
    if not path.is_absolute():
        if root_dir is None:
            return None
        path = Path(root_dir) / path
    return path.resolve()


def _resolve_changed_file_dir(changed_file: str, root_dir: Path) -> Path | None:
    """Return the absolute directory containing *changed_file*, or None if unresolvable."""
    path = _resolve_changed_file(changed_file, root_dir)
    return path.parent if path is not None else None


def _test_module_path(test_module: str, root_dir: Path) -> Path | None:
    """Locate the source file for a dotted module name under *root_dir* (fallback for nodes without a ``path``)."""
    module_path = "/".join(test_module.split("."))
    root_path = normalize_path(root_dir)
    for candidate in (root_path / (module_path + ".py"), root_path / module_path / "__init__.py"):
        # os.path, not Path.exists(): a file in an unsearchable directory is missing, rather than raising.
        if os.path.isfile(candidate):
            return candidate
    return None


def _module_path(module: str, dep_tree: nx.DiGraph, root_dir: Path) -> Path | None:
    """The source file of graph node *module*.

    Prefers the ``path`` recorded by :func:`~pytest_impacted.graph.build_dep_tree`,
    which is right for src-layout too; nodes added by extensions may lack it.
    """
    path = dep_tree.nodes[module].get("path")
    return Path(path) if path else _test_module_path(module, root_dir)


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def find_test_modules_under(directory: Path, dep_tree: nx.DiGraph, *, root_dir: Path) -> list[str]:
    """Return the sorted test modules whose files live in *directory* or any subdirectory.

    This is the "same directory and below" impact rule used for every conftest that
    was edited or imports changed test code (:class:`PytestImpactStrategy`) and, when
    opted in, every conftest that imports changed application code too
    (:class:`ConftestImportImpactStrategy`).
    """
    matches = []
    for test_module in dep_tree.nodes:
        if not is_test_module(test_module):
            continue
        path = _module_path(test_module, dep_tree, root_dir)
        if path is not None and _is_under(path, directory):
            matches.append(test_module)
    return sorted(matches)


def _outermost(directories: set[Path]) -> list[Path]:
    """Drop every directory nested inside another one: its tests are already covered."""
    resolved = sorted({directory.resolve() for directory in directories}, key=lambda d: len(d.parts))
    kept: list[Path] = []
    for directory in resolved:
        if not any(directory.is_relative_to(outer) for outer in kept):
            kept.append(directory)
    return kept


def _reached(impacted_modules: list[str], dep_tree: nx.DiGraph) -> set[str]:
    """Every node that depends, directly or transitively, on *impacted_modules* (sources included).

    One multi-source traversal, so a large changeset does not re-walk shared descendants.
    """
    sources = [module for module in impacted_modules if module in dep_tree]
    return set().union(*nx.bfs_layers(dep_tree, sources))


def _conftest_dirs(nodes: Iterable[str], dep_tree: nx.DiGraph, root_dir: Path) -> set[Path]:
    """Directories of the conftests among *nodes*."""
    return {
        path.parent
        for node in nodes
        # The name is a cheap pre-filter; the file name decides, as for changed
        # files, so a package named ``conftest`` is not one.
        if is_conftest_module(node)
        if (path := _module_path(node, dep_tree, root_dir)) is not None and path.name == "conftest.py"
    }


def _changed_conftest_dirs(changed_files: list[str], root_dir: Path) -> set[Path]:
    """Directories of the changed files named exactly ``conftest.py`` (the only name pytest loads)."""
    return {
        conftest_dir
        for changed_file in changed_files
        if PurePosixPath(changed_file).name == "conftest.py"
        # None: the path could not be normalized
        if (conftest_dir := _resolve_changed_file_dir(changed_file, root_dir)) is not None
    }


def _tests_under_conftests(conftest_dirs: set[Path], dep_tree: nx.DiGraph, root_dir: Path) -> list[str]:
    """The test modules in each conftest directory and below, nested directories collapsed first."""
    return [
        test_module
        for conftest_dir in _outermost(conftest_dirs)
        for test_module in find_test_modules_under(conftest_dir, dep_tree, root_dir=root_dir)
    ]


class _CodeRoles:
    """Tells application code (the code under test) from test code.

    Application code is what :func:`~pytest_impacted.traversal.discover_application_files`
    finds — the ``--impacted-module`` walk, less the ``--impacted-tests-dir`` walk — and
    is never a conftest. Everything else in the graph is test code.
    """

    def __init__(self, *, ns_module: str, tests_package: str | None, root_dir: Path):
        self.application = discover_application_files(ns_module, tests_package, root_dir=root_dir)

    def is_application_code(self, path: Path) -> bool:
        return path.name != "conftest.py" and str(path) in self.application


def _relative(path: Path, root_dir: Path) -> str:
    """*path* relative to the project root, for messages."""
    root = canonical_root(root_dir)
    return path.resolve().relative_to(root).as_posix() if _is_under(path, root) else str(path)


def _every_test(dep_tree: nx.DiGraph, reason: str, session: Any) -> list[str]:
    """All test modules, announcing *reason* — the answer when a change can reach any test."""
    all_test_modules = sorted(node for node in dep_tree.nodes if is_test_module(node))
    notify(f"{reason}. Marking all {len(all_test_modules)} test modules as impacted.", session)
    return all_test_modules


def _session_wide_changes(reached: set[str], dep_tree: nx.DiGraph, session: Any) -> list[str]:
    """Reached modules that pytest loads as plugins, whose fixtures and hooks reach every test.

    - a ``pytest_plugins`` target (flagged in the graph) — reached when it, or anything it imports, changed
    - a plugin loaded with ``-p`` (command line or ``addopts``) or ``PYTEST_PLUGINS``
    """
    aliases = dep_tree.graph.get("aliases", {})
    session_plugins = {aliases.get(name, name) for name in _session_plugins(session)}
    return sorted({node for node in reached if dep_tree.nodes[node].get("pytest_plugin")} | (session_plugins & reached))


def _session_plugins(session: Any) -> set[str]:
    """Modules loaded with ``-p`` or ``PYTEST_PLUGINS``; ``-p no:name`` disables one and loads nothing."""
    names = getattr(getattr(getattr(session, "config", None), "option", None), "plugins", None)
    from_options = [name for name in names if isinstance(name, str)] if isinstance(names, list) else []
    from_env = os.environ.get("PYTEST_PLUGINS", "").split(",")
    return {name.strip() for name in [*from_options, *from_env] if name.strip() and not name.startswith("no:")}


class ImpactStrategy(ABC):
    """Abstract base class for impact analysis strategies.

    Third-party extensions can subclass this and register via entry points.
    See :mod:`pytest_impacted.extensions` for the plugin system.

    Class-level attributes for extensions:
        config_options: Declare configuration options the strategy accepts.
        priority: Ordering weight (lower = runs earlier, default = 100).

    Lifecycle:
        :meth:`enrich_dep_tree` runs first, once per pytest run, on a per-run
        copy of the dependency graph. It is the only hook permitted to mutate
        the graph.

        :meth:`setup` runs once per pytest run, before any
        :meth:`find_impacted_tests` call. Strategies can use it to build
        expensive indices, read config files, or warm caches — work that
        would otherwise have to live in a lazy-init guard inside
        :meth:`find_impacted_tests`.

        :meth:`teardown` runs once per pytest run, after all
        :meth:`find_impacted_tests` calls have completed. It fires even if
        :meth:`find_impacted_tests` raises. Strategies can use it to release
        resources or reset per-run state.

        All three hooks have no-op default implementations, so existing
        strategies need no changes to adopt the lifecycle.
    """

    config_options: ClassVar[list[ConfigOption]] = []
    priority: ClassVar[int] = 100

    def enrich_dep_tree(  # noqa: B027  — intentional no-op default
        self,
        dep_tree: nx.DiGraph,
        *,
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
    ) -> None:
        """Add nodes or edges to the dependency graph before impact analysis runs.

        Called exactly once per :func:`~pytest_impacted.api.get_impacted_tests`
        invocation, **before** :meth:`setup` and any :meth:`find_impacted_tests`
        call, on a per-run copy of the graph (the cached base graph is never
        mutated). Strategies use this hook to add synthetic edges that
        represent relationships not visible to static import analysis —
        e.g. dependency-injection bindings, codegen outputs, plugin
        discovery, config-driven wiring.

        The hook receives the same context kwargs as :meth:`setup`
        (``ns_module``, ``tests_package``, ``root_dir``, ``session``) so
        that scan-based enrichers can walk the source tree with
        :func:`~pytest_impacted.traversal.discover_project_modules` (the
        graph's own names, its aliases and the conftests above the packages
        included) and :func:`~pytest_impacted.parsing.parse_file_imports`
        before deciding which edges to add.

        Once all strategies have enriched the graph, the final graph is
        passed by reference to every :meth:`setup` and :meth:`find_impacted_tests`
        call. This means edges added by one strategy are visible to every
        later strategy in the pipeline — including the built-in AST
        strategy, which will traverse them normally.

        Default implementation is a no-op. Override to mutate *dep_tree*
        in place with :meth:`networkx.DiGraph.add_edge` and similar.

        Args:
            dep_tree: The per-run dependency graph, mutable. A :func:`run_copy`
                of the LRU-cached base graph produced by
                :func:`~pytest_impacted.strategies.cached_build_dep_tree`, so
                mutations do not persist across pytest runs.
            ns_module: The namespace module being analyzed.
            tests_package: Optional tests package name.
            root_dir: Project root (the pytest rootdir); may be below the git root.
            session: Optional pytest session object.
        """

    def setup(  # noqa: B027  — intentional no-op default; subclasses override if needed
        self,
        *,
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
        dep_tree: nx.DiGraph,
    ) -> None:
        """Prepare the strategy for a pytest run.

        Called exactly once per :func:`~pytest_impacted.api.get_impacted_tests`
        invocation, before any :meth:`find_impacted_tests` call. Default
        implementation is a no-op — override when you need to build per-run
        indices or warm caches. Receives the same context kwargs as
        :meth:`find_impacted_tests` except ``changed_files`` /
        ``impacted_modules``, which are not known at setup time.

        Args:
            ns_module: The namespace module being analyzed.
            tests_package: Optional tests package name.
            root_dir: Project root (the pytest rootdir); may be below the git root.
            session: Optional pytest session object.
            dep_tree: The pre-built dependency graph for this run. Safe to
                inspect; do not mutate here — :meth:`enrich_dep_tree`, which
                runs before :meth:`setup`, is the sanctioned mutation hook.
        """

    def teardown(self) -> None:  # noqa: B027  — intentional no-op default
        """Release any state built during :meth:`setup`.

        Called exactly once per run after all :meth:`find_impacted_tests`
        calls have completed, even if one of them raises. Default
        implementation is a no-op.
        """

    @abstractmethod
    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
        *,
        dep_tree: nx.DiGraph,
    ) -> list[str]:
        """Find test modules impacted by the given changed files and modules.

        Args:
            changed_files: List of file paths that have changed
            impacted_modules: The ``dep_tree`` nodes the changed ``.py`` files resolve to,
                by node ``path``, before enrichment; conftests above the packages included
            ns_module: The namespace module being analyzed
            tests_package: Optional tests package name
            root_dir: Project root (the pytest rootdir); may be below the git root
            session: Optional pytest session object
            dep_tree: Pre-built dependency graph (NetworkX DiGraph). Built once
                per run by :func:`~pytest_impacted.api.get_impacted_tests` (via
                :func:`cached_build_dep_tree`) and shared across all strategies
                in the pipeline. Use :func:`~pytest_impacted.graph.resolve_impacted_tests`
                for standard graph traversal.

        Returns:
            List of impacted test module names
        """


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
        *,
        dep_tree: nx.DiGraph,
    ) -> list[str]:
        """Find impacted tests using AST dependency graph analysis."""
        return resolve_impacted_tests(impacted_modules, dep_tree)


class PytestImpactStrategy(ImpactStrategy):
    """Strategy that handles pytest-specific dependencies like conftest.py files.

    A conftest impacts every test in its directory and below when it is edited, or
    when it imports changed *test code* (another conftest, a fixture module in the tests
    dir). A conftest importing changed *application* code is
    :class:`ConftestImportImpactStrategy`'s business. A change reaching a pytest plugin
    impacts every test.
    """

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
        *,
        dep_tree: nx.DiGraph,
    ) -> list[str]:
        """Find impacted tests including pytest-specific dependencies."""
        reached = _reached(impacted_modules, dep_tree)
        if session_wide := _session_wide_changes(reached, dep_tree, session):
            # pytest registers plugins for the whole session: their fixtures and
            # hooks are visible to every test, wherever they were declared.
            return _every_test(dep_tree, f"pytest plugin changes detected: {session_wide}", session)

        # AST-based analysis, from the same traversal; modules outside the graph
        # keep resolve_impacted_tests' conservative handling.
        impacted_tests = [node for node in reached if is_test_module(node)]
        impacted_tests += resolve_impacted_tests([m for m in impacted_modules if m not in dep_tree], dep_tree)

        if root_dir is not None:
            # Tests never import their conftest — pytest injects its fixtures — so a
            # conftest that changed, or imports changed test code, impacts its directory.
            test_code = _changes_by_role(
                impacted_modules, dep_tree, ns_module, tests_package, root_dir, application=False
            )
            conftest_dirs = _changed_conftest_dirs(changed_files, root_dir)
            conftest_dirs |= _conftest_dirs(_reached(test_code, dep_tree), dep_tree, root_dir)
            impacted_tests += _tests_under_conftests(conftest_dirs, dep_tree, root_dir)
        return sorted(set(impacted_tests))


class ConftestImportImpactStrategy(ImpactStrategy):
    """Strategy for conftests that import changed *application* code, directly or transitively.

    Tests never import their conftest, so a fixture built on changed code is invisible
    to test-side import analysis. This strategy treats such a conftest like an edited
    one: every test in its directory and below is impacted. It is safe but coarse — a
    top-level conftest importing the application selects almost every test on almost
    every change — so the default pipeline runs it with ``report_only=True``: it names
    those conftests instead, and ``--impacted-conftest-imports`` makes it select.
    (Conftests importing changed *test* code are :class:`PytestImpactStrategy`'s, always.)
    """

    def __init__(self, *, report_only: bool = False):
        self.report_only = report_only

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
        *,
        dep_tree: nx.DiGraph,
    ) -> list[str]:
        """Return the tests under every conftest the changed application code reaches."""
        if root_dir is None:
            return []
        application = _changes_by_role(impacted_modules, dep_tree, ns_module, tests_package, root_dir, application=True)
        conftest_dirs = _conftest_dirs(_reached(application, dep_tree), dep_tree, root_dir)
        if not self.report_only:
            return _tests_under_conftests(conftest_dirs, dep_tree, root_dir)
        if conftest_dirs:
            conftests = sorted(_relative(directory / "conftest.py", root_dir) for directory in conftest_dirs)
            notify(
                f"Changed application code is imported by {conftests}. Tests beneath them that use it only "
                + "through fixtures are not selected unless another rule selects them; pass "
                + "--impacted-conftest-imports (impacted-tests CLI: --conftest-imports) to select them all.",
                session,
            )
        return []


def _changes_by_role(
    impacted_modules: list[str],
    dep_tree: nx.DiGraph,
    ns_module: str,
    tests_package: str | None,
    root_dir: Path,
    *,
    application: bool,
) -> list[str]:
    """The changed graph modules that are application code — or, with ``application=False``, test code."""
    roles = _CodeRoles(ns_module=ns_module, tests_package=tests_package, root_dir=root_dir)

    def is_application_code(module: str) -> bool:
        # A node without a file cannot be placed: it counts as test code, which is followed.
        path = _module_path(module, dep_tree, root_dir)
        return path is not None and roles.is_application_code(path)

    return [module for module in impacted_modules if module in dep_tree and is_application_code(module) == application]


class DependencyFileImpactStrategy(ImpactStrategy):
    """Strategy that triggers all tests when dependency or test-config files change.

    When files like uv.lock, requirements*.txt, pyproject.toml or pytest.ini
    are modified, any test could potentially be affected. This strategy
    conservatively marks all discovered test modules as impacted. Besides
    the name patterns, the config file the running pytest actually loaded
    (``config.inipath``, e.g. from ``-c ci.ini``) always counts.
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
        *,
        dep_tree: nx.DiGraph,
    ) -> list[str]:
        """Return all test modules if dependency files have changed."""
        loaded_config = _loaded_config_file(session)
        dep_files = [
            f
            for f in changed_files
            if matches_dependency_file(f, self.patterns, self.glob_patterns)
            # File name first: resolving every changed path would stat each one.
            or (
                loaded_config is not None
                and PurePosixPath(f).name == loaded_config.name
                and _resolve_changed_file(f, root_dir) == loaded_config
            )
        ]
        if not dep_files:
            return []
        return _every_test(dep_tree, f"Dependency file changes detected: {dep_files}", session)


class InvalidationFileImpactStrategy(ImpactStrategy):
    """Strategy driven by user-supplied glob patterns for non-Python files.

    Static import analysis cannot see that a test depends on a JSON fixture,
    a SQL schema, or a YAML config. This strategy lets users declare those
    relationships themselves: a change to any file matching one of
    *patterns* marks **every** test module as impacted, exactly as a change
    to ``pyproject.toml`` does. See :func:`matches_any_glob` for the
    matching rules.

    It is the user-extensible counterpart to
    :class:`DependencyFileImpactStrategy`, configured via
    ``--impacted-invalidate-all`` (or the ``impacted_invalidate_all`` ini
    setting) in the pytest plugin, and ``--invalidate-all`` in the
    ``impacted-tests`` CLI.
    """

    def __init__(self, patterns: Sequence[str] = ()):
        self.patterns = tuple(patterns)

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
        *,
        dep_tree: nx.DiGraph,
    ) -> list[str]:
        """Return all test modules if any changed file matches a configured pattern."""
        hits = [f for f in changed_files if matches_any_glob(f, self.patterns)]
        if not hits:
            return []

        return _every_test(
            dep_tree, f"Invalidation file changes detected: {hits} (matched --impacted-invalidate-all)", session
        )


def get_default_strategies(
    *,
    watch_dep_files: bool = True,
    invalidate_all_patterns: Sequence[str] = (),
    conftest_imports: bool = False,
) -> list[ImpactStrategy]:
    """Return the default (built-in) strategy list for impact analysis.

    This centralizes the knowledge of which built-in strategies form the
    default pipeline. Third-party extensions are added separately via
    :func:`~pytest_impacted.api.build_strategy_with_extensions`.

    Args:
        watch_dep_files: Include :class:`DependencyFileImpactStrategy`.
        invalidate_all_patterns: Globs for :class:`InvalidationFileImpactStrategy`,
            whose matches impact every test. The strategy is only added to the
            pipeline when at least one pattern is given.
        conftest_imports: Let :class:`ConftestImportImpactStrategy` select, so a
            conftest importing changed *application* code impacts every test beneath
            it too (one importing changed test code always does). Without it, the
            strategy only names such conftests.
    """
    strategies: list[ImpactStrategy] = [
        ASTImpactStrategy(),
        PytestImpactStrategy(),
        ConftestImportImpactStrategy(report_only=not conftest_imports),
    ]
    if watch_dep_files:
        strategies.append(DependencyFileImpactStrategy())
    if invalidate_all_patterns:
        strategies.append(InvalidationFileImpactStrategy(invalidate_all_patterns))
    return strategies


class CompositeImpactStrategy(ImpactStrategy):
    """Strategy that combines multiple strategies."""

    def __init__(self, strategies: Sequence[ImpactStrategy | StrategyProtocol]):
        """Initialize with a list of strategies to apply."""
        self.strategies = strategies

    def enrich_dep_tree(
        self,
        dep_tree: nx.DiGraph,
        *,
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
    ) -> None:
        """Propagate :meth:`enrich_dep_tree` to every sub-strategy in list order.

        Because the graph is mutated in place, edges added by one
        sub-strategy are visible to every later sub-strategy's
        :meth:`enrich_dep_tree` call — and, once enrichment is complete,
        to every sub-strategy's :meth:`find_impacted_tests` call.
        Exceptions are logged and swallowed so one misbehaving extension
        cannot block the others.

        All context kwargs are forwarded unchanged so that scan-based
        enrichers have the same information available as :meth:`setup`
        and :meth:`find_impacted_tests`.
        """
        for strategy in self.strategies:
            # Duck-typed extensions need only find_impacted_tests; the hooks are optional.
            if (enrich := getattr(strategy, "enrich_dep_tree", None)) is None:
                continue
            try:
                enrich(
                    dep_tree,
                    ns_module=ns_module,
                    tests_package=tests_package,
                    root_dir=root_dir,
                    session=session,
                )
            except Exception:
                logger.warning(
                    "Strategy %s.%s raised in enrich_dep_tree(); skipping its enrichment phase.",
                    strategy.__class__.__module__,
                    strategy.__class__.__qualname__,
                    exc_info=True,
                )

    def setup(
        self,
        *,
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
        dep_tree: nx.DiGraph,
    ) -> None:
        """Propagate :meth:`setup` to every sub-strategy in list order.

        Exceptions raised by individual sub-strategies are logged at WARNING
        level and swallowed — one misbehaving extension must not prevent the
        others from running. This matches the fault-tolerance applied to
        entry-point discovery in :mod:`pytest_impacted.extensions`.
        """
        for strategy in self.strategies:
            if (strategy_setup := getattr(strategy, "setup", None)) is None:
                continue
            try:
                strategy_setup(
                    ns_module=ns_module,
                    tests_package=tests_package,
                    root_dir=root_dir,
                    session=session,
                    dep_tree=dep_tree,
                )
            except Exception:
                logger.warning(
                    "Strategy %s.%s raised in setup(); skipping its setup phase.",
                    strategy.__class__.__module__,
                    strategy.__class__.__qualname__,
                    exc_info=True,
                )

    def teardown(self) -> None:
        """Propagate :meth:`teardown` to every sub-strategy in reverse order.

        Reverse order follows the LIFO convention used by context managers
        and ``ExitStack``: the last strategy set up is the first torn down.
        Exceptions are logged and swallowed for the same fault-tolerance
        reason as :meth:`setup`.
        """
        for strategy in reversed(self.strategies):
            if (strategy_teardown := getattr(strategy, "teardown", None)) is None:
                continue
            try:
                strategy_teardown()
            except Exception:
                logger.warning(
                    "Strategy %s.%s raised in teardown(); continuing with remaining strategies.",
                    strategy.__class__.__module__,
                    strategy.__class__.__qualname__,
                    exc_info=True,
                )

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session: Any = None,
        *,
        dep_tree: nx.DiGraph,
    ) -> list[str]:
        """Find impacted tests by applying all strategies and combining results.

        Passes the shared ``dep_tree`` to all sub-strategies so the expensive
        graph construction happens only once in the pipeline.
        """
        all_impacted = []

        for strategy in self.strategies:
            strategy_results = strategy.find_impacted_tests(
                changed_files=changed_files,
                impacted_modules=impacted_modules,
                ns_module=ns_module,
                tests_package=tests_package,
                root_dir=root_dir,
                session=session,
                dep_tree=dep_tree,
            )
            all_impacted.extend(strategy_results)

        # Remove duplicates and sort
        return sorted(set(all_impacted))
