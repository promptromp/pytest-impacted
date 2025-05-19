import pytest
from _pytest.config.argparsing import Parser
from pytest_impacted.plugin import (
    pytest_addoption,
    pytest_configure,
    pytest_report_header,
    validate_config,
)
from pytest_impacted.git import GitMode


def test_pytest_addoption():
    """Test that the plugin adds the correct command line options."""
    parser = Parser()
    pytest_addoption(parser)

    # Check that the impacted group exists
    group = parser.getgroup("impacted")
    assert group is not None

    # Check that all options are added
    options = {opt.dest for opt in group.options}
    expected_options = {
        "impacted",
        "impacted_module",
        "impacted_git_mode",
        "impacted_base_branch",
        "impacted_tests_dir",
    }
    assert options == expected_options


def test_pytest_configure(pytestconfig):
    """Test that the plugin configures correctly."""
    pytest_configure(pytestconfig)

    # Check that the marker is added
    markers = pytestconfig.getini("markers")
    assert (
        "impacted(state): mark test as impacted by the state of the git repository"
        in markers
    )


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
    pytestconfig.option.impacted_module = "test_module"
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
    pytestconfig.option.impacted_module = "test_module"
    pytestconfig.option.impacted_git_mode = GitMode.BRANCH
    pytestconfig.option.impacted_base_branch = None
    pytestconfig._inicache["impacted_base_branch"] = None
    with pytest.raises(pytest.UsageError, match="No base branch specified"):
        validate_config(pytestconfig)
