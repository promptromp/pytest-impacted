"""Unit-tests for the dependency file impact strategy module."""

from unittest.mock import MagicMock

import networkx as nx
import pytest

from pytest_impacted.strategies import (
    DEFAULT_DEPENDENCY_GLOB_PATTERNS,
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
    def test_matches_dependency_file(self, file_path, expected):
        assert matches_dependency_file(file_path) is expected

    def test_custom_patterns(self):
        assert matches_dependency_file("custom.lock", patterns=("custom.lock",), glob_patterns=()) is True
        assert matches_dependency_file("uv.lock", patterns=("custom.lock",), glob_patterns=()) is False

    def test_the_built_in_globs_have_no_double_star(self):
        """A recursive ``**`` would widen the built-in set, and plain ``PurePath.match`` would read it otherwise."""
        assert not [glob for glob in DEFAULT_DEPENDENCY_GLOB_PATTERNS if "**" in glob.split("/")]


@pytest.mark.parametrize(
    ("changed", "patterns", "expected"),
    [
        pytest.param(["src/module.py", "uv.lock", "tests/test_foo.py"], None, True, id="among other files"),
        pytest.param(["uv.lock"], None, True, id="alone"),
        pytest.param(["src/module.py", "tests/test_foo.py"], None, False, id="none"),
        pytest.param([], None, False, id="nothing changed"),
        pytest.param(["uv.lock"], (), False, id="no patterns"),
        pytest.param(["custom.lock"], ("custom.lock",), True, id="a custom pattern"),
    ],
)
def test_has_dependency_file_changes(changed, patterns, expected):
    kwargs = {} if patterns is None else {"patterns": patterns, "glob_patterns": ()}

    assert has_dependency_file_changes(changed, **kwargs) is expected


#: A package of two modules and its tests, in an arbitrary order: every test comes back, sorted.
GRAPH = ["mypackage.api", "mypackage.utils", "tests.test_z", "tests.test_api", "tests.test_m"]
EVERY_TEST = ["tests.test_api", "tests.test_m", "tests.test_z"]


@pytest.mark.parametrize(
    ("changed", "impacted", "patterns", "expected"),
    [
        pytest.param(["uv.lock"], [], None, EVERY_TEST, id="a lock file: every test, sorted"),
        pytest.param(["pyproject.toml", "uv.lock"], [], None, EVERY_TEST, id="dependency files alone"),
        pytest.param(["src/api.py", "requirements.txt"], ["mypackage.api"], None, EVERY_TEST, id="with code"),
        pytest.param(["src/api.py"], ["mypackage.api"], None, [], id="code alone: nothing"),
        pytest.param(["uv.lock"], [], ("custom.lock",), [], id="custom patterns replace the defaults"),
        pytest.param(["custom.lock"], [], ("custom.lock",), EVERY_TEST, id="a custom pattern"),
    ],
)
def test_dependency_file_impact_strategy(changed, impacted, patterns, expected):
    """Any dependency file changed impacts every test; the rest is other strategies' business."""
    strategy = DependencyFileImpactStrategy() if patterns is None else DependencyFileImpactStrategy(patterns, ())
    dep_tree = nx.DiGraph()
    dep_tree.add_nodes_from(GRAPH)

    result = strategy.find_impacted_tests(
        changed_files=changed,
        impacted_modules=impacted,
        ns_module="mypackage",
        tests_package="tests",
        dep_tree=dep_tree,
    )

    assert result == expected


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


def _session_without_config_file():
    session = MagicMock()
    session.config.inipath = None  # pytest found no config file
    return session


@pytest.mark.parametrize("session", [_session_without_config_file(), None], ids=["no_config_file", "cli_no_session"])
def test_without_a_loaded_config_only_names_match(session):
    """No config file found, or no pytest session at all (the CLI): name patterns still apply."""
    dep_tree = nx.DiGraph()
    dep_tree.add_node("tests.test_a")
    strategy = DependencyFileImpactStrategy()

    def run(changed):
        return strategy.find_impacted_tests(changed, [], "pkg", session=session, dep_tree=dep_tree)

    assert run(["ci/other.ini"]) == []
    assert run(["pytest.ini"]) == ["tests.test_a"]


def test_requirements_txt_survives_custom_glob_patterns():
    """Callers replacing the globs keep the exact names, requirements.txt included."""
    assert matches_dependency_file("requirements.txt", glob_patterns=("deps/*.txt",))
