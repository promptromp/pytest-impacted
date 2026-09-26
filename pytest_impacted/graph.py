"""Graph analysis functionality."""

import logging
from pathlib import Path

import networkx as nx

from pytest_impacted._rust import RUST_AVAILABLE, rust_parse_all_imports
from pytest_impacted.parsing import (
    is_conftest_module,
    is_test_module,
    parse_file_imports,
    parse_pytest_plugins,
)
from pytest_impacted.traversal import discover_project_modules


logger = logging.getLogger(__name__)


def _parse_all_module_imports(submodules: dict[str, str]) -> dict[str, list[str]]:
    """Parse imports for all discovered submodules.

    Uses the Rust extension (parallel batch via rayon) when available,
    falling back to sequential astroid parsing.
    """
    if RUST_AVAILABLE:
        modules_info = [(path, name, path.endswith("__init__.py")) for name, path in submodules.items()]
        return rust_parse_all_imports(modules_info)

    result: dict[str, list[str]] = {}
    for name, file_path in submodules.items():
        logger.debug("Processing submodule: %s", name)
        is_pkg = file_path.endswith("__init__.py")
        result[name] = parse_file_imports(file_path, name, is_package=is_pkg)
    return result


def resolve_impacted_tests(impacted_modules, dep_tree: nx.DiGraph) -> list[str]:
    """Resolve impacted tests based on impacted modules.

    The current logic is to do a DFS from the impacted module to find all nodes that depend on it.
    We then check if these nodes are test modules.
    We return the list of test modules that are impacted.

    For modules not found in the dependency tree (e.g. outside the analyzed package scope):
    - Test modules are included directly as impacted (they changed, so they should run).
    - Production modules cause ALL test modules to be marked as impacted,
      erring on the side of caution per project philosophy.

    """
    impacted_tests = []
    all_test_modules_in_tree = [node for node in dep_tree.nodes if is_test_module(node)]

    for module in impacted_modules:
        if module not in dep_tree.nodes:
            logger.warning(
                "Module %s is marked as impacted but was not found in dependency tree "
                "(possibly outside the analyzed package scope).",
                module,
            )
            if is_test_module(module):
                # Test module changed but not in tree — include it directly.
                impacted_tests.append(module)
            else:
                # Production module changed but not in tree — conservatively
                # mark all known test modules as impacted.
                logger.warning(
                    "Production module %s not in dependency tree; conservatively marking all test modules as impacted.",
                    module,
                )
                impacted_tests.extend(all_test_modules_in_tree)
            continue

        dependent_nodes = [node for node in nx.dfs_preorder_nodes(dep_tree, source=module) if is_test_module(node)]

        impacted_tests.extend(dependent_nodes)

    # Remove duplicates and sort the list for good measure.
    # (although the order of the tests should not matter)
    impacted_tests = sorted(set(impacted_tests))

    return impacted_tests


def _pytest_plugin_edges(submodules: dict[str, str], aliases: dict[str, str]) -> dict[str, list[str]]:
    """``{declaring module: [plugin modules]}`` for every ``pytest_plugins`` declaration in scope.

    pytest reads the declaration from conftests and test modules, and again from
    every plugin it loads that way, so plugins are followed transitively. Names
    outside the graph — usually third-party plugins such as ``pytester`` — are skipped.
    """
    pending = [name for name in submodules if is_conftest_module(name) or is_test_module(name)]
    edges: dict[str, list[str]] = {}
    while pending:
        name = pending.pop()
        if name in edges:
            continue
        edges[name] = []
        for declared in parse_pytest_plugins(submodules[name]):
            plugin = aliases.get(declared, declared)
            if plugin not in submodules:
                logger.debug("pytest_plugins entry %r in %s is not an analysed module; not followed", plugin, name)
                continue
            edges[name].append(plugin)
            pending.append(plugin)
    return {name: plugins for name, plugins in edges.items() if plugins}


def build_dep_tree(package: str, tests_package: str | None = None, root_dir: str | Path | None = None) -> nx.DiGraph:
    """Build a dependency tree using filesystem discovery (no imports).

    Scans the package directory to find modules, reads their source files,
    and parses imports via AST — without executing any module-level code.
    Package paths are resolved against *root_dir* (default: the current directory).

    Conftests above the packages are included too (see
    :func:`~pytest_impacted.traversal.discover_project_modules`), and every
    discovered node carries its absolute file in the ``path`` attribute. Modules
    named in a ``pytest_plugins`` declaration count as imports (see
    :func:`~pytest_impacted.parsing.parse_pytest_plugins`) and are flagged with
    the ``pytest_plugin`` attribute.
    """
    submodules, aliases = discover_project_modules(package, tests_package, root_dir=root_dir)

    logger.debug("Building dependency tree for %d submodules", len(submodules))

    # Parse imports — Rust parallel path or Python sequential fallback
    all_imports = _parse_all_module_imports(submodules)

    plugin_edges = _pytest_plugin_edges(submodules, aliases)

    digraph = nx.DiGraph()
    for name, file_path in submodules.items():
        digraph.add_node(name, path=file_path)
        for candidate in [*all_imports.get(name, []), *plugin_edges.get(name, [])]:
            imp = aliases.get(candidate, candidate)
            if imp in submodules:
                digraph.add_node(imp)
                digraph.add_edge(name, imp)
    # pytest registers plugins for the whole session; see PytestImpactStrategy.
    for plugin in {plugin for plugins in plugin_edges.values() for plugin in plugins}:
        digraph.nodes[plugin]["pytest_plugin"] = True

    # Other names each module imports under (see discover_project_modules), for names
    # that come from outside the source, such as ``-p`` plugins.
    digraph.graph["aliases"] = dict(aliases)  # a copy: the discovery result is cached and shared

    # The dependency graph is the reverse of the import graph, so invert it before returning.
    return digraph.reverse()
