"""Unit-tests for the api module."""

from pathlib import Path
from unittest.mock import patch

from pytest_impacted.api import matches_impacted_tests, get_impacted_tests
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
    impacted_tests = [
        "project/module/tests/test_example.pyc"
    ]  # .pyc instead of .py, so not a suffix
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
    mock_find_impacted_files.assert_called_once_with(
        Path("."), git_mode=GitMode.UNSTAGED, base_branch="main"
    )
