"""Unit-tests for the AST impact strategy module."""

from unittest.mock import MagicMock, patch

from pytest_impacted.strategies import (
    ASTImpactStrategy,
)


class TestASTImpactStrategy:
    """Test the AST-based impact strategy."""

    @patch("pytest_impacted.strategies._cached_build_dep_tree")
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
