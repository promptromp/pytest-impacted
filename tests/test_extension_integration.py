"""Integration tests for the extension system using pytester."""

import importlib
from importlib.metadata import entry_points as installed_entry_points  # before any test patches it
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import networkx as nx
from click.testing import CliRunner

from pytest_impacted import cli as cli_module, resolve_impacted_tests
from pytest_impacted.cli import impacted_tests_cli
from pytest_impacted.extensions import ConfigOption, clear_extension_cache
from pytest_impacted.plugin import pytest_report_header
from pytest_impacted.strategies import CompositeImpactStrategy, ImpactStrategy

from .git_helpers import entry_point


class SimpleTestStrategy(ImpactStrategy):
    """Test strategy for integration tests."""

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return ["tests.test_simple"]


class TestExtensionPluginIntegration:
    """Test that extensions integrate correctly with the pytest plugin."""

    def test_header_format_without_extensions(self, pytester):
        """Report header should show standard fields when no extensions are installed."""
        pytester.makepyfile(test_dummy="def test_pass(): pass")

        result = pytester.runpytest("-v")
        result.stdout.fnmatch_lines(["*pytest-impacted:*backend=*"])

    def test_extensions_shown_in_report_header_unit(self, entry_points):
        """Extension names should appear in the header via pytest_report_header."""
        entry_points.return_value = [entry_point("my_ext", SimpleTestStrategy)]

        config = MagicMock()
        config.getoption.return_value = None
        config.getini.return_value = None

        header = pytest_report_header(config)
        assert any("extensions=my_ext" in line for line in header)

    def test_disable_ext_option_registered(self, entry_points, pytester):
        """The --impacted-disable-ext option should be available."""
        entry_points.return_value = []

        pytester.makepyfile(test_dummy="def test_pass(): pass")

        result = pytester.runpytest("--help")
        result.stdout.fnmatch_lines(["*--impacted-disable-ext*"])

    def test_extension_config_options_registered(self, entry_points, pytester):
        """Extension config options should appear as CLI flags."""

        class ConfiguredStrategy(ImpactStrategy):
            config_options: ClassVar[list[ConfigOption]] = [
                ConfigOption(name="my_flag", help="A test flag", type=str, default="default_val"),
            ]

            def find_impacted_tests(self, *args, **kwargs):
                return []

        entry_points.return_value = [entry_point("configured", ConfiguredStrategy)]

        pytester.makepyfile(test_dummy="def test_pass(): pass")

        result = pytester.runpytest("--help")
        result.stdout.fnmatch_lines(["*--impacted-ext-configured-my-flag*"])

    def test_no_extensions_header_omits_extensions(self, entry_points, pytester):
        """When no extensions are installed, 'extensions=' should not appear in header."""
        entry_points.return_value = []

        pytester.makepyfile(test_dummy="def test_pass(): pass")

        result = pytester.runpytest("-v")
        assert not any("extensions=" in line for line in result.outlines)


class TestDepTreePassThrough:
    """Test that dep_tree is built once and passed to all strategies."""

    def test_composite_passes_dep_tree_to_all_strategies(self):
        """CompositeImpactStrategy builds dep_tree once and shares it."""
        received_trees: list[nx.DiGraph | None] = []

        class CapturingStrategy(ImpactStrategy):
            def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
                received_trees.append(kwargs.get("dep_tree"))
                return []

        composite = CompositeImpactStrategy([CapturingStrategy(), CapturingStrategy()])

        # Build a dep_tree and pass it explicitly
        graph = nx.DiGraph()
        graph.add_node("mypackage.core")
        composite.find_impacted_tests(
            changed_files=["src/core.py"],
            impacted_modules=["mypackage.core"],
            ns_module="mypackage",
            dep_tree=graph,
        )

        # Both strategies should have received the same dep_tree
        assert len(received_trees) == 2
        assert received_trees[0] is graph
        assert received_trees[1] is graph

    def test_strategy_receives_dep_tree_with_nodes(self):
        """A strategy can use the dep_tree to query the dependency graph."""

        class CustomStrategy(ImpactStrategy):
            def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
                dep_tree = kwargs.get("dep_tree")
                if dep_tree is None:
                    return []
                # Use the public resolve_impacted_tests utility
                return resolve_impacted_tests(impacted_modules, dep_tree)

        # Build a realistic dep graph (inverted import direction):
        # core is depended on by api, api is depended on by test_api
        graph = nx.DiGraph()
        graph.add_edge("mypackage.core", "mypackage.api")
        graph.add_edge("mypackage.api", "tests.test_api")

        strategy = CustomStrategy()
        result = strategy.find_impacted_tests(
            changed_files=["mypackage/core.py"],
            impacted_modules=["mypackage.core"],
            ns_module="mypackage",
            dep_tree=graph,
        )

        # Changing core should impact test_api (via api -> core dependency chain)
        assert "tests.test_api" in result

    def test_resolve_impacted_tests_importable_from_package(self):
        """resolve_impacted_tests should be importable from pytest_impacted."""
        graph = nx.DiGraph()
        graph.add_edge("mypackage.foo", "tests.test_foo")
        result = resolve_impacted_tests(["mypackage.foo"], graph)
        assert "tests.test_foo" in result


class TestExtensionCLIIntegration:
    """Test that extensions integrate correctly with the Click CLI."""

    def test_cli_disable_ext_option(self, entry_points, tmp_path, monkeypatch):
        """The --disable-ext option should be accepted by the CLI."""
        entry_points.return_value = []

        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        Path("test_ns").mkdir()
        with patch("pytest_impacted.cli.get_impacted_tests") as mock_get:
            mock_get.return_value = ["tests/test_a.py"]
            result = runner.invoke(
                impacted_tests_cli,
                ["--module", "test_ns", "--disable-ext", "some_ext"],
            )
            assert result.exit_code == 0


class FlaggedStrategy(ImpactStrategy):
    """An extension with a string setting and an on/off one."""

    config_options: ClassVar[list[ConfigOption]] = [
        ConfigOption(name="label", help="A label", type=str, default="none"),
        ConfigOption(name="strict", help="An on/off setting", type=bool, default=False),
    ]

    def find_impacted_tests(self, *args, **kwargs):
        return []


class TestAnExtensionsSettings:
    """Its settings reach it, from pytest and from the CLI."""

    def test_an_on_off_setting_is_a_flag(self, entry_points, pytester):
        """``--impacted-ext-flagged-strict`` alone turns it on, as ``store_true`` does; no value follows it."""
        entry_points.return_value = [entry_point("flagged", FlaggedStrategy)]

        config = pytester.parseconfig("--impacted-ext-flagged-strict", "--impacted-ext-flagged-label=x")

        assert config.getoption("impacted_ext_flagged_strict") is True
        assert config.getoption("impacted_ext_flagged_label") == "x"

    def test_the_cli_registers_them_and_hands_their_values_over(self, entry_points, tmp_path):
        """``impacted-tests`` registers each extension's settings when ``pytest_impacted.cli`` is imported (as an
        install's first import of it does): re-imported here with the extension installed, then without."""
        entry_points.return_value = [entry_point("flagged", FlaggedStrategy)]
        (tmp_path / "pkg").mkdir()
        try:
            command = importlib.reload(cli_module).impacted_tests_cli
            with (
                patch("pytest_impacted.cli.build_strategy_with_extensions") as build,
                patch("pytest_impacted.cli.get_impacted_tests", return_value=[]),
            ):
                args = ["--root-dir", str(tmp_path), "--module", "pkg", "--impacted-ext-flagged-label", "x"]
                result = CliRunner().invoke(command, args)
        finally:
            # Re-imported as the process had it: the extensions really installed, discovered afresh.
            entry_points.side_effect = installed_entry_points
            clear_extension_cache()
            importlib.reload(cli_module)

        assert result.exit_code == 0, result.output
        assert build.call_args.kwargs["ext_config"]["impacted_ext_flagged_label"] == "x"
