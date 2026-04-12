"""Unit-tests for the AST impact strategy module."""

from unittest.mock import MagicMock, patch

from pytest_impacted.strategies import (
    ASTImpactStrategy,
)


class TestASTImpactStrategy:
    """Test the AST-based impact strategy."""

    @patch("pytest_impacted.strategies.resolve_impacted_tests")
    def test_find_impacted_tests(self, mock_resolve):
        """Test that AST strategy uses the provided dep_tree."""
        mock_dep_tree = MagicMock()
        mock_resolve.return_value = ["test_module_a", "test_module_b"]

        strategy = ASTImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["src/module_a.py"],
            impacted_modules=["module_a"],
            ns_module="mypackage",
            tests_package="tests",
            dep_tree=mock_dep_tree,
        )

        mock_resolve.assert_called_once_with(["module_a"], mock_dep_tree)
        assert result == ["test_module_a", "test_module_b"]
