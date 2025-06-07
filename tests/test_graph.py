"""Unit tests for the graph module."""

from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

from pytest_impacted import graph


@pytest.fixture
def sample_dep_tree():
    """Create a sample dependency tree for testing."""
    digraph = nx.DiGraph()
    # Add some test and non-test modules
    # Note: edges go from regular modules to test modules in the dependency graph
    digraph.add_edges_from(
        [
            ("module_a", "test_module1"),
            ("module_b", "test_module1"),
            ("module_b", "test_module2"),
            ("module_c", "test_module2"),
            ("module_d", "module_a"),
            ("module_d", "module_b"),
            ("module_e", "module_c"),
        ]
    )
    return digraph


@pytest.mark.parametrize(
    "modified_modules,expected_impacted",
    [
        # Test single module modification
        (["module_d"], {"test_module1", "test_module2"}),
        # Test multiple module modifications
        (["module_b", "module_c"], {"test_module1", "test_module2"}),
        # Test no impact
        (["module_e"], {"test_module2"}),
        # Test dangling node (module not in dependency tree)
        (["dangling_module"], set()),
    ],
)
def test_resolve_impacted_tests(sample_dep_tree, modified_modules, expected_impacted):
    """Test resolving impacted tests from modified modules.

    Args:
        sample_dep_tree: Fixture providing a sample dependency tree
        modified_modules: List of modules that were modified
        expected_impacted: Set of test modules expected to be impacted
    """
    impacted = graph.resolve_impacted_tests(modified_modules, sample_dep_tree)
    assert set(impacted) == expected_impacted


def test_build_dep_tree():
    """Test building dependency tree from a package."""
    # Create a mock package module
    mock_package = MagicMock()
    mock_package.__name__ = "mock_package"
    mock_package.__path__ = []

    # Mock the submodules and their imports
    mock_submodules = {
        "module_a": MagicMock(),
        "module_b": MagicMock(),
        "module_c": MagicMock(),
    }

    with (
        patch("pytest_impacted.graph.import_submodules", return_value=mock_submodules),
        patch("pytest_impacted.graph.parse_module_imports") as mock_parse_imports,
    ):
        # Set up mock imports for each module
        mock_parse_imports.side_effect = [
            ["module_b"],  # module_a imports
            ["module_c"],  # module_b imports
            [],  # module_c imports
        ]

        dep_tree = graph.build_dep_tree(mock_package)

        # Verify the graph structure
        assert set(dep_tree.nodes()) == {"module_a", "module_b", "module_c"}
        assert dep_tree.has_edge("module_b", "module_a")  # Note: edges are inverted
        assert dep_tree.has_edge("module_c", "module_b")


def test_maybe_prune_graph():
    """Test pruning of singleton nodes from the graph."""
    digraph = nx.DiGraph()
    digraph.add_edges_from(
        [
            ("module_a", "module_b"),
            ("module_c", "module_d"),
        ]
    )
    digraph.add_node("singleton")  # Add a singleton node

    pruned = graph.maybe_prune_graph(digraph)

    assert "singleton" not in pruned.nodes()
    assert "module_a" in pruned.nodes()
    assert "module_b" in pruned.nodes()
    assert "module_c" in pruned.nodes()
    assert "module_d" in pruned.nodes()


def test_inverted():
    """Test graph inversion."""
    digraph = nx.DiGraph()
    digraph.add_edges_from(
        [
            ("module_a", "module_b"),
            ("module_b", "module_c"),
        ]
    )

    inverted_graph = graph.inverted(digraph)

    assert inverted_graph.has_edge("module_b", "module_a")
    assert inverted_graph.has_edge("module_c", "module_b")
    assert not inverted_graph.has_edge("module_a", "module_b")
    assert not inverted_graph.has_edge("module_b", "module_c")
