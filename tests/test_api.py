"""Unit-tests for the api module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pytest_impacted.api import get_impacted_tests, matches_impacted_tests
from pytest_impacted.git import GitMode


@pytest.mark.parametrize(
    ("item_path", "impacted_tests", "expected"),
    [
        pytest.param(
            "tests/test_example.py",
            ["project/module/tests/test_example.py", "project/another_module/tests/test_other.py"],
            True,
            id="suffix_match",
        ),
        pytest.param(
            "tests/test_another.py",
            ["project/module/tests/test_example.py", "project/another_module/tests/test_other.py"],
            False,
            id="no_match",
        ),
        pytest.param("tests/test_example.py", [], False, id="empty_impacted_list"),
        pytest.param(
            "project/module/tests/test_example.py",
            ["project/module/tests/test_example.py"],
            True,
            id="exact_match",
        ),
        pytest.param(
            "test_example.py",
            ["project/module/tests/test_example.pyc"],
            False,
            id="substring_not_suffix",
        ),
        pytest.param(
            "longer/path/to/tests/test_example.py",
            ["tests/test_example.py"],
            False,
            id="item_path_longer_than_impacted",
        ),
        pytest.param(
            "test_example.py",
            ["project/module/tests/foo_test_example.py"],
            False,
            id="false_suffix_no_boundary",
        ),
    ],
)
def test_matches_impacted_tests(item_path, impacted_tests, expected):
    assert matches_impacted_tests(item_path, impacted_tests=impacted_tests) is expected


@patch("pytest_impacted.api.find_impacted_files_in_repo")
def test_get_impacted_tests_no_impacted_files(mock_find_impacted_files):
    mock_find_impacted_files.return_value = []
    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
    )
    assert result is None
    mock_find_impacted_files.assert_called_once_with(Path("."), git_mode=GitMode.UNSTAGED, base_branch="main")


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_modules")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_success_with_tests_dir(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """Test get_impacted_tests successful path with tests_dir."""
    # Setup mocks
    mock_find_impacted_files.return_value = ["file1.py", "file2.py"]
    mock_resolve_files_to_modules.return_value = ["module1", "module2"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py", "test_file2.py"]

    # Create a mock strategy that returns our expected test modules
    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1", "test_module2"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
        strategy=mock_strategy,
    )

    assert result == ["test_file1.py", "test_file2.py"]
    # Verify tests_package was derived from tests_dir and passed through
    mock_resolve_files_to_modules.assert_called_once_with(
        ["file1.py", "file2.py"], ns_module="project_ns", tests_package="tests"
    )


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_modules")
def test_get_impacted_tests_no_impacted_modules(
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """Test get_impacted_tests when no impacted modules are found."""
    mock_find_impacted_files.return_value = ["file1.py", "file2.py"]
    mock_resolve_files_to_modules.return_value = []

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
    )

    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_modules")
def test_get_impacted_tests_no_impacted_test_modules(
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """Test get_impacted_tests when no impacted test modules are found."""
    mock_find_impacted_files.return_value = ["file1.py"]
    mock_resolve_files_to_modules.return_value = ["module1"]

    # Create a mock strategy that returns no test modules
    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = []

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        strategy=mock_strategy,
    )

    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_modules")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_no_impacted_test_files(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """Test get_impacted_tests when no impacted test files are found."""
    mock_find_impacted_files.return_value = ["file1.py"]
    mock_resolve_files_to_modules.return_value = ["module1"]
    mock_resolve_modules_to_files.return_value = []

    # Create a mock strategy that returns test modules
    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        strategy=mock_strategy,
    )

    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_modules")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_success_without_tests_dir(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """Test get_impacted_tests successful path without tests_dir."""
    mock_find_impacted_files.return_value = ["file1.py"]
    mock_resolve_files_to_modules.return_value = ["module1"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py"]

    # Create a mock strategy that returns test modules
    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        strategy=mock_strategy,
    )

    assert result == ["test_file1.py"]
    mock_resolve_files_to_modules.assert_called_once_with(["file1.py"], ns_module="project_ns", tests_package=None)


@patch("pytest_impacted.api.find_impacted_files_in_repo")
def test_get_impacted_tests_with_nested_tests_dir(mock_find_impacted_files):
    """Test get_impacted_tests correctly converts nested tests_dir to package name."""
    mock_find_impacted_files.return_value = []

    with patch("pytest_impacted.api.resolve_files_to_modules") as mock_resolve:
        mock_resolve.return_value = []

        get_impacted_tests(
            impacted_git_mode=GitMode.UNSTAGED,
            impacted_base_branch="main",
            root_dir=Path("."),
            ns_module="project_ns",
            tests_dir="tests/unit",
        )

        # path_to_package_name should not be called since no impacted files
        # but verify the function doesn't crash with nested paths


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_modules")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_dep_file_only_change(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """When only dependency files changed, the strategy pipeline still runs.

    Even though impacted_modules is empty (no .py files changed), the orchestrator
    always delegates to strategies — DependencyFileImpactStrategy handles this case.
    """
    mock_find_impacted_files.return_value = ["uv.lock"]
    mock_resolve_files_to_modules.return_value = []  # No .py files -> no modules

    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1", "test_module2"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py", "test_file2.py"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
        strategy=mock_strategy,
        watch_dep_files=True,
    )

    assert result == ["test_file1.py", "test_file2.py"]
    mock_strategy.find_impacted_tests.assert_called_once()


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_modules")
def test_get_impacted_tests_dep_file_with_watch_disabled(
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """When watch_dep_files=False, DependencyFileImpactStrategy is excluded from the default
    composite. With no .py modules changed, the remaining strategies (AST, Pytest) find
    nothing, so the result is None.
    """
    mock_find_impacted_files.return_value = ["uv.lock"]
    mock_resolve_files_to_modules.return_value = []

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        watch_dep_files=False,
    )

    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_modules")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_mixed_dep_and_py_changes(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """Both dep files and .py files changed — strategy should receive all changed files."""
    mock_find_impacted_files.return_value = ["src/module.py", "uv.lock"]
    mock_resolve_files_to_modules.return_value = ["mypackage.module"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py", "test_file2.py"]

    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1", "test_module2"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
        strategy=mock_strategy,
    )

    assert result == ["test_file1.py", "test_file2.py"]
    # Strategy should receive all changed files including uv.lock
    call_args = mock_strategy.find_impacted_tests.call_args
    assert "uv.lock" in call_args.kwargs["changed_files"]
    assert "src/module.py" in call_args.kwargs["changed_files"]
