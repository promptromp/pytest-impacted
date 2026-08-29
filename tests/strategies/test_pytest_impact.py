"""Unit-tests for the strategies module."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx

from pytest_impacted.strategies import (
    PytestImpactStrategy,
    find_test_modules_under,
)


class TestPytestImpactStrategy:
    """Test the pytest-specific impact strategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.root_dir = Path(self.temp_dir)

    @patch("pytest_impacted.strategies.resolve_impacted_tests")
    @patch("pytest_impacted.strategies.is_test_module")
    def test_find_impacted_tests_no_conftest(self, mock_is_test, mock_resolve):
        """Test strategy when no conftest.py files are changed."""
        mock_dep_tree = MagicMock()
        mock_dep_tree.nodes = ["test_module_a", "module_b"]
        mock_resolve.return_value = ["test_module_a"]
        mock_is_test.side_effect = lambda x: x.startswith("test_")

        strategy = PytestImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["src/module_a.py"],
            impacted_modules=["module_a"],
            ns_module="mypackage",
            tests_package="tests",
            root_dir=self.root_dir,
            dep_tree=mock_dep_tree,
        )

        assert result == ["test_module_a"]
        mock_resolve.assert_called_once()

    @patch("pytest_impacted.strategies.resolve_impacted_tests")
    @patch("pytest_impacted.strategies.is_test_module")
    def test_find_impacted_tests_with_conftest(self, mock_is_test, mock_resolve):
        """Test strategy when conftest.py files are changed."""
        # Create test directory structure
        test_dir = self.root_dir / "tests"
        test_dir.mkdir()
        subdir = test_dir / "subdir"
        subdir.mkdir()

        # Create conftest.py and test files
        conftest_file = test_dir / "conftest.py"
        conftest_file.touch()
        test_file = subdir / "test_example.py"
        test_file.touch()

        mock_dep_tree = MagicMock()
        mock_dep_tree.nodes = ["tests.subdir.test_example", "tests.test_other", "module_b"]
        mock_resolve.return_value = []  # No AST-based impacts
        mock_is_test.side_effect = lambda x: x.startswith("tests.") and "test_" in x

        strategy = PytestImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["tests/conftest.py"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
            root_dir=self.root_dir,
            dep_tree=mock_dep_tree,
        )

        # Should include test modules affected by conftest.py
        assert "tests.subdir.test_example" in result
        assert "tests.test_other" not in result  # This one is not in a subdirectory

    def test_find_test_modules_under_conftest_dir(self):
        """The conftest rule uses the shared "same directory and below" helper."""
        # Create test directory structure
        test_dir = self.root_dir / "tests"
        test_dir.mkdir()
        subdir = test_dir / "subdir"
        subdir.mkdir()
        (subdir / "test_example.py").touch()
        other_dir = self.root_dir / "other_tests"
        other_dir.mkdir()
        (other_dir / "test_other.py").touch()

        dep_tree = nx.DiGraph()
        dep_tree.add_nodes_from(["tests.subdir.test_example", "other_tests.test_other", "mypackage.core"])

        # Only the module in a subdirectory of the conftest dir is affected
        assert find_test_modules_under(test_dir, dep_tree, root_dir=self.root_dir) == ["tests.subdir.test_example"]

        # A conftest at the repo root reaches every test module...
        assert find_test_modules_under(self.root_dir, dep_tree, root_dir=self.root_dir) == [
            "other_tests.test_other",
            "tests.subdir.test_example",
        ]
        # ...and a directory holding no tests reaches none.
        assert find_test_modules_under(self.root_dir / "docs", dep_tree, root_dir=self.root_dir) == []
