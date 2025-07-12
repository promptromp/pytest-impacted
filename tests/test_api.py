"""Unit-tests for the api module."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from pytest_impacted.api import get_impacted_tests, matches_impacted_tests
from pytest_impacted.git import GitMode


def test_matches_impacted_tests_positive_match():
    item_path = "tests/test_example.py"
    impacted_tests = [
        "project/module/tests/test_example.py",
        "project/another_module/tests/test_other.py",
    ]
    assert matches_impacted_tests(item_path, impacted_tests=impacted_tests) is True


def test_matches_impacted_tests_no_match():
    item_path = "tests/test_another.py"
    impacted_tests = [
        "project/module/tests/test_example.py",
        "project/another_module/tests/test_other.py",
    ]
    assert matches_impacted_tests(item_path, impacted_tests=impacted_tests) is False


def test_matches_impacted_tests_empty_impacted_list():
    item_path = "tests/test_example.py"
    impacted_tests = []
    assert matches_impacted_tests(item_path, impacted_tests=impacted_tests) is False


def test_matches_impacted_tests_exact_match():
    item_path = "project/module/tests/test_example.py"
    impacted_tests = ["project/module/tests/test_example.py"]
    assert matches_impacted_tests(item_path, impacted_tests=impacted_tests) is True


def test_matches_impacted_tests_substring_not_suffix():
    item_path = "test_example.py"  # item_path is just 'test_example.py'
    impacted_tests = ["project/module/tests/test_example.pyc"]  # .pyc instead of .py, so not a suffix
    assert not matches_impacted_tests(item_path, impacted_tests=impacted_tests)


def test_matches_impacted_tests_item_path_longer():
    item_path = "longer/path/to/tests/test_example.py"
    impacted_tests = ["tests/test_example.py"]  # impacted_tests is shorter
    assert matches_impacted_tests(item_path, impacted_tests=impacted_tests) is False


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
@patch("pytest_impacted.api.path_to_package_name")
def test_get_impacted_tests_success_with_tests_dir(
    mock_path_to_package_name,
    mock_resolve_modules_to_files,
    mock_resolve_files_to_modules,
    mock_find_impacted_files,
):
    """Test get_impacted_tests successful path with tests_dir."""
    # Setup mocks
    mock_find_impacted_files.return_value = ["file1.py", "file2.py"]
    mock_resolve_files_to_modules.return_value = ["module1", "module2"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py", "test_file2.py"]
    mock_path_to_package_name.return_value = "tests"

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
    mock_path_to_package_name.assert_called_once_with("tests")


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
@patch("pytest_impacted.api.path_to_package_name")
@patch("pytest_impacted.api.logging.debug")
def test_get_impacted_tests_with_tests_dir_logging_debug(
    mock_logging_debug,
    mock_path_to_package_name,
    mock_find_impacted_files,
):
    """Test get_impacted_tests logs debug message when adding tests_dir parent to sys.path."""
    mock_find_impacted_files.return_value = []
    mock_path_to_package_name.return_value = "tests"

    tests_dir = "/some/path/tests"
    expected_parent = str(Path(tests_dir).resolve().parent)

    # Ensure the parent path is not already in sys.path
    if expected_parent in sys.path:
        sys.path.remove(expected_parent)

    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir=tests_dir,
    )

    # Check that debug logging was called
    mock_logging_debug.assert_called_once_with(
        "Adding tests_dir parent directory to sys.path: %s", Path(expected_parent)
    )

    # Clean up
    if expected_parent in sys.path:
        sys.path.remove(expected_parent)


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.path_to_package_name")
def test_get_impacted_tests_with_tests_dir_sys_path_modification(
    mock_path_to_package_name,
    mock_find_impacted_files,
):
    """Test get_impacted_tests modifies sys.path when tests_dir is provided."""
    mock_find_impacted_files.return_value = []
    mock_path_to_package_name.return_value = "tests"

    tests_dir = "/some/path/tests"
    expected_parent = str(Path(tests_dir).resolve().parent)

    # Ensure the parent path is not already in sys.path
    if expected_parent in sys.path:
        sys.path.remove(expected_parent)

    original_path_length = len(sys.path)

    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir=tests_dir,
    )

    # Check that sys.path was modified
    assert len(sys.path) == original_path_length + 1
    assert sys.path[0] == expected_parent

    # Clean up
    sys.path.remove(expected_parent)


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.path_to_package_name")
def test_get_impacted_tests_with_tests_dir_sys_path_already_present(
    mock_path_to_package_name,
    mock_find_impacted_files,
):
    """Test get_impacted_tests doesn't duplicate sys.path when tests_dir parent is already present."""
    mock_find_impacted_files.return_value = []
    mock_path_to_package_name.return_value = "tests"

    tests_dir = "/some/path/tests"
    expected_parent = str(Path(tests_dir).resolve().parent)

    # Add the parent path to sys.path first
    sys.path.insert(0, expected_parent)
    original_path_length = len(sys.path)

    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir=tests_dir,
    )

    # Check that sys.path wasn't modified further
    assert len(sys.path) == original_path_length

    # Clean up
    sys.path.remove(expected_parent)
