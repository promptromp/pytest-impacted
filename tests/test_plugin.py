from unittest.mock import MagicMock

import pytest

from pytest_impacted.git import GitMode
from pytest_impacted.plugin import (
    _validate_base_branch,
    _validate_module,
    _validate_tests_dir,
    pytest_addoption,
    pytest_configure,
    pytest_report_header,
    validate_config,
)


@pytest.fixture
def cli_options():
    return [
        "impacted",
        "impacted_module",
        "impacted_git_mode",
        "impacted_base_branch",
        "impacted_tests_dir",
    ]


def test_pytest_addoption(cli_options):
    """Test that the plugin adds the correct command line options."""
    # Create mock options with the necessary attributes
    mock_options = []
    for option_name in cli_options:
        mock_option = MagicMock()
        mock_option.dest = option_name
        mock_options.append(mock_option)

    # Create a mock group that will return our mock options
    mock_group = MagicMock()
    mock_group.options = mock_options
    mock_group.addoption = MagicMock()

    # Create a mock parser that will return our mock group
    mock_parser = MagicMock()
    mock_parser.getgroup.return_value = mock_group
    mock_parser.addini = MagicMock()

    # Call the function with our mock parser
    pytest_addoption(mock_parser)

    # Verify the impacted group was requested
    mock_parser.getgroup.assert_called_once_with("impacted")
    assert mock_group is not None

    # Check that all options were added
    options = {opt.dest for opt in mock_group.options}
    assert options == set(cli_options)


def test_pytest_configure(pytestconfig):
    """Test that the plugin configures correctly."""
    pytest_configure(pytestconfig)

    # Check that the marker is added
    markers = pytestconfig.getini("markers")
    assert "impacted(state): mark test as impacted by the state of the git repository" in markers


def test_pytest_report_header(pytestconfig):
    """Test that the plugin adds the correct header information."""
    pytestconfig.option.impacted_module = "test_module"
    pytestconfig.option.impacted_git_mode = GitMode.UNSTAGED
    pytestconfig.option.impacted_base_branch = "main"
    pytestconfig.option.impacted_tests_dir = "tests"

    header = pytest_report_header(pytestconfig)
    assert len(header) == 1
    assert "pytest-impacted:" in header[0]
    assert "impacted_module=test_module" in header[0]
    assert "impacted_git_mode=unstaged" in header[0]
    assert "impacted_base_branch=main" in header[0]
    assert "impacted_tests_dir=tests" in header[0]


def test_validate_config_valid(pytestconfig):
    """Test that valid configuration passes validation."""
    pytestconfig.option.impacted = True
    pytestconfig.option.impacted_module = "pytest_impacted"
    pytestconfig.option.impacted_git_mode = GitMode.UNSTAGED
    validate_config(pytestconfig)  # Should not raise


def test_validate_config_missing_module(pytestconfig):
    """Test that validation fails when module is missing."""
    pytestconfig.option.impacted = True
    pytestconfig.option.impacted_module = None
    pytestconfig._inicache["impacted_module"] = None
    pytestconfig.option.impacted_git_mode = GitMode.UNSTAGED
    with pytest.raises(pytest.UsageError, match="No module specified"):
        validate_config(pytestconfig)


def test_validate_config_missing_git_mode(pytestconfig):
    """Test that validation fails when git mode is missing."""
    pytestconfig.option.impacted = True
    pytestconfig.option.impacted_module = "test_module"
    pytestconfig.option.impacted_git_mode = None
    pytestconfig._inicache["impacted_git_mode"] = None
    with pytest.raises(pytest.UsageError, match="No git mode specified"):
        validate_config(pytestconfig)


def test_validate_config_branch_mode_missing_base(pytestconfig):
    """Test that validation fails when branch mode is used without base branch."""
    pytestconfig.option.impacted = True
    pytestconfig.option.impacted_module = "pytest_impacted"
    pytestconfig.option.impacted_git_mode = GitMode.BRANCH
    pytestconfig.option.impacted_base_branch = None
    pytestconfig._inicache["impacted_base_branch"] = None
    with pytest.raises(pytest.UsageError, match="No base branch specified"):
        validate_config(pytestconfig)


def test_validate_module_hyphen_suggests_underscore():
    """Test that a hyphenated module name suggests the underscore version."""
    with pytest.raises(pytest.UsageError, match="Did you mean: --impacted-module=pytest_impacted"):
        _validate_module("pytest-impacted")


def test_validate_module_nonexistent():
    """Test that a completely unknown module gives a helpful error."""
    with pytest.raises(pytest.UsageError, match="Module 'doesnotexist' not found"):
        _validate_module("doesnotexist")


def test_validate_module_valid():
    """Test that a valid module name passes validation."""
    _validate_module("pytest_impacted")  # Should not raise


def test_validate_tests_dir_nonexistent():
    """Test that a non-existent tests directory gives a helpful error."""
    with pytest.raises(pytest.UsageError, match="Tests directory 'nonexistent_dir' does not exist"):
        _validate_tests_dir("nonexistent_dir")


def test_validate_tests_dir_valid():
    """Test that a valid tests directory passes validation."""
    _validate_tests_dir("tests")  # Should not raise


def test_validate_base_branch_nonexistent():
    """Test that a non-existent base branch gives a helpful error with available refs."""
    with pytest.raises(pytest.UsageError, match="Base branch 'nonexistent_branch_xyz' does not exist"):
        _validate_base_branch("nonexistent_branch_xyz", ".")


def test_validate_base_branch_valid():
    """Test that a valid base branch passes validation."""
    _validate_base_branch("HEAD", ".")  # Should not raise — HEAD exists in any git checkout
