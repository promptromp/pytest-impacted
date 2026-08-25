"""Unit-tests for the user-configurable file invalidation strategy."""

import logging
from pathlib import Path

import networkx as nx
import pytest

from pytest_impacted.strategies import (
    DependencyFileImpactStrategy,
    InvalidationFileImpactStrategy,
    find_test_modules_under,
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
        ],
    )
    def test_patterns(self, file_path, glob_pattern, expected):
        assert matches_any_glob(file_path, (glob_pattern,)) is expected

    def test_empty_patterns_never_match(self):
        assert matches_any_glob("anything.json", ()) is False


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tiny on-disk layout so directory-scoped matching can resolve test module paths."""
    for rel in ("tests/unit/test_a.py", "tests/unit/deep/test_b.py", "tests/integration/test_c.py"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    (tmp_path / "tests" / "unit" / "fixtures").mkdir()
    return tmp_path


@pytest.fixture
def dep_tree() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(["pkg.core", "tests.unit.test_a", "tests.unit.deep.test_b", "tests.integration.test_c"])
    return graph


class TestFindTestModulesUnder:
    def test_returns_tests_in_dir_and_below(self, project, dep_tree):
        result = find_test_modules_under(project / "tests" / "unit", dep_tree, root_dir=project)
        assert result == ["tests.unit.deep.test_b", "tests.unit.test_a"]

    def test_root_returns_everything(self, project, dep_tree):
        result = find_test_modules_under(project, dep_tree, root_dir=project)
        assert result == ["tests.integration.test_c", "tests.unit.deep.test_b", "tests.unit.test_a"]

    def test_unrelated_dir_returns_nothing(self, project, dep_tree):
        assert find_test_modules_under(project / "docs", dep_tree, root_dir=project) == []


class TestInvalidationFileImpactStrategy:
    def _run(self, strategy, changed_files, dep_tree, root_dir=None):
        return strategy.find_impacted_tests(
            changed_files=changed_files,
            impacted_modules=[],
            ns_module="pkg",
            tests_package="tests",
            root_dir=root_dir,
            dep_tree=dep_tree,
        )

    def test_no_patterns_is_a_no_op(self, dep_tree):
        strategy = InvalidationFileImpactStrategy()
        assert self._run(strategy, ["config/settings.json", "uv.lock"], dep_tree) == []

    def test_all_pattern_returns_every_test_module(self, dep_tree):
        strategy = InvalidationFileImpactStrategy(all_patterns=("*.json",))
        result = self._run(strategy, ["config/settings.json"], dep_tree)
        assert result == ["tests.integration.test_c", "tests.unit.deep.test_b", "tests.unit.test_a"]

    def test_all_pattern_no_match_returns_nothing(self, dep_tree):
        strategy = InvalidationFileImpactStrategy(all_patterns=("*.json",))
        assert self._run(strategy, ["config/settings.yaml", "pkg/core.py"], dep_tree) == []

    def test_dir_pattern_returns_tests_under_changed_file_directory(self, project, dep_tree):
        strategy = InvalidationFileImpactStrategy(dir_patterns=("*.json",))
        result = self._run(strategy, ["tests/unit/fixtures/data.json"], dep_tree, root_dir=project)
        # fixtures/ itself has no tests; nothing above it is included.
        assert result == []

        result = self._run(strategy, ["tests/unit/data.json"], dep_tree, root_dir=project)
        assert result == ["tests.unit.deep.test_b", "tests.unit.test_a"]

    def test_dir_pattern_accepts_absolute_changed_paths(self, project, dep_tree):
        strategy = InvalidationFileImpactStrategy(dir_patterns=("*.json",))
        result = self._run(strategy, [str(project / "tests" / "integration" / "x.json")], dep_tree, root_dir=project)
        assert result == ["tests.integration.test_c"]

    def test_dir_pattern_without_root_dir_returns_nothing(self, dep_tree):
        strategy = InvalidationFileImpactStrategy(dir_patterns=("*.json",))
        assert self._run(strategy, ["tests/unit/data.json"], dep_tree, root_dir=None) == []

    def test_all_and_dir_results_are_unioned_and_sorted(self, project, dep_tree):
        strategy = InvalidationFileImpactStrategy(all_patterns=("*.lock",), dir_patterns=("*.json",))
        result = self._run(strategy, ["tests/unit/data.json", "custom.lock"], dep_tree, root_dir=project)
        assert result == ["tests.integration.test_c", "tests.unit.deep.test_b", "tests.unit.test_a"]

    def test_notifies_which_files_triggered(self, project, dep_tree, caplog):
        strategy = InvalidationFileImpactStrategy(all_patterns=("*.lock",), dir_patterns=("*.json",))
        with caplog.at_level(logging.INFO, logger="pytest_impacted.display"):
            self._run(strategy, ["custom.lock", "tests/unit/data.json"], dep_tree, root_dir=project)
        assert "custom.lock" in caplog.text
        assert "--impacted-invalidate-all" in caplog.text
        assert "tests/unit/data.json" in caplog.text
        assert "--impacted-invalidate-dir" in caplog.text


class TestGetDefaultStrategies:
    def test_not_included_without_patterns(self):
        strategies = get_default_strategies()
        assert not any(isinstance(s, InvalidationFileImpactStrategy) for s in strategies)

    def test_included_after_dep_file_strategy_when_configured(self):
        strategies = get_default_strategies(invalidate_all_patterns=["*.json"])
        assert isinstance(strategies[-2], DependencyFileImpactStrategy)
        strategy = strategies[-1]
        assert isinstance(strategy, InvalidationFileImpactStrategy)
        assert strategy.all_patterns == ("*.json",)
        assert strategy.dir_patterns == ()

    def test_dir_patterns_alone_are_enough(self):
        strategy = get_default_strategies(invalidate_dir_patterns=("*.yaml",))[-1]
        assert isinstance(strategy, InvalidationFileImpactStrategy)
        assert strategy.dir_patterns == ("*.yaml",)

    def test_independent_of_dep_file_switch(self):
        strategies = get_default_strategies(watch_dep_files=False, invalidate_all_patterns=["*.json"])
        assert not any(isinstance(s, DependencyFileImpactStrategy) for s in strategies)
        assert isinstance(strategies[-1], InvalidationFileImpactStrategy)
