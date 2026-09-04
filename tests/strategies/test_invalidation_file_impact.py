"""Unit-tests for the user-configurable file invalidation strategy."""

import logging

import networkx as nx
import pytest

from pytest_impacted.strategies import (
    DependencyFileImpactStrategy,
    InvalidationFileImpactStrategy,
    get_default_strategies,
    matches_any_glob,
)


class TestMatchesAnyGlob:
    """Test the shared glob matcher used for user-supplied patterns."""

    @pytest.mark.parametrize(
        ("file_path", "glob_pattern", "expected"),
        [
            pytest.param("settings.json", "*.json", True, id="extension_glob_at_root"),
            pytest.param("config/nested/settings.json", "*.json", True, id="extension_glob_nested"),
            pytest.param("config/settings.json", "config/*.json", True, id="dir_scoped_glob"),
            pytest.param("other/settings.json", "config/*.json", False, id="dir_scoped_glob_wrong_dir"),
            pytest.param("fixtures/a/data.yaml", "fixtures/*/*.yaml", True, id="two_level_glob"),
            pytest.param("fixtures/a/b/data.yaml", "fixtures/*/*.yaml", False, id="star_spans_one_segment"),
            pytest.param("schema.graphql", "schema.graphql", True, id="bare_filename"),
            pytest.param("src/schema.graphql", "schema.graphql", True, id="bare_filename_matches_basename"),
            pytest.param("settings.json5", "*.json", False, id="no_partial_match"),
            # PurePath.match treats "**" as a plain "*" — it is NOT recursive. Documented
            # in docs/usage.md; pinned here so a matcher change cannot silently break it.
            pytest.param("config/a/x.yaml", "config/**/*.yaml", True, id="double_star_is_one_segment"),
            pytest.param("config/a/b/x.yaml", "config/**/*.yaml", False, id="double_star_does_not_recurse"),
        ],
    )
    def test_patterns(self, file_path, glob_pattern, expected):
        assert matches_any_glob(file_path, (glob_pattern,)) is expected

    def test_empty_patterns_never_match(self):
        assert matches_any_glob("anything.json", ()) is False


@pytest.fixture
def dep_tree() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(["pkg.core", "tests.unit.test_a", "tests.unit.deep.test_b", "tests.integration.test_c"])
    return graph


class TestInvalidationFileImpactStrategy:
    def _run(self, strategy, changed_files, dep_tree):
        return strategy.find_impacted_tests(
            changed_files=changed_files,
            impacted_modules=[],
            ns_module="pkg",
            tests_package="tests",
            root_dir=None,
            dep_tree=dep_tree,
        )

    def test_no_patterns_is_a_no_op(self, dep_tree):
        strategy = InvalidationFileImpactStrategy()
        assert self._run(strategy, ["config/settings.json", "uv.lock"], dep_tree) == []

    def test_match_returns_every_test_module(self, dep_tree):
        strategy = InvalidationFileImpactStrategy(("*.json",))
        result = self._run(strategy, ["config/settings.json"], dep_tree)
        assert result == ["tests.integration.test_c", "tests.unit.deep.test_b", "tests.unit.test_a"]

    def test_no_match_returns_nothing(self, dep_tree):
        strategy = InvalidationFileImpactStrategy(("*.json",))
        assert self._run(strategy, ["config/settings.yaml", "pkg/core.py"], dep_tree) == []

    def test_any_of_several_patterns_may_match(self, dep_tree):
        strategy = InvalidationFileImpactStrategy(("*.lock", "config/*.yaml"))
        every_test = ["tests.integration.test_c", "tests.unit.deep.test_b", "tests.unit.test_a"]
        assert self._run(strategy, ["config/settings.yaml"], dep_tree) == every_test
        assert self._run(strategy, ["custom.lock"], dep_tree) == every_test

    def test_notifies_which_files_triggered(self, dep_tree, caplog):
        strategy = InvalidationFileImpactStrategy(("*.lock",))
        with caplog.at_level(logging.INFO, logger="pytest_impacted.display"):
            self._run(strategy, ["custom.lock", "pkg/core.py"], dep_tree)
        assert "custom.lock" in caplog.text
        assert "pkg/core.py" not in caplog.text
        assert "--impacted-invalidate-all" in caplog.text


class TestGetDefaultStrategiesWithInvalidation:
    def test_not_included_without_patterns(self):
        strategies = get_default_strategies()
        assert not any(isinstance(s, InvalidationFileImpactStrategy) for s in strategies)

    def test_included_after_dep_file_strategy_when_configured(self):
        strategies = get_default_strategies(invalidate_all_patterns=["*.json"])
        assert isinstance(strategies[-2], DependencyFileImpactStrategy)
        strategy = strategies[-1]
        assert isinstance(strategy, InvalidationFileImpactStrategy)
        assert strategy.patterns == ("*.json",)

    def test_independent_of_dep_file_switch(self):
        strategies = get_default_strategies(watch_dep_files=False, invalidate_all_patterns=["*.json"])
        assert not any(isinstance(s, DependencyFileImpactStrategy) for s in strategies)
        assert isinstance(strategies[-1], InvalidationFileImpactStrategy)
