"""Unit-tests for the strategies module."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from pytest_impacted.strategies import (
    ASTImpactStrategy,
    CompositeImpactStrategy,
    ImpactStrategy,
    PytestImpactStrategy,
)


class TestASTImpactStrategy:
    """Test the AST-based impact strategy."""

    @patch("pytest_impacted.strategies.build_dep_tree")
    @patch("pytest_impacted.strategies.resolve_impacted_tests")
    def test_find_impacted_tests(self, mock_resolve, mock_build_tree):
        """Test that AST strategy calls the correct functions."""
        # Setup mocks
        mock_dep_tree = MagicMock()
        mock_build_tree.return_value = mock_dep_tree
        mock_resolve.return_value = ["test_module_a", "test_module_b"]

        strategy = ASTImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["src/module_a.py"],
            impacted_modules=["module_a"],
            ns_module="mypackage",
            tests_package="tests",
        )

        # Verify the correct functions were called with correct parameters
        mock_build_tree.assert_called_once_with("mypackage", tests_package="tests")
        mock_resolve.assert_called_once_with(["module_a"], mock_dep_tree)
        assert result == ["test_module_a", "test_module_b"]


class TestPytestImpactStrategy:
    """Test the pytest-specific impact strategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.root_dir = Path(self.temp_dir)

    @patch("pytest_impacted.strategies.build_dep_tree")
    @patch("pytest_impacted.strategies.resolve_impacted_tests")
    @patch("pytest_impacted.strategies.is_test_module")
    def test_find_impacted_tests_no_conftest(self, mock_is_test, mock_resolve, mock_build_tree):
        """Test strategy when no conftest.py files are changed."""
        mock_dep_tree = MagicMock()
        mock_dep_tree.nodes = ["test_module_a", "module_b"]
        mock_build_tree.return_value = mock_dep_tree
        mock_resolve.return_value = ["test_module_a"]
        mock_is_test.side_effect = lambda x: x.startswith("test_")

        strategy = PytestImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["src/module_a.py"],
            impacted_modules=["module_a"],
            ns_module="mypackage",
            tests_package="tests",
            root_dir=self.root_dir,
        )

        assert result == ["test_module_a"]
        mock_build_tree.assert_called_once()
        mock_resolve.assert_called_once()

    @patch("pytest_impacted.strategies.build_dep_tree")
    @patch("pytest_impacted.strategies.resolve_impacted_tests")
    @patch("pytest_impacted.strategies.is_test_module")
    def test_find_impacted_tests_with_conftest(self, mock_is_test, mock_resolve, mock_build_tree):
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
        mock_build_tree.return_value = mock_dep_tree
        mock_resolve.return_value = []  # No AST-based impacts
        mock_is_test.side_effect = lambda x: x.startswith("tests.") and "test_" in x

        strategy = PytestImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["tests/conftest.py"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
            root_dir=self.root_dir,
        )

        # Should include test modules affected by conftest.py
        assert "tests.subdir.test_example" in result
        assert "tests.test_other" not in result  # This one is not in a subdirectory

    def test_is_test_affected_by_conftest(self):
        """Test the conftest impact detection logic."""
        # Create test directory structure
        test_dir = self.root_dir / "tests"
        test_dir.mkdir()
        subdir = test_dir / "subdir"
        subdir.mkdir()
        test_file = subdir / "test_example.py"
        test_file.touch()

        strategy = PytestImpactStrategy()

        # Test module in subdirectory should be affected
        result = strategy._is_test_affected_by_conftest("tests.subdir.test_example", test_dir, self.root_dir)
        assert result is True

        # Test module in sibling directory should not be affected
        other_dir = self.root_dir / "other_tests"
        other_dir.mkdir()
        other_test_file = other_dir / "test_other.py"
        other_test_file.touch()

        result = strategy._is_test_affected_by_conftest("other_tests.test_other", test_dir, self.root_dir)
        assert result is False


class TestCompositeImpactStrategy:
    """Test the composite strategy that combines multiple strategies."""

    def test_find_impacted_tests_combines_strategies(self):
        """Test that composite strategy combines results from multiple strategies."""
        # Create mock strategies
        strategy1 = MagicMock(spec=ImpactStrategy)
        strategy1.find_impacted_tests.return_value = ["test_a", "test_b"]

        strategy2 = MagicMock(spec=ImpactStrategy)
        strategy2.find_impacted_tests.return_value = ["test_b", "test_c"]

        composite = CompositeImpactStrategy([strategy1, strategy2])
        result = composite.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["module"],
            ns_module="mypackage",
        )

        # Should combine and deduplicate results
        assert sorted(result) == ["test_a", "test_b", "test_c"]

        # Both strategies should have been called with the same parameters
        expected_call_args = {
            "changed_files": ["src/module.py"],
            "impacted_modules": ["module"],
            "ns_module": "mypackage",
            "tests_package": None,
            "root_dir": None,
            "session": None,
        }
        strategy1.find_impacted_tests.assert_called_once_with(**expected_call_args)
        strategy2.find_impacted_tests.assert_called_once_with(**expected_call_args)

    def test_find_impacted_tests_empty_strategies(self):
        """Test composite strategy with no sub-strategies."""
        composite = CompositeImpactStrategy([])
        result = composite.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["module"],
            ns_module="mypackage",
        )
        assert result == []

    def test_find_impacted_tests_single_strategy(self):
        """Test composite strategy with single sub-strategy."""
        strategy = MagicMock(spec=ImpactStrategy)
        strategy.find_impacted_tests.return_value = ["test_a"]

        composite = CompositeImpactStrategy([strategy])
        result = composite.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["module"],
            ns_module="mypackage",
        )

        assert result == ["test_a"]
        strategy.find_impacted_tests.assert_called_once()


class TestIntegration:
    """Integration tests for the strategy system."""

    def test_pytest_strategy_includes_ast_results(self):
        """Test that PytestImpactStrategy includes AST results."""
        with (
            patch("pytest_impacted.strategies.build_dep_tree") as mock_build_tree,
            patch("pytest_impacted.strategies.resolve_impacted_tests") as mock_resolve,
        ):
            mock_dep_tree = MagicMock()
            mock_build_tree.return_value = mock_dep_tree
            mock_resolve.return_value = ["test_module_ast"]

            strategy = PytestImpactStrategy()
            result = strategy.find_impacted_tests(
                changed_files=["src/module.py"],
                impacted_modules=["module"],
                ns_module="mypackage",
            )

            # Should include AST-based results even when no conftest.py changes
            assert "test_module_ast" in result
