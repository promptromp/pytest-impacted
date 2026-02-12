"""Graph analysis functionality."""

import logging
import types

import networkx as nx

from pytest_impacted.parsing import is_test_module, parse_file_imports
from pytest_impacted.traversal import discover_submodules


def resolve_impacted_tests(impacted_modules, dep_tree: nx.DiGraph) -> list[str]:
    """Resolve impacted tests based on impacted modules.

    The current logic is to do a DFS from the impacted module to find all nodes that depend on it.
    We then check if these nodes are test modules.
    We return the list of test modules that are impacted.

    For modules not found in the dependency tree (dangling nodes):
    - Test modules are included directly as impacted (they changed, so they should run).
    - Production modules cause ALL test modules to be marked as impacted,
      erring on the side of caution per project philosophy.

    """
    impacted_tests = []
    all_test_modules_in_tree = [node for node in dep_tree.nodes if is_test_module(node)]

    for module in impacted_modules:
        if module not in dep_tree.nodes:
            logging.warning(
                "Module %s is marked as impacted but was not found in dependency tree "
                "(likely pruned as a dangling node).",
                module,
            )
            if is_test_module(module):
                # Test module changed but not in tree — include it directly.
                impacted_tests.append(module)
            else:
                # Production module changed but not in tree — conservatively
                # mark all known test modules as impacted.
                logging.warning(
                    "Production module %s not in dependency tree; conservatively marking all test modules as impacted.",
                    module,
                )
                impacted_tests.extend(all_test_modules_in_tree)
            continue

        dependent_nodes = [node for node in nx.dfs_preorder_nodes(dep_tree, source=module) if is_test_module(node)]

        impacted_tests.extend(dependent_nodes)

    # Remove duplicates and sort the list for good measure.
    # (although the order of the tests should not matter)
    impacted_tests = sorted(list(set(impacted_tests)))

    return impacted_tests


def build_dep_tree(package: str | types.ModuleType, tests_package: str | types.ModuleType | None = None) -> nx.DiGraph:
    """Build a dependency tree using filesystem discovery (no imports).

    Scans the package directory to find modules, reads their source files,
    and parses imports via AST — without executing any module-level code.
    """
    pkg_name = package if isinstance(package, str) else package.__name__
    submodules = discover_submodules(pkg_name)

    if tests_package:
        tests_name = tests_package if isinstance(tests_package, str) else tests_package.__name__
        logging.debug("Adding modules from tests_package: %s", tests_name)
        test_submodules = discover_submodules(tests_name)
        submodules = {**submodules, **test_submodules}

    logging.debug("Building dependency tree for %d submodules", len(submodules))

    digraph = nx.DiGraph()
    for name, file_path in submodules.items():
        logging.debug("Processing submodule: %s", name)
        digraph.add_node(name)
        is_pkg = file_path.endswith("__init__.py")
        module_imports = parse_file_imports(file_path, name, is_package=is_pkg)
        for imp in module_imports:
            if imp in submodules:
                # Nb. We only care about imports that are also submodules
                # of the package we are analyzing.
                digraph.add_node(imp)
                digraph.add_edge(name, imp)

    maybe_prune_graph(digraph)

    # The dependency graph is the reverse of the import graph, so invert it before returning.
    inverted_digraph = inverted(digraph)

    return inverted_digraph


def display_digraph(digraph: nx.DiGraph) -> None:
    """Display the dependency graph.

    Useful for debugging and verbose output to verify the graph is built correctly.

    """
    for node in digraph.nodes:
        edges = list(digraph.successors(node))
        print(f"{node} -> {edges}")


def maybe_prune_graph(digraph: nx.DiGraph) -> nx.DiGraph:
    """Prune the graph to remove nodes we do not need, e.g. singleton nodes."""
    for node in list(digraph.nodes):
        if digraph.in_degree(node) == 0 and digraph.out_degree(node) == 0:
            # prune singleton nodes (typically __init__.py files)
            logging.debug("Removing singleton node: %s", node)
            digraph.remove_node(node)

    return digraph


def inverted(digraph: nx.DiGraph) -> nx.DiGraph:
    """Invert the graph."""
    return digraph.reverse()
