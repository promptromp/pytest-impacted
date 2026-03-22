"""Unit tests for the graph module."""

from unittest.mock import patch

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
        # Test dangling production module — conservatively marks all tests as impacted
        (["dangling_module"], {"test_module1", "test_module2"}),
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


def test_resolve_impacted_tests_dangling_test_module(sample_dep_tree):
    """Test that a dangling test module is directly included as impacted."""
    impacted = graph.resolve_impacted_tests(["test_new_feature"], sample_dep_tree)
    assert "test_new_feature" in impacted


def test_resolve_impacted_tests_dangling_production_module(sample_dep_tree):
    """Test that a dangling production module causes all test modules to be impacted."""
    impacted = graph.resolve_impacted_tests(["unknown_prod_module"], sample_dep_tree)
    # Should include all test modules from the tree
    assert "test_module1" in impacted
    assert "test_module2" in impacted


def test_build_dep_tree():
    """Test building dependency tree from a package."""
    # Mock discovered submodules: name -> absolute file path
    mock_submodules = {
        "module_a": "/fake/module_a.py",
        "module_b": "/fake/module_b.py",
        "module_c": "/fake/module_c.py",
    }

    with (
        patch("pytest_impacted.graph.RUST_AVAILABLE", False),
        patch("pytest_impacted.graph.discover_submodules", return_value=mock_submodules),
        patch("pytest_impacted.graph.parse_file_imports") as mock_parse_imports,
    ):
        # Set up mock imports for each module
        mock_parse_imports.side_effect = [
            ["module_b"],  # module_a imports
            ["module_c"],  # module_b imports
            [],  # module_c imports
        ]

        dep_tree = graph.build_dep_tree("mock_package")

        # Verify the graph structure
        assert set(dep_tree.nodes()) == {"module_a", "module_b", "module_c"}
        assert dep_tree.has_edge("module_b", "module_a")  # Note: edges are inverted
        assert dep_tree.has_edge("module_c", "module_b")


def test_pruned_singleton_init_triggers_run_all():
    """A changed __init__.py singleton should not cause all tests to run."""
    mock_submodules = {
        "pkg": "/fake/pkg/__init__.py",
        "pkg.core": "/fake/pkg/core.py",
        "tests.test_core": "/fake/tests/test_core.py",
    }

    with (
        patch("pytest_impacted.graph.RUST_AVAILABLE", False),
        patch("pytest_impacted.graph.discover_submodules", return_value=mock_submodules),
        patch("pytest_impacted.graph.parse_file_imports") as mock_parse,
    ):
        # pkg/__init__.py imports nothing, pkg.core imports nothing,
        # tests.test_core imports pkg.core
        mock_parse.side_effect = [
            [],  # pkg/__init__.py
            [],  # pkg.core
            ["pkg.core"],  # tests.test_core
        ]

        dep_tree = graph.build_dep_tree("pkg")
        impacted = graph.resolve_impacted_tests(["pkg"], dep_tree)

        # Only __init__.py changed — nothing depends on it, so no tests should run
        assert impacted == []


def test_pruned_singleton_init_does_not_affect_other_changes():
    """Changing __init__.py alongside a real module should only run tests for the real module."""
    mock_submodules = {
        "pkg": "/fake/pkg/__init__.py",
        "pkg.core": "/fake/pkg/core.py",
        "pkg.utils": "/fake/pkg/utils.py",
        "tests.test_core": "/fake/tests/test_core.py",
        "tests.test_utils": "/fake/tests/test_utils.py",
    }

    with (
        patch("pytest_impacted.graph.RUST_AVAILABLE", False),
        patch("pytest_impacted.graph.discover_submodules", return_value=mock_submodules),
        patch("pytest_impacted.graph.parse_file_imports") as mock_parse,
    ):
        mock_parse.side_effect = [
            [],  # pkg/__init__.py
            [],  # pkg.core
            [],  # pkg.utils
            ["pkg.core"],  # tests.test_core
            ["pkg.utils"],  # tests.test_utils
        ]

        dep_tree = graph.build_dep_tree("pkg")
        # Both __init__.py and core changed
        impacted = graph.resolve_impacted_tests(["pkg", "pkg.core"], dep_tree)

        # Only test_core should run (depends on pkg.core), not test_utils
        assert set(impacted) == {"tests.test_core"}


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
