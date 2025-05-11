"""Graph analysis functionality."""

import logging
import types

import networkx as nx

from pytest_affected.traversal import import_submodules
from pytest_affected.parsing import is_test_module, parse_module_imports


def resolve_affected_tests(modified_modules, dep_tree: nx.DiGraph) -> list[str]:
    """Resolve affected tests based on modified modules."""
    affected_tests = []
    for module in modified_modules:
        # Find all nodes that depend on the modified module by doing a DFS from the module
        # using the (inverted) directed graph of imports which is the dependency graph.
        dependent_nodes = [
            node
            for node in nx.dfs_preorder_nodes(dep_tree, source=module)
            if is_test_module(node)
        ]

        affected_tests.extend(dependent_nodes)

    # Remove duplicates and sort the list for good measure.
    # (although the order of the tests should not matter)
    affected_tests = sorted(list(set(affected_tests)))

    return affected_tests


def build_dep_tree(package: str | types.ModuleType) -> nx.DiGraph:
    """Run the script for a given package name."""
    submodules = import_submodules(package)
    digraph = nx.DiGraph()
    for name, module in submodules.items():
        logging.debug("build_dep_tree: processing submodule: %s", name)
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
    return inverted(digraph)


def maybe_prune_graph(digraph: nx.DiGraph) -> nx.DiGraph:
    """Prune the graph to remove nodes we do not need, e.g. singleton nodes."""
    for node in list(digraph.nodes):
        if digraph.in_degree(node) == 0 and digraph.out_degree(node) == 0:
            # prune singleton nodes (typically __init__.py files)
            logging.debug("build_dep_tree: removing singleton node: %s", node)
            digraph.remove_node(node)

    return digraph


def inverted(digraph: nx.DiGraph) -> nx.DiGraph:
    """Invert the graph."""
    return digraph.reverse()
