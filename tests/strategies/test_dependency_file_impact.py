"""Unit-tests for the dependency file impact strategy module."""

from unittest.mock import patch

import networkx as nx
import pytest

from pytest_impacted.strategies import (
    DependencyFileImpactStrategy,
    _matches_dependency_file,
    has_dependency_file_changes,
)


class TestMatchesDependencyFile:
    """Test the _matches_dependency_file helper function."""

    @pytest.mark.parametrize(
        ("file_path", "expected"),
        [
            pytest.param("uv.lock", True, id="uv_lock"),
            pytest.param("project/uv.lock", True, id="uv_lock_in_subdirectory"),
            pytest.param("requirements.txt", True, id="requirements_txt"),
            pytest.param("pyproject.toml", True, id="pyproject_toml"),
            pytest.param("Pipfile", True, id="pipfile"),
            pytest.param("Pipfile.lock", True, id="pipfile_lock"),
            pytest.param("poetry.lock", True, id="poetry_lock"),
            pytest.param("setup.py", True, id="setup_py"),
            pytest.param("setup.cfg", True, id="setup_cfg"),
            pytest.param("requirements/prod.txt", True, id="nested_requirements"),
            pytest.param("requirements/sub/dev.txt", True, id="deeply_nested_requirements"),
            pytest.param("src/module.py", False, id="regular_py_file"),
            pytest.param("my_requirements.txt", False, id="similar_name_no_match"),
            pytest.param("requirements/README.md", False, id="non_txt_in_requirements_dir"),
        ],
    )
    def test_matches_dependency_file(self, file_path, expected):
        assert _matches_dependency_file(file_path) is expected

    def test_custom_patterns(self):
        assert _matches_dependency_file("custom.lock", patterns=("custom.lock",), glob_patterns=()) is True
        assert _matches_dependency_file("uv.lock", patterns=("custom.lock",), glob_patterns=()) is False


class TestHasDependencyFileChanges:
    """Test the has_dependency_file_changes function."""

    def test_returns_true_when_dep_file_present(self):
        changed_files = ["src/module.py", "uv.lock", "tests/test_foo.py"]
        assert has_dependency_file_changes(changed_files) is True

    def test_returns_false_when_no_dep_files(self):
        changed_files = ["src/module.py", "tests/test_foo.py"]
        assert has_dependency_file_changes(changed_files) is False

    def test_returns_false_for_empty_list(self):
        assert has_dependency_file_changes([]) is False

    def test_returns_true_for_only_dep_files(self):
        assert has_dependency_file_changes(["uv.lock"]) is True

    def test_uses_custom_patterns(self):
        assert has_dependency_file_changes(["uv.lock"], patterns=(), glob_patterns=()) is False
        assert has_dependency_file_changes(["custom.lock"], patterns=("custom.lock",), glob_patterns=()) is True


class TestDependencyFileImpactStrategy:
    """Test the DependencyFileImpactStrategy."""

    def _make_dep_tree(self, nodes: list[str]) -> nx.DiGraph:
        """Create a simple dependency graph with the given nodes."""
        graph = nx.DiGraph()
        graph.add_nodes_from(nodes)
        return graph

    @patch("pytest_impacted.strategies._cached_build_dep_tree")
    def test_returns_all_tests_when_dep_file_changed(self, mock_build_dep_tree):
        """When a dependency file changes, all test modules should be returned."""
        mock_build_dep_tree.return_value = self._make_dep_tree(
            ["mypackage.api", "mypackage.utils", "tests.test_api", "tests.test_utils"]
        )

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["uv.lock"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
        )

        assert result == ["tests.test_api", "tests.test_utils"]

    @patch("pytest_impacted.strategies._cached_build_dep_tree")
    def test_returns_empty_when_no_dep_file_changed(self, mock_build_dep_tree):
        """When no dependency files changed, should return empty list."""
        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["mypackage.module"],
            ns_module="mypackage",
        )

        assert result == []
        # Dep tree should not even be built
        mock_build_dep_tree.assert_not_called()

    @patch("pytest_impacted.strategies._cached_build_dep_tree")
    def test_works_with_empty_impacted_modules(self, mock_build_dep_tree):
        """The key scenario: only dep files changed, so impacted_modules is empty."""
        mock_build_dep_tree.return_value = self._make_dep_tree(["mypackage.core", "tests.test_core"])

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["pyproject.toml", "uv.lock"],
            impacted_modules=[],  # No .py files changed
            ns_module="mypackage",
            tests_package="tests",
        )

        assert result == ["tests.test_core"]

    @patch("pytest_impacted.strategies._cached_build_dep_tree")
    def test_custom_patterns(self, mock_build_dep_tree):
        """Custom patterns should be used instead of defaults."""
        mock_build_dep_tree.return_value = self._make_dep_tree(["mypackage.api", "tests.test_api"])

        strategy = DependencyFileImpactStrategy(
            patterns=("custom.lock",),
            glob_patterns=(),
        )

        # Default patterns should not trigger
        result = strategy.find_impacted_tests(
            changed_files=["uv.lock"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
        )
        assert result == []

        # Custom pattern should trigger
        result = strategy.find_impacted_tests(
            changed_files=["custom.lock"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
        )
        assert result == ["tests.test_api"]

    @patch("pytest_impacted.strategies._cached_build_dep_tree")
    def test_mixed_dep_and_py_changes(self, mock_build_dep_tree):
        """When both dep files and .py files change, all tests should be returned."""
        mock_build_dep_tree.return_value = self._make_dep_tree(
            ["mypackage.api", "mypackage.utils", "tests.test_api", "tests.test_utils"]
        )

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["src/api.py", "requirements.txt"],
            impacted_modules=["mypackage.api"],
            ns_module="mypackage",
            tests_package="tests",
        )

        # All tests returned because requirements.txt changed
        assert result == ["tests.test_api", "tests.test_utils"]

    @patch("pytest_impacted.strategies._cached_build_dep_tree")
    def test_results_are_sorted(self, mock_build_dep_tree):
        """Results should be sorted alphabetically."""
        mock_build_dep_tree.return_value = self._make_dep_tree(
            ["pkg.mod", "tests.test_z", "tests.test_a", "tests.test_m"]
        )

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["uv.lock"],
            impacted_modules=[],
            ns_module="pkg",
            tests_package="tests",
        )

        assert result == ["tests.test_a", "tests.test_m", "tests.test_z"]
