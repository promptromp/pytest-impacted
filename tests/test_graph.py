"""Unit tests for the graph module."""

from unittest.mock import patch

import networkx as nx
import pytest

from pytest_impacted import graph
from pytest_impacted.traversal import ProjectModules


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
        patch("pytest_impacted.graph.discover_project_modules", return_value=ProjectModules(mock_submodules, {})),
        patch("pytest_impacted.graph.discover_ancestor_conftests", return_value={}),
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


def test_changed_init_with_no_dependents_impacts_nothing():
    """A changed __init__.py singleton should not cause all tests to run."""
    mock_submodules = {
        "pkg": "/fake/pkg/__init__.py",
        "pkg.core": "/fake/pkg/core.py",
        "tests.test_core": "/fake/tests/test_core.py",
    }

    with (
        patch("pytest_impacted.graph.RUST_AVAILABLE", False),
        patch("pytest_impacted.graph.discover_project_modules", return_value=ProjectModules(mock_submodules, {})),
        patch("pytest_impacted.graph.discover_ancestor_conftests", return_value={}),
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
        patch("pytest_impacted.graph.discover_project_modules", return_value=ProjectModules(mock_submodules, {})),
        patch("pytest_impacted.graph.discover_ancestor_conftests", return_value={}),
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


def test_build_dep_tree_includes_root_conftest_with_paths(tmp_path):
    """A root conftest joins the graph, and every node records its source file."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").touch()
    (tmp_path / "app/db.py").write_text("")
    (tmp_path / "conftest.py").write_text("from app.db import connect\n")

    dep_tree = graph.build_dep_tree("app", root_dir=tmp_path)

    assert dep_tree.has_edge("app.db", "conftest")  # the graph is inverted: dependency -> dependent
    assert dep_tree.nodes["conftest"]["path"] == str((tmp_path / "conftest.py").resolve())
    assert dep_tree.nodes["app.db"]["path"] == str((tmp_path / "app/db.py").resolve())


def test_build_dep_tree_does_not_duplicate_a_conftest_inside_the_package(tmp_path):
    """Walking up from a tests dir inside the package passes a conftest pkgutil already named."""
    for rel in ("src/app/__init__.py", "src/app/conftest.py", "src/app/tests/test_a.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")

    dep_tree = graph.build_dep_tree("src/app", tests_package="src/app/tests", root_dir=tmp_path)

    assert "app.conftest" in dep_tree
    assert "src.app.conftest" not in dep_tree


def test_ancestor_conftest_relative_imports_resolve_in_src_layout(tmp_path):
    """Named ``app.conftest`` (not ``src.app.conftest``), so ``.core.db`` matches the discovered module."""
    files = {
        "src/app/__init__.py": "",
        "src/app/conftest.py": "from .core.db import connect\n",
        "src/app/core/__init__.py": "",
        "src/app/core/db.py": "def connect(): ...\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("src/app/core", root_dir=tmp_path)

    assert dep_tree.has_edge("app.core.db", "app.conftest")


def test_ancestor_conftest_whose_short_name_clashes_keeps_its_full_name(tmp_path):
    """``tests/app/conftest.py`` would shorten to ``app.conftest``, already the package's own conftest.

    Dropping it on the clash would lose every edge from it, so it keeps its full path name.
    """
    files = {
        "src/app/__init__.py": "",
        "src/app/conftest.py": "",
        "src/app/core/__init__.py": "",
        "src/app/core/db.py": "",
        "tests/app/__init__.py": "",
        "tests/app/conftest.py": "import app.core.db\n",
        "tests/app/unit/test_x.py": "",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("src/app", tests_package="tests/app/unit", root_dir=tmp_path)

    assert dep_tree.nodes["app.conftest"]["path"] == str((tmp_path / "src/app/conftest.py").resolve())
    assert dep_tree.has_edge("app.core.db", "tests.app.conftest")


def test_pytest_plugins_declaration_is_an_edge(tmp_path):
    """A plugin module is imported by pytest on the conftest's behalf; tests never import it."""
    files = {
        "app/__init__.py": "",
        "app/db.py": "",
        "tests/fixtures.py": "from app.db import connect\n",
        "conftest.py": 'pytest_plugins = ["tests.fixtures", "pytester"]\n',
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("tests.fixtures", "conftest")
    assert nx.has_path(dep_tree, "app.db", "conftest")


def test_pytest_plugin_targets_are_flagged(tmp_path):
    """Flagged so the strategy can treat them as session-wide."""
    files = {
        "app/__init__.py": "",
        "tests/fixtures.py": "",
        "tests/test_a.py": 'pytest_plugins = "tests.fixtures"\n',
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.nodes["tests.fixtures"].get("pytest_plugin") is True
    assert not dep_tree.nodes["tests.test_a"].get("pytest_plugin")


def test_namespace_subpackage_modules_are_linked_by_absolute_and_relative_imports(tmp_path):
    """``processors/`` has no ``__init__.py``; its modules still import and are imported."""
    files = {
        "app/__init__.py": "",
        "app/processors/ocr.py": "from . import util\n",
        "app/processors/util.py": "",
        "tests/test_ocr.py": "from app.processors.ocr import run\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("app.processors.util", "app.processors.ocr")
    assert dep_tree.has_edge("app.processors.ocr", "tests.test_ocr")
    assert graph.resolve_impacted_tests(["app.processors.util"], dep_tree) == ["tests.test_ocr"]


def test_a_file_reached_under_two_names_is_one_node(tmp_path):
    """A tests dir inside the package is walked by both discoveries; its files must not be doubled."""
    files = {
        "app/__init__.py": "",
        "app/core.py": "",
        "app/tests/helpers.py": "import app.core\n",
        "app/tests/test_a.py": "from tests.helpers import make\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="app/tests", root_dir=tmp_path)

    paths = [dep_tree.nodes[node]["path"] for node in dep_tree.nodes]
    assert len(paths) == len(set(paths))
    # ``tests.helpers`` is an alias of ``app.tests.helpers``: the import still becomes an edge.
    assert "app.tests.test_a" in graph.resolve_impacted_tests(["app.core"], dep_tree)
