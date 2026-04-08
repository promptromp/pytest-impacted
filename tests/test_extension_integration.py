"""Integration tests for the extension system using pytester."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from pytest_impacted.cli import impacted_tests_cli
from pytest_impacted.extensions import ConfigOption, clear_extension_cache
from pytest_impacted.plugin import pytest_report_header
from pytest_impacted.strategies import ImpactStrategy


class SimpleTestStrategy(ImpactStrategy):
    """Test strategy for integration tests."""

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return ["tests.test_simple"]


def _make_mock_entry_point(name, cls):
    ep = MagicMock()
    ep.name = name
    ep.value = f"{cls.__module__}:{cls.__qualname__}"
    ep.load.return_value = cls
    return ep


class TestExtensionPluginIntegration:
    """Test that extensions integrate correctly with the pytest plugin."""

    def setup_method(self):
        clear_extension_cache()

    def teardown_method(self):
        clear_extension_cache()

    def test_header_format_without_extensions(self, pytester):
        """Report header should show standard fields when no extensions are installed."""
        pytester.makepyfile(test_dummy="def test_pass(): pass")

        result = pytester.runpytest("-v")
        result.stdout.fnmatch_lines(["*pytest-impacted:*backend=*"])

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_extensions_shown_in_report_header_unit(self, mock_entry_points):
        """Extension names should appear in the header via pytest_report_header."""
        mock_entry_points.return_value = [_make_mock_entry_point("my_ext", SimpleTestStrategy)]
        clear_extension_cache()

        config = MagicMock()
        config.getoption.return_value = None
        config.getini.return_value = None

        header = pytest_report_header(config)
        assert any("extensions=my_ext" in line for line in header)

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_disable_ext_option_registered(self, mock_entry_points, pytester):
        """The --impacted-disable-ext option should be available."""
        mock_entry_points.return_value = []
        clear_extension_cache()

        pytester.makepyfile(test_dummy="def test_pass(): pass")

        result = pytester.runpytest("--help")
        result.stdout.fnmatch_lines(["*--impacted-disable-ext*"])

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_extension_config_options_registered(self, mock_entry_points, pytester):
        """Extension config options should appear as CLI flags."""

        class ConfiguredStrategy(ImpactStrategy):
            config_options = [
                ConfigOption(name="my_flag", help="A test flag", type=str, default="default_val"),
            ]

            def find_impacted_tests(self, *args, **kwargs):
                return []

        mock_entry_points.return_value = [_make_mock_entry_point("configured", ConfiguredStrategy)]
        clear_extension_cache()

        pytester.makepyfile(test_dummy="def test_pass(): pass")

        result = pytester.runpytest("--help")
        result.stdout.fnmatch_lines(["*--impacted-ext-configured-my-flag*"])

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_no_extensions_header_omits_extensions(self, mock_entry_points, pytester):
        """When no extensions are installed, 'extensions=' should not appear in header."""
        mock_entry_points.return_value = []
        clear_extension_cache()

        pytester.makepyfile(test_dummy="def test_pass(): pass")

        result = pytester.runpytest("-v")
        assert not any("extensions=" in line for line in result.outlines)


class TestExtensionCLIIntegration:
    """Test that extensions integrate correctly with the Click CLI."""

    def setup_method(self):
        clear_extension_cache()

    def teardown_method(self):
        clear_extension_cache()

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_cli_disable_ext_option(self, mock_entry_points):
        """The --disable-ext option should be accepted by the CLI."""
        mock_entry_points.return_value = []
        clear_extension_cache()

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("test_ns").mkdir()
            with patch("pytest_impacted.cli.get_impacted_tests") as mock_get:
                mock_get.return_value = ["tests/test_a.py"]
                result = runner.invoke(
                    impacted_tests_cli,
                    ["--module", "test_ns", "--disable-ext", "some_ext"],
                )
                assert result.exit_code == 0
