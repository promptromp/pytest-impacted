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
        with (
            patch("pytest_impacted.strategies._cached_build_dep_tree") as mock_build_tree,
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

    def test_localpath_objects_from_git(self):
        """Test handling of py.path.local.LocalPath objects from GitPython."""

        class MockLocalPath:
            """Mock LocalPath object that mimics py.path.local.LocalPath behavior."""

            def __init__(self, path_str: str):
                self.strpath = path_str

            def __str__(self):
                return f"LocalPath({self.strpath})"

            def endswith(self, suffix: str) -> bool:
                return self.strpath.endswith(suffix)

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

        with (
            patch("pytest_impacted.strategies._cached_build_dep_tree") as mock_build_tree,
            patch("pytest_impacted.strategies.resolve_impacted_tests") as mock_resolve,
            patch("pytest_impacted.strategies.is_test_module") as mock_is_test,
        ):
            mock_dep_tree = MagicMock()
            mock_dep_tree.nodes = ["tests.subdir.test_example", "tests.test_other", "module_b"]
            mock_build_tree.return_value = mock_dep_tree
            mock_resolve.return_value = []  # No AST-based impacts
            mock_is_test.side_effect = lambda x: x.startswith("tests.") and "test_" in x

            # Create LocalPath object for conftest.py - this simulates what GitPython returns
            conftest_localpath = MockLocalPath(str(conftest_file))

            strategy = PytestImpactStrategy()
            result = strategy.find_impacted_tests(
                changed_files=[conftest_localpath],  # Pass LocalPath object
                impacted_modules=[],
                ns_module="mypackage",
                tests_package="tests",
                root_dir=self.root_dir,
            )

            # Should include test modules affected by conftest.py
            assert "tests.subdir.test_example" in result

    def test_mixed_path_types(self):
        """Test handling of mixed path types in changed_files."""

        class MockLocalPath:
            """Mock LocalPath object."""

            def __init__(self, path_str: str):
                self.strpath = path_str

            def endswith(self, suffix: str) -> bool:
                return self.strpath.endswith(suffix)

        # Create test directory structure
        test_dir = self.root_dir / "tests"
        test_dir.mkdir()

        conftest_file = test_dir / "conftest.py"
        conftest_file.touch()

        with (
            patch("pytest_impacted.strategies._cached_build_dep_tree") as mock_build_tree,
            patch("pytest_impacted.strategies.resolve_impacted_tests") as mock_resolve,
            patch("pytest_impacted.strategies.is_test_module") as mock_is_test,
        ):
            mock_dep_tree = MagicMock()
            mock_dep_tree.nodes = ["tests.test_example"]
            mock_build_tree.return_value = mock_dep_tree
            mock_resolve.return_value = []
            mock_is_test.side_effect = lambda x: x.startswith("tests.") and "test_" in x

            # Mix of LocalPath object and regular string
            mixed_files = [
                "src/module.py",  # Regular string
                MockLocalPath(str(conftest_file)),  # LocalPath object
            ]

            strategy = PytestImpactStrategy()
            # Should not crash with mixed path types
            result = strategy.find_impacted_tests(
                changed_files=mixed_files,
                impacted_modules=[],
                ns_module="mypackage",
                tests_package="tests",
                root_dir=self.root_dir,
            )

            # Should handle both types without error
            assert isinstance(result, list)
