import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pytest_impacted.git import GitMode
from pytest_impacted.plugin import (
    pytest_addoption,
    pytest_configure,
    pytest_report_header,
    validate_base_branch,
    validate_config,
    validate_module,
    validate_tests_dir,
)


CLI_OPTION_DESTS = {
    "impacted",
    "impacted_module",
    "impacted_git_mode",
    "impacted_base_branch",
    "impacted_tests_dir",
    "no_impacted_dep_files",
    "impacted_invalidate_all",
    "impacted_disable_ext",
}


@patch("pytest_impacted.plugin.discover_extension_metadata", return_value=())
def test_pytest_addoption(_mock_extensions):
    """Every option lands in the ``impacted`` group, and every one has a matching ini key.

    Extensions register options of their own, so they are stubbed out here —
    otherwise merely installing one would fail this test.
    """
    mock_group = MagicMock()
    mock_parser = MagicMock()
    mock_parser.getgroup.return_value = mock_group

    pytest_addoption(mock_parser)

    mock_parser.getgroup.assert_called_once_with("impacted")
    registered = {call.kwargs["dest"] for call in mock_group.addoption.call_args_list}
    assert registered == CLI_OPTION_DESTS
    ini_names = {call.args[0] for call in mock_parser.addini.call_args_list}
    assert ini_names == CLI_OPTION_DESTS


def test_pytest_configure(pytestconfig):
    """Test that the plugin configures correctly."""
    pytest_configure(pytestconfig)

    # Check that the marker is added
    markers = pytestconfig.getini("markers")
    assert "impacted(state): mark test as impacted by the state of the git repository" in markers


def test_pytest_report_header(pytestconfig, monkeypatch):
    """Test that the plugin adds the correct header information."""
    for name, value in (
        ("impacted_module", "test_module"),
        ("impacted_git_mode", GitMode.UNSTAGED),
        ("impacted_base_branch", "main"),
        ("impacted_tests_dir", "tests"),
        ("impacted_invalidate_all", ["*.json"]),
    ):
        monkeypatch.setattr(pytestconfig.option, name, value)

    header = pytest_report_header(pytestconfig)
    assert len(header) == 1
    assert "pytest-impacted:" in header[0]
    assert "impacted_module=test_module" in header[0]
    assert "impacted_git_mode=unstaged" in header[0]
    assert "impacted_base_branch=main" in header[0]
    assert "impacted_tests_dir=tests" in header[0]
    assert "impacted_invalidate_all=['*.json']" in header[0]
    assert "backend=" in header[0]


def test_validate_config_valid(pytestconfig, monkeypatch):
    """Test that valid configuration passes validation."""
    monkeypatch.setattr(pytestconfig.option, "impacted", True)
    monkeypatch.setattr(pytestconfig.option, "impacted_module", "pytest_impacted")
    monkeypatch.setattr(pytestconfig.option, "impacted_git_mode", GitMode.UNSTAGED)
    validate_config(pytestconfig)  # Should not raise


def test_validate_config_missing_module(pytestconfig, monkeypatch):
    """Test that validation fails when module is missing."""
    monkeypatch.setattr(pytestconfig.option, "impacted", True)
    monkeypatch.setattr(pytestconfig.option, "impacted_module", None)
    monkeypatch.setitem(pytestconfig._inicache, "impacted_module", None)
    monkeypatch.setattr(pytestconfig.option, "impacted_git_mode", GitMode.UNSTAGED)
    with pytest.raises(pytest.UsageError, match="No module specified"):
        validate_config(pytestconfig)


def test_validate_config_missing_git_mode(pytestconfig, monkeypatch):
    """Test that validation fails when git mode is missing."""
    monkeypatch.setattr(pytestconfig.option, "impacted", True)
    monkeypatch.setattr(pytestconfig.option, "impacted_module", "test_module")
    monkeypatch.setattr(pytestconfig.option, "impacted_git_mode", None)
    monkeypatch.setitem(pytestconfig._inicache, "impacted_git_mode", None)
    with pytest.raises(pytest.UsageError, match="No git mode specified"):
        validate_config(pytestconfig)


def test_validate_config_branch_mode_missing_base(pytestconfig, monkeypatch):
    """Test that validation fails when branch mode is used without base branch."""
    monkeypatch.setattr(pytestconfig.option, "impacted", True)
    monkeypatch.setattr(pytestconfig.option, "impacted_module", "pytest_impacted")
    monkeypatch.setattr(pytestconfig.option, "impacted_git_mode", GitMode.BRANCH)
    monkeypatch.setattr(pytestconfig.option, "impacted_base_branch", None)
    monkeypatch.setitem(pytestconfig._inicache, "impacted_base_branch", None)
    with pytest.raises(pytest.UsageError, match="No base branch specified"):
        validate_config(pytestconfig)


def testvalidate_module_hyphen_suggests_underscore():
    """Test that a hyphenated module name suggests the underscore version."""
    with pytest.raises(pytest.UsageError, match="Did you mean: --impacted-module=pytest_impacted"):
        validate_module("pytest-impacted", Path.cwd())


def testvalidate_module_nonexistent():
    """Test that a completely unknown module gives a helpful error."""
    with pytest.raises(pytest.UsageError, match="Module 'doesnotexist' not found"):
        validate_module("doesnotexist", Path.cwd())


def testvalidate_module_valid():
    """Test that a valid module name passes validation."""
    validate_module("pytest_impacted", Path.cwd())  # Should not raise


def testvalidate_tests_dir_nonexistent():
    """Test that a non-existent tests directory gives a helpful error."""
    with pytest.raises(pytest.UsageError, match="Tests directory 'nonexistent_dir' does not exist"):
        validate_tests_dir("nonexistent_dir", Path.cwd())


def testvalidate_tests_dir_valid():
    """Test that a valid tests directory passes validation."""
    validate_tests_dir("tests", Path.cwd())  # Should not raise


def testvalidate_tests_dir_without_init(tmp_path):
    """Test that a tests directory without __init__.py passes validation."""
    test_dir = tmp_path / "my_tests"
    test_dir.mkdir()
    (test_dir / "test_example.py").write_text("def test_it(): pass\n")
    validate_tests_dir("my_tests", tmp_path)  # Should not raise


def testvalidate_base_branch_nonexistent():
    """Test that a non-existent base branch gives a helpful error with available refs."""
    with pytest.raises(pytest.UsageError, match="Base branch 'nonexistent_branch_xyz' does not exist"):
        validate_base_branch("nonexistent_branch_xyz", ".")


def testvalidate_base_branch_valid():
    """Test that a valid base branch passes validation."""
    validate_base_branch("HEAD", ".")  # Should not raise — HEAD exists in any git checkout


def testvalidate_base_branch_from_subdirectory():
    """validate_base_branch works from a subdirectory of the git root (monorepo)."""
    # tests/ is a subdirectory of the project; .git is at the project root
    validate_base_branch("HEAD", "tests")  # Should not raise


def testvalidate_base_branch_option_like():
    """An option-like base branch is rejected as a usage error, not passed to git."""
    with pytest.raises(pytest.UsageError, match="Invalid base branch"):
        validate_base_branch("--output=/tmp/pwned", ".")


def testvalidate_base_branch_no_git_repo(tmp_path):
    """validate_base_branch gives a helpful error when no git repo is found."""
    with pytest.raises(pytest.UsageError, match="No git repository found"):
        validate_base_branch("main", str(tmp_path))


def testvalidate_module_src_layout_suggestion(tmp_path):
    """When a module exists under src/, suggest the src-layout path."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mypackage").mkdir()
    (tmp_path / "src" / "mypackage" / "__init__.py").touch()

    with pytest.raises(pytest.UsageError, match="--impacted-module=src/mypackage"):
        validate_module("mypackage", tmp_path)


def test_plugin_imports_without_git_executable(tmp_path):
    """The pytest11 entry point loads on every pytest run, so a missing git binary must not break pytest.

    GitPython raises ImportError when the git executable is absent (slim
    containers), which is why ``pytest_impacted.git`` guards its import.
    """
    script = "import pytest_impacted.plugin as p; print(p.GIT_AVAILABLE)"
    result = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path / "no-git")},
        capture_output=True,
        text=True,
        check=False,  # assert below, so a failure reports the child's stderr
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


@pytest.mark.parametrize("ini_name", ["impacted", "no_impacted_dep_files"])
def test_boolean_ini_values_are_typed(pytester, ini_name):
    """Untyped ini values arrive as strings, and ``"false"`` is truthy — so ``= false`` did the opposite."""
    pytester.makeini(f"[pytest]\n{ini_name} = false\n")

    config = pytester.parseconfig()

    assert config.getini(ini_name) is False
