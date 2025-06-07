"""Graph analysis functionality."""

import logging
import types

import networkx as nx

from pytest_impacted.parsing import is_test_module, parse_module_imports
from pytest_impacted.traversal import import_submodules


def resolve_impacted_tests(impacted_modules, dep_tree: nx.DiGraph) -> list[str]:
    """Resolve impacted tests based on impacted modules.

    The current logic is to do a DFS from the impacted module to find all nodes that depend on it.
    We then check if these nodes are test modules.
    We return the list of test modules that are impacted.

    """
    impacted_tests = []
    for module in impacted_modules:
        if module not in dep_tree.nodes:
            # This could happen if a module file was modified but is a "dangling node" in the dependency tree.
            # This is likely because the module is not imported by any other module.
            logging.warning(
                "Module %s is marked as impacted was not found in dependency tree, "
                "likely pruned because it is a dangling node. Skipping.",
                module,
            )
            continue

        dependent_nodes = [node for node in nx.dfs_preorder_nodes(dep_tree, source=module) if is_test_module(node)]

        impacted_tests.extend(dependent_nodes)

    # Remove duplicates and sort the list for good measure.
    # (although the order of the tests should not matter)
    impacted_tests = sorted(list(set(impacted_tests)))

    return impacted_tests


def build_dep_tree(package: str | types.ModuleType, tests_package: str | types.ModuleType | None = None) -> nx.DiGraph:
    """Run the script for a given package name."""
    submodules = import_submodules(package)

    if tests_package:
        logging.debug("Adding modules from tests_package: %s", tests_package)
        test_submodules = import_submodules(tests_package)
        submodules.update(test_submodules)

    logging.debug("Building dependency tree for submodules: %s", submodules)

    digraph = nx.DiGraph()
    for name, module in submodules.items():
        logging.debug("Processing submodule: %s", name)
        digraph.add_node(name)
        module_imports = parse_module_imports(module)
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
