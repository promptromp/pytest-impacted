"""Integration tests for the extension system using pytester."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx
from click.testing import CliRunner

from pytest_impacted import resolve_impacted_tests
from pytest_impacted.cli import impacted_tests_cli
from pytest_impacted.extensions import ConfigOption, clear_extension_cache
from pytest_impacted.plugin import pytest_report_header
from pytest_impacted.strategies import CompositeImpactStrategy, ImpactStrategy


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
