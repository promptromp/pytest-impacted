"""Unit-tests for the dependency file impact strategy module."""

from unittest.mock import patch

import networkx as nx

from pytest_impacted.strategies import (
    DependencyFileImpactStrategy,
    _matches_dependency_file,
    has_dependency_file_changes,
)


class TestMatchesDependencyFile:
    """Test the _matches_dependency_file helper function."""

    def test_matches_uv_lock(self):
        assert _matches_dependency_file("uv.lock") is True

    def test_matches_uv_lock_in_subdirectory(self):
        assert _matches_dependency_file("project/uv.lock") is True

    def test_matches_requirements_txt(self):
        assert _matches_dependency_file("requirements.txt") is True

    def test_matches_pyproject_toml(self):
        assert _matches_dependency_file("pyproject.toml") is True

    def test_matches_pipfile(self):
        assert _matches_dependency_file("Pipfile") is True

    def test_matches_pipfile_lock(self):
        assert _matches_dependency_file("Pipfile.lock") is True

    def test_matches_poetry_lock(self):
        assert _matches_dependency_file("poetry.lock") is True

    def test_matches_setup_py(self):
        assert _matches_dependency_file("setup.py") is True

    def test_matches_setup_cfg(self):
        assert _matches_dependency_file("setup.cfg") is True

    def test_matches_nested_requirements(self):
        assert _matches_dependency_file("requirements/prod.txt") is True

    def test_matches_deeply_nested_requirements(self):
        assert _matches_dependency_file("requirements/sub/dev.txt") is True

    def test_no_match_regular_py_file(self):
        assert _matches_dependency_file("src/module.py") is False

    def test_no_match_similar_name(self):
        """Basename must be exact — 'my_requirements.txt' should not match."""
        assert _matches_dependency_file("my_requirements.txt") is False

    def test_no_match_non_txt_in_requirements_dir(self):
        assert _matches_dependency_file("requirements/README.md") is False

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
