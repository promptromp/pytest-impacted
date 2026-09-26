"""Unit-tests for the dependency file impact strategy module."""

from unittest.mock import MagicMock

import networkx as nx
import pytest

from pytest_impacted.strategies import (
    DependencyFileImpactStrategy,
    has_dependency_file_changes,
    matches_dependency_file,
)


class TestMatchesDependencyFile:
    """Test the matches_dependency_file helper function."""

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
            # Test-runner configuration: addopts, markers, filterwarnings apply to every test
            pytest.param("pytest.ini", True, id="pytest_ini"),
            pytest.param("tox.ini", True, id="tox_ini"),
            # Requirements files named by suffix, at any depth
            pytest.param("requirements-dev.txt", True, id="requirements_dev_txt"),
            pytest.param("requirements_test.txt", True, id="requirements_test_txt"),
            pytest.param("backend/requirements-ci.txt", True, id="requirements_variant_in_subdirectory"),
            pytest.param("requirements.in", True, id="pip_tools_in"),
            pytest.param("requirements-dev.in", True, id="pip_tools_variant_in"),
            pytest.param("constraints.txt", True, id="constraints_txt"),
            pytest.param("test-constraints.txt", True, id="prefixed_constraints"),
            pytest.param("pdm.lock", True, id="pdm_lock"),
            pytest.param("pytest.ini.bak", False, id="pytest_ini_backup"),
            pytest.param("docs/requirements-guide.md", False, id="requirements_named_doc"),
            pytest.param("src/module.py", False, id="regular_py_file"),
            # Over-selecting is the safe direction: any *requirements*.txt counts
            pytest.param("my_requirements.txt", True, id="prefixed_requirements"),
            pytest.param("test-requirements.txt", True, id="openstack_style_requirements"),
            pytest.param("requirements/dev.in", True, id="nested_pip_tools_in"),
            pytest.param("requirements-dev.lock", True, id="rye_lock"),
            pytest.param("pylock.toml", True, id="pep751_lock"),
            pytest.param("pylock.dev.toml", True, id="pep751_named_lock"),
            pytest.param("pixi.lock", True, id="pixi_lock"),
            pytest.param(".pytest.ini", True, id="hidden_pytest_ini"),
            pytest.param("pytest.toml", True, id="pytest_toml"),
            pytest.param(".pytest.toml", True, id="hidden_pytest_toml"),
            pytest.param("docs/requirements.md", False, id="requirements_doc"),
            pytest.param("requirements/README.md", False, id="non_txt_in_requirements_dir"),
        ],
    )
    def testmatches_dependency_file(self, file_path, expected):
        assert matches_dependency_file(file_path) is expected

    def test_custom_patterns(self):
        assert matches_dependency_file("custom.lock", patterns=("custom.lock",), glob_patterns=()) is True
        assert matches_dependency_file("uv.lock", patterns=("custom.lock",), glob_patterns=()) is False


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

    def test_returns_all_tests_when_dep_file_changed(self):
        """When a dependency file changes, all test modules should be returned."""
        dep_tree = self._make_dep_tree(["mypackage.api", "mypackage.utils", "tests.test_api", "tests.test_utils"])

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["uv.lock"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
            dep_tree=dep_tree,
        )

        assert result == ["tests.test_api", "tests.test_utils"]

    def test_returns_empty_when_no_dep_file_changed(self):
        """When no dependency files changed, should return empty list."""
        dep_tree = self._make_dep_tree(["mypackage.module"])

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["mypackage.module"],
            ns_module="mypackage",
            dep_tree=dep_tree,
        )

        assert result == []

    def test_works_with_empty_impacted_modules(self):
        """The key scenario: only dep files changed, so impacted_modules is empty."""
        dep_tree = self._make_dep_tree(["mypackage.core", "tests.test_core"])

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["pyproject.toml", "uv.lock"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
            dep_tree=dep_tree,
        )

        assert result == ["tests.test_core"]

    def test_custom_patterns(self):
        """Custom patterns should be used instead of defaults."""
        dep_tree = self._make_dep_tree(["mypackage.api", "tests.test_api"])

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
            dep_tree=dep_tree,
        )
        assert result == []

        # Custom pattern should trigger
        result = strategy.find_impacted_tests(
            changed_files=["custom.lock"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
            dep_tree=dep_tree,
        )
        assert result == ["tests.test_api"]

    def test_mixed_dep_and_py_changes(self):
        """When both dep files and .py files change, all tests should be returned."""
        dep_tree = self._make_dep_tree(["mypackage.api", "mypackage.utils", "tests.test_api", "tests.test_utils"])

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["src/api.py", "requirements.txt"],
            impacted_modules=["mypackage.api"],
            ns_module="mypackage",
            tests_package="tests",
            dep_tree=dep_tree,
        )

        # All tests returned because requirements.txt changed
        assert result == ["tests.test_api", "tests.test_utils"]

    def test_results_are_sorted(self):
        """Results should be sorted alphabetically."""
        dep_tree = self._make_dep_tree(["pkg.mod", "tests.test_z", "tests.test_a", "tests.test_m"])

        strategy = DependencyFileImpactStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["uv.lock"],
            impacted_modules=[],
            ns_module="pkg",
            tests_package="tests",
            dep_tree=dep_tree,
        )

        assert result == ["tests.test_a", "tests.test_m", "tests.test_z"]


class TestLoadedConfigFile:
    """The config file pytest actually loaded counts whatever it is called (``-c ci.ini``)."""

    @staticmethod
    def session_with_inipath(inipath):
        session = MagicMock()
        session.config.inipath = inipath
        return session

    def test_loaded_config_is_a_dependency_file(self, tmp_path):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci/pytest-ci.ini").touch()
        dep_tree = nx.DiGraph()
        dep_tree.add_node("tests.test_a")

        result = DependencyFileImpactStrategy().find_impacted_tests(
            changed_files=["ci/pytest-ci.ini"],
            impacted_modules=[],
            ns_module="pkg",
            root_dir=tmp_path,
            session=self.session_with_inipath(tmp_path / "ci/pytest-ci.ini"),
            dep_tree=dep_tree,
        )

        assert result == ["tests.test_a"]

    def test_a_same_named_file_elsewhere_does_not_count(self, tmp_path):
        """Only the loaded file itself: the name matching is just a shortcut before the path check."""
        dep_tree = nx.DiGraph()
        dep_tree.add_node("tests.test_a")

        result = DependencyFileImpactStrategy().find_impacted_tests(
            changed_files=["other/pytest-ci.ini"],
            impacted_modules=[],
            ns_module="pkg",
            root_dir=tmp_path,
            session=self.session_with_inipath(tmp_path / "ci/pytest-ci.ini"),
            dep_tree=dep_tree,
        )

        assert result == []

    def test_other_ini_files_do_not_count(self, tmp_path):
        dep_tree = nx.DiGraph()
        dep_tree.add_node("tests.test_a")

        result = DependencyFileImpactStrategy().find_impacted_tests(
            changed_files=["ci/other.ini"],
            impacted_modules=[],
            ns_module="pkg",
            root_dir=tmp_path,
            session=self.session_with_inipath(tmp_path / "ci/pytest-ci.ini"),
            dep_tree=dep_tree,
        )

        assert result == []


def test_a_run_without_a_config_file_matches_names_only():
    """pytest found no config file (``config.inipath is None``): only the name patterns apply."""
    session = MagicMock()
    session.config.inipath = None
    dep_tree = nx.DiGraph()
    dep_tree.add_node("tests.test_a")

    result = DependencyFileImpactStrategy().find_impacted_tests(
        changed_files=["ci/other.ini"], impacted_modules=[], ns_module="pkg", session=session, dep_tree=dep_tree
    )

    assert result == []


def test_requirements_txt_survives_custom_glob_patterns():
    """Callers replacing the globs keep the exact names, requirements.txt included."""
    assert matches_dependency_file("requirements.txt", glob_patterns=("deps/*.txt",))
