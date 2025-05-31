"""Unit tests for the CLI module."""

import logging
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from pytest_impacted.cli import configure_logging, impacted_tests_cli
from pytest_impacted.git import GitMode


class TestConfigureLogging:
    """Tests for the configure_logging function."""

    def test_configure_logging_verbose_true(self):
        """Test that verbose=True sets DEBUG level."""
        with patch("logging.basicConfig") as mock_basic_config:
            configure_logging(verbose=True)

            mock_basic_config.assert_called_once()
            call_args = mock_basic_config.call_args
            assert call_args[1]["level"] == logging.DEBUG

    def test_configure_logging_verbose_false(self):
        """Test that verbose=False sets INFO level."""
        with patch("logging.basicConfig") as mock_basic_config:
            configure_logging(verbose=False)

            mock_basic_config.assert_called_once()
            call_args = mock_basic_config.call_args
            assert call_args[1]["level"] == logging.INFO

    def test_configure_logging_format(self):
        """Test that logging is configured with the expected format."""
        with patch("logging.basicConfig") as mock_basic_config:
            configure_logging(verbose=False)

            mock_basic_config.assert_called_once()
            call_args = mock_basic_config.call_args
            assert call_args[1]["format"] == "%(funcName)-20s | %(message)s"
            assert call_args[1]["datefmt"] == "[%x]"


class TestImpactedTestsCLI:
    """Tests for the impacted_tests_cli Click command."""

    def setup_method(self):
        """Set up test environment."""
        self.runner = CliRunner()

    @patch("pytest_impacted.cli.get_impacted_tests")
    @patch("pytest_impacted.cli.configure_logging")
    def test_cli_with_all_required_args(self, mock_configure_logging, mock_get_impacted_tests):
        """Test CLI with all required arguments."""
        mock_get_impacted_tests.return_value = ["tests/test_example.py", "tests/test_other.py"]

        with self.runner.isolated_filesystem():
            # Create test directories
            Path("test_ns").mkdir()

            result = self.runner.invoke(
                impacted_tests_cli,
                [
                    "--git-mode",
                    "unstaged",
                    "--base-branch",
                    "main",
                    "--root-dir",
                    ".",
                    "--module",
                    "test_ns",
                    "--verbose",
                ],
            )

            assert result.exit_code == 0
            mock_configure_logging.assert_called_once_with(verbose=True)
            mock_get_impacted_tests.assert_called_once_with(
                impacted_git_mode=GitMode.UNSTAGED,
                impacted_base_branch="main",
                root_dir=".",
                ns_module="test_ns",
                tests_dir=None,
            )

            # Check that the impacted tests are printed to stdout
            assert "tests/test_example.py" in result.output
            assert "tests/test_other.py" in result.output

    @patch("pytest_impacted.cli.get_impacted_tests")
    @patch("pytest_impacted.cli.configure_logging")
    def test_cli_with_tests_dir(self, mock_configure_logging, mock_get_impacted_tests):
        """Test CLI with tests-dir specified."""
        mock_get_impacted_tests.return_value = ["tests/test_example.py"]

        with self.runner.isolated_filesystem():
            # Create test directories
            Path("test_ns").mkdir()
            Path("tests").mkdir()

            result = self.runner.invoke(
                impacted_tests_cli,
                [
                    "--git-mode",
                    "branch",
                    "--base-branch",
                    "develop",
                    "--root-dir",
                    ".",
                    "--module",
                    "test_ns",
                    "--tests-dir",
                    "tests",
                ],
            )

            assert result.exit_code == 0
            mock_get_impacted_tests.assert_called_once_with(
                impacted_git_mode=GitMode.BRANCH,
                impacted_base_branch="develop",
                root_dir=".",
                ns_module="test_ns",
                tests_dir="tests",
            )

    @patch("pytest_impacted.cli.get_impacted_tests")
    @patch("pytest_impacted.cli.configure_logging")
    def test_cli_no_impacted_tests(self, mock_configure_logging, mock_get_impacted_tests):
        """Test CLI when no impacted tests are found."""
        mock_get_impacted_tests.return_value = None

        with self.runner.isolated_filesystem():
            Path("test_ns").mkdir()

            result = self.runner.invoke(
                impacted_tests_cli,
                ["--git-mode", "unstaged", "--base-branch", "main", "--root-dir", ".", "--module", "test_ns"],
            )

            assert result.exit_code == 0
            assert "No impacted tests found." in result.output

    @patch("pytest_impacted.cli.get_impacted_tests")
    @patch("pytest_impacted.cli.configure_logging")
    def test_cli_empty_impacted_tests_list(self, mock_configure_logging, mock_get_impacted_tests):
        """Test CLI when empty list of impacted tests is returned."""
        mock_get_impacted_tests.return_value = []

        with self.runner.isolated_filesystem():
            Path("test_ns").mkdir()

            result = self.runner.invoke(
                impacted_tests_cli,
                ["--git-mode", "unstaged", "--base-branch", "main", "--root-dir", ".", "--module", "test_ns"],
            )

            assert result.exit_code == 0
            assert "No impacted tests found." in result.output

    def test_cli_missing_required_arg(self):
        """Test CLI fails when required argument is missing."""
        result = self.runner.invoke(
            impacted_tests_cli,
            [
                "--git-mode",
                "unstaged",
                "--base-branch",
                "main",
                "--root-dir",
                ".",
                # Missing --module
            ],
        )

        assert result.exit_code != 0
        assert "Missing option" in result.output

    def test_cli_invalid_git_mode(self):
        """Test CLI fails with invalid git mode."""
        with self.runner.isolated_filesystem():
            Path("test_ns").mkdir()

            result = self.runner.invoke(
                impacted_tests_cli,
                ["--git-mode", "invalid_mode", "--base-branch", "main", "--root-dir", ".", "--module", "test_ns"],
            )

            assert result.exit_code != 0
            # The invalid git mode should cause an error during execution
            assert result.exception is not None

    def test_cli_nonexistent_root_dir(self):
        """Test CLI fails with non-existent root directory."""
        result = self.runner.invoke(
            impacted_tests_cli,
            [
                "--git-mode",
                "unstaged",
                "--base-branch",
                "main",
                "--root-dir",
                "/nonexistent/path",
                "--module",
                "test_ns",
            ],
        )

        assert result.exit_code != 0

    def test_cli_nonexistent_module(self):
        """Test CLI fails with non-existent namespace module."""
        result = self.runner.invoke(
            impacted_tests_cli,
            [
                "--git-mode",
                "unstaged",
                "--base-branch",
                "main",
                "--root-dir",
                ".",
                "--module",
                "/nonexistent/module",
            ],
        )

        assert result.exit_code != 0

    def test_cli_nonexistent_tests_dir(self):
        """Test CLI fails with non-existent tests directory."""
        with self.runner.isolated_filesystem():
            Path("test_ns").mkdir()

            result = self.runner.invoke(
                impacted_tests_cli,
                [
                    "--git-mode",
                    "unstaged",
                    "--base-branch",
                    "main",
                    "--root-dir",
                    ".",
                    "--module",
                    "test_ns",
                    "--tests-dir",
                    "/nonexistent/tests",
                ],
            )

            assert result.exit_code != 0

    @patch("pytest_impacted.cli.get_impacted_tests")
    @patch("pytest_impacted.cli.configure_logging")
    def test_cli_defaults(self, mock_configure_logging, mock_get_impacted_tests):
        """Test CLI with default values."""
        mock_get_impacted_tests.return_value = ["tests/test_example.py"]

        with self.runner.isolated_filesystem():
            Path("test_ns").mkdir()

            result = self.runner.invoke(impacted_tests_cli, ["--module", "test_ns"])

            assert result.exit_code == 0
            mock_configure_logging.assert_called_once_with(verbose=False)
            mock_get_impacted_tests.assert_called_once_with(
                impacted_git_mode=GitMode.UNSTAGED,  # default
                impacted_base_branch="main",  # default
                root_dir=".",  # default
                ns_module="test_ns",
                tests_dir=None,  # default
            )

    @patch("pytest_impacted.cli.get_impacted_tests")
    @patch("pytest_impacted.cli.configure_logging")
    def test_cli_stderr_output(self, mock_configure_logging, mock_get_impacted_tests):
        """Test that CLI outputs configuration info to stderr."""
        mock_get_impacted_tests.return_value = ["tests/test_example.py"]

        with self.runner.isolated_filesystem():
            Path("test_ns").mkdir()
            Path("tests").mkdir()  # Create the tests directory

            result = self.runner.invoke(
                impacted_tests_cli,
                [
                    "--git-mode",
                    "branch",
                    "--base-branch",
                    "develop",
                    "--root-dir",
                    ".",
                    "--module",
                    "test_ns",
                    "--tests-dir",
                    "tests",
                ],
            )

            # Check that configuration is shown in output
            assert result.exit_code == 0
            assert "impacted-tests" in result.output
            assert "git-mode: branch" in result.output
            assert "base-branch: develop" in result.output
            assert "module: test_ns" in result.output
