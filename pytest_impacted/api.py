"""Public entry point and composition root for impact analysis.

Assembles the strategy pipeline from the built-ins and any registered
extensions, then drives it: git state -> changed files -> impacted modules ->
impacted test files.
"""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import networkx as nx

from pytest_impacted.display import notify, warn
from pytest_impacted.extensions import StrategyProtocol, load_extensions
from pytest_impacted.git import GitMode, find_impacted_files_in_repo
from pytest_impacted.graph import link_changed_files, resolve_files_to_nodes
from pytest_impacted.strategies import (
    CompositeImpactStrategy,
    ImpactStrategy,
    cached_build_dep_tree,
    get_default_strategies,
    run_copy,
)
from pytest_impacted.traversal import (
    canonical_root,
    path_to_package_name,
    resolve_modules_to_files,
)


def matches_impacted_tests(item_path: str, *, impacted_tests: list[str]) -> bool:
    """Check if the item path matches any of the impacted tests.

    Public API. The plugin uses it as the legacy path-suffix match on ``item.location``,
    alongside matching ``item.path`` and the resolved location (see ``plugin._impacted_items``).
    """
    return any(test == item_path or test.endswith(os.sep + item_path) for test in impacted_tests)


def build_strategy_with_extensions(
    *,
    watch_dep_files: bool = True,
    invalidate_all_patterns: Sequence[str] = (),
    conftest_imports: bool = False,
    disabled: Sequence[str] = (),
    ext_config: dict[str, Any] | None = None,
) -> ImpactStrategy:
    """Build a composite strategy combining built-in and extension strategies.

    This is the composition root for the analysis pipeline: it is the one place
    that knows about both the built-in strategies and the entry-point
    extensions, which is why it lives here rather than in either of those
    modules. Built-in strategies come first, then extensions by priority.

    Args:
        watch_dep_files: Whether to include DependencyFileImpactStrategy.
        invalidate_all_patterns: User globs whose matches impact every test
            (see :class:`~pytest_impacted.strategies.InvalidationFileImpactStrategy`).
        conftest_imports: Whether a conftest importing changed *application* code
            impacts every test beneath it, as one importing changed test code always does
            (see :class:`~pytest_impacted.strategies.ConftestImportImpactStrategy`).
        disabled: Extension names to exclude.
        ext_config: Configuration values for extensions.

    Returns:
        A CompositeImpactStrategy wrapping all strategies.
    """
    builtin_strategies = get_default_strategies(
        watch_dep_files=watch_dep_files,
        invalidate_all_patterns=invalidate_all_patterns,
        conftest_imports=conftest_imports,
    )
    ext_strategies = load_extensions(disabled=disabled, ext_config=ext_config)

    # Sort extensions by priority (built-ins keep their fixed order)
    ext_strategies.sort(key=lambda s: getattr(s, "priority", 100))

    return CompositeImpactStrategy(builtin_strategies + ext_strategies)


def _notify_unimported(linked: list[str], dep_tree: nx.DiGraph, root_dir: str | Path, session: Any) -> None:
    """Name the changed files no analysed module imports: import analysis selects nothing for them.

    Only that: another strategy may still select for them (``setup.py`` is a dependency
    file, a changed ``conftest.py`` selects its directory), so the notice advises nothing.
    """
    root = canonical_root(root_dir)
    unimported = sorted(
        Path(dep_tree.nodes[node]["path"]).relative_to(root).as_posix()
        for node in linked
        if not dep_tree.out_degree(node)
    )
    if unimported:
        pronoun = "it" if len(unimported) == 1 else "them"
        notify(f"Import analysis selects no tests for {unimported}: no analysed module imports {pronoun}.", session)


def _test_files(
    modules: list[str], dep_tree: nx.DiGraph, ns_module: str, tests_package: str | None, root_dir: str | Path
) -> list[str]:
    """The file of each impacted test module: its node's ``path``, or discovery's for a node without one.

    Through the graph first, like the changed files: a test module no walk finds — one an
    extension added with its ``path`` — would otherwise be dropped.
    """
    paths = [dep_tree.nodes[module].get("path") if module in dep_tree else None for module in modules]
    unplaced = [module for module, path in zip(modules, paths, strict=True) if not path]
    if not unplaced:
        return [path for path in paths if path]
    found = resolve_modules_to_files(unplaced, ns_module=ns_module, tests_package=tests_package, root_dir=root_dir)
    return [path for path in paths if path] + found


def get_impacted_tests(
    impacted_git_mode: GitMode,
    impacted_base_branch: str,
    root_dir: Path,
    ns_module: str,
    tests_dir: str | None = None,
    session=None,
    strategy: ImpactStrategy | StrategyProtocol | None = None,
    watch_dep_files: bool = True,
    invalidate_all_patterns: Sequence[str] = (),
    use_merge_base: bool = True,
    conftest_imports: bool = False,
) -> list[str] | None:
    """Get the list of impacted tests based on the git state and static analysis.

    ``watch_dep_files``, ``invalidate_all_patterns`` and ``conftest_imports`` configure
    the default pipeline and are ignored when an explicit ``strategy`` is supplied.
    ``use_merge_base`` makes branch mode diff from the fork point (the default)
    rather than the base branch's tip.

    Returns ``None`` when nothing is impacted, and raises
    :class:`~pytest_impacted.git.GitUnavailableError` when that cannot be known
    because git cannot run — callers must not treat the two alike.
    """
    git_mode = impacted_git_mode
    base_branch = impacted_base_branch

    # Use default strategy if none provided
    if strategy is None:
        strategy = CompositeImpactStrategy(
            get_default_strategies(
                watch_dep_files=watch_dep_files,
                invalidate_all_patterns=invalidate_all_patterns,
                conftest_imports=conftest_imports,
            )
        )
    elif not isinstance(strategy, ImpactStrategy):
        # A duck-typed strategy need only have find_impacted_tests; the composite
        # skips the lifecycle hooks it lacks.
        strategy = CompositeImpactStrategy([strategy])

    tests_package = None
    if tests_dir:
        tests_package = path_to_package_name(tests_dir)

    impacted_files = find_impacted_files_in_repo(
        root_dir,
        git_mode=git_mode,
        base_branch=base_branch,
        use_merge_base=use_merge_base,
        on_fallback=lambda reason: warn(reason, session),
    )
    if not impacted_files:
        notify(
            "No modified files found in the repository. "
            + "Please check your git state and the value supplied to --impacted-git-mode if you expected otherwise.",
            session,
        )
        return None

    notify(
        f"Impacted files in the repository: {impacted_files}",
        session,
    )

    # Build the dependency graph once and pass it to the strategy pipeline.
    # The result is LRU-cached and must not be mutated, so we hand each run a
    # copy. Strategies that implement enrich_dep_tree() mutate the copy,
    # leaving the cached base graph pristine for subsequent runs.
    cached = cached_build_dep_tree(ns_module, tests_package=tests_package, root_dir=canonical_root(root_dir))
    dep_tree = run_copy(cached)

    # A changed file the graph lacks — deleted, or no walk reaches it — joins the run's copy,
    # linked to whatever still imports it; say so for one nothing imports.
    _notify_unimported(link_changed_files(impacted_files, dep_tree, root_dir=root_dir), dep_tree, root_dir, session)

    # Resolved through the graph, before enrichment, so every impacted module is one of its nodes.
    impacted_modules = resolve_files_to_nodes(impacted_files, dep_tree, root_dir=root_dir)
    if not impacted_modules:
        notify(
            f"No impacted Python modules detected. Impacted files were: {impacted_files}. "
            "Continuing to strategy pipeline.",
            session,
        )

    # Enrichment phase — runs before setup so that setup and find_impacted_tests
    # both see the final graph (with any synthetic edges added by extensions).
    # Receives the full context so scan-based enrichers can walk the source tree.
    strategy.enrich_dep_tree(
        dep_tree,
        ns_module=ns_module,
        tests_package=tests_package,
        root_dir=root_dir,
        session=session,
    )

    # Lifecycle: setup → find_impacted_tests → teardown. The try/finally
    # guarantees teardown runs even if find_impacted_tests raises, so
    # strategies that allocated resources in setup always get a chance to
    # release them.
    strategy.setup(
        ns_module=ns_module,
        tests_package=tests_package,
        root_dir=root_dir,
        session=session,
        dep_tree=dep_tree,
    )
    try:
        impacted_test_modules = strategy.find_impacted_tests(
            changed_files=impacted_files,
            impacted_modules=impacted_modules,
            ns_module=ns_module,
            tests_package=tests_package,
            root_dir=root_dir,
            session=session,
            dep_tree=dep_tree,
        )
    finally:
        strategy.teardown()

    if not impacted_test_modules:
        warn(
            "No unit-test modules impacted by the changes could be detected. "
            + f"Impacted Python modules were: {impacted_modules}",
            session,
        )
        return None

    impacted_test_files = _test_files(impacted_test_modules, dep_tree, ns_module, tests_package, root_dir)
    if not impacted_test_files:
        warn(
            "No unit-test file paths impacted by the changes could be found. "
            + f"impacted test modules were: {impacted_test_modules}",
            session,
        )
        return None

    notify(
        f"impacted unit-test files in the repository: {impacted_test_files}",
        session,
    )

    return impacted_test_files
