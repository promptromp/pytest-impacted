"""Integration tests for the strategies sub-modules."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from pytest_impacted.strategies import (
    PytestImpactStrategy,
)


class TestIntegration:
    """Integration tests for the strategy system."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.root_dir = Path(self.temp_dir)

    def test_pytest_strategy_includes_ast_results(self):
        """Test that PytestImpactStrategy includes AST results."""
        with patch("pytest_impacted.strategies.resolve_impacted_tests") as mock_resolve:
            mock_dep_tree = MagicMock()
            mock_resolve.return_value = ["test_module_ast"]

            strategy = PytestImpactStrategy()
            result = strategy.find_impacted_tests(
                changed_files=["src/module.py"],
                impacted_modules=["module"],
                ns_module="mypackage",
                dep_tree=mock_dep_tree,
            )

            # Should include AST-based results even when no conftest.py changes
            assert "test_module_ast" in result

    def test_absolute_and_relative_paths(self):
        """``changed_files`` may mix repo-relative and absolute paths; both resolve against ``root_dir``."""
        test_dir = self.root_dir / "tests"
        test_dir.mkdir()
        (test_dir / "conftest.py").touch()
        (test_dir / "test_example.py").touch()

        with (
            patch("pytest_impacted.strategies.resolve_impacted_tests") as mock_resolve,
            patch("pytest_impacted.strategies.is_test_module") as mock_is_test,
        ):
            mock_dep_tree = MagicMock()
            mock_dep_tree.nodes = ["tests.test_example"]
            mock_resolve.return_value = []
            mock_is_test.side_effect = lambda x: x.startswith("tests.") and "test_" in x

            strategy = PytestImpactStrategy()
            for conftest in ("tests/conftest.py", str(test_dir / "conftest.py")):
                result = strategy.find_impacted_tests(
                    changed_files=["src/module.py", conftest],
                    impacted_modules=[],
                    ns_module="mypackage",
                    tests_package="tests",
                    root_dir=self.root_dir,
                    dep_tree=mock_dep_tree,
                )
                assert result == ["tests.test_example"]
