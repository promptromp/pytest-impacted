"""Unit-tests for the user-configurable file invalidation strategy."""

import logging
from itertools import product
from pathlib import PurePosixPath

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
            # A "**" segment stands for any number of directories; between segments, none too.
            pytest.param("config/a/x.yaml", "config/**/*.yaml", True, id="double_star_one_directory"),
            pytest.param("config/a/b/x.yaml", "config/**/*.yaml", True, id="double_star_recurses"),
            pytest.param("config/x.yaml", "config/**/*.yaml", True, id="double_star_no_directory"),
            pytest.param("other/a/x.yaml", "config/**/*.yaml", False, id="double_star_wrong_directory"),
            pytest.param("config/a/b/x.json", "config/**/*.yaml", False, id="double_star_wrong_name"),
            # At the end, everything inside the directory, at any depth, but not a file of its name.
            pytest.param("docs/a/b/c.md", "docs/**", True, id="trailing_double_star_recurses"),
            pytest.param("docs/a.md", "docs/**", True, id="trailing_double_star_one_level"),
            pytest.param("docs", "docs/**", False, id="trailing_double_star_not_the_directory"),
            # Right-anchored as every other glob: any directory of that name, at any depth.
            pytest.param("site/docs/a/b.md", "docs/**", True, id="double_star_right_anchored"),
            pytest.param("docs/x/y/config/z.yaml", "docs/**/config/*.yaml", True, id="double_star_then_segments"),
            pytest.param("docs/x/config/a/z.yaml", "docs/**/config/*.yaml", False, id="then_star_one_segment"),
            # Inside a segment, "**" is a plain "*" (as in .gitignore): it never spans a "/".
            pytest.param("aXYb/c", "a**b/c", True, id="double_star_in_a_segment_is_a_star"),
            pytest.param("aX/Yb/c", "a**b/c", False, id="double_star_in_a_segment_stays_in_it"),
            pytest.param("aX/Yb/c", "a**b/**", False, id="double_star_in_a_segment_beside_a_recursive_one"),
            pytest.param("a/b/x.json", "**.json", True, id="double_star_prefix_is_a_star"),
            # An absolute glob is anchored at the root too; a file outside the rootdir arrives absolute.
            pytest.param("/abs/x/y/z.json", "/abs/**/*.json", True, id="absolute_double_star_recurses"),
            pytest.param("/other/abs/z.json", "/abs/**", False, id="absolute_double_star_anchored_at_root"),
            pytest.param("abs/x/z.json", "/abs/**", False, id="absolute_glob_never_matches_a_relative_path"),
            pytest.param("q/abs/x.json", "/abs/**", False, id="absolute_glob_never_matches_a_relative_tail"),
            pytest.param("/outside/repo/data/a/b.csv", "data/**", True, id="relative_double_star_on_absolute_path"),
            pytest.param("/x/y", "*/x/**/y", False, id="the_root_is_no_directory"),
            pytest.param("Docs/a/b.md", "docs/**", False, id="double_star_glob_case_sensitive"),
        ],
    )
    def test_patterns(self, file_path, glob_pattern, expected):
        assert matches_any_glob(file_path, (glob_pattern,)) is expected

    def test_empty_patterns_never_match(self):
        assert matches_any_glob("anything.json", ()) is False

    def test_a_glob_without_a_double_star_segment_matches_as_pure_path_match(self):
        """Exactly as before, Python version quirks included: only a ``**`` segment widens."""
        for path, glob in product(PATHS, (glob for glob in GLOBS if "**" not in glob.split("/"))):
            assert matches_any_glob(path, [glob]) is PurePosixPath(path).match(glob), (path, glob)

    def test_a_double_star_only_widens_what_a_glob_matches(self):
        """Every file a glob matched when ``**`` was a plain ``*`` it still matches: more tests, never fewer."""
        for path, glob in product(PATHS, GLOBS):
            if PurePosixPath(path).match(glob):
                assert matches_any_glob(path, [glob]), (path, glob)

    @pytest.mark.skipif(not hasattr(PurePosixPath, "full_match"), reason="PurePath.full_match is new in Python 3.13")
    def test_a_relative_glob_matches_as_full_match_does_any_tail_of_the_path(self):
        """Python 3.13's ``full_match`` has the same ``**``; anchored at the end, a glob matches a path's tail."""
        for path, glob in product((path for path in PATHS if not path.startswith("/")), GLOBS):
            if not glob.startswith("/"):
                expected = PurePosixPath(path).full_match(f"**/{glob}")  # type: ignore[attr-defined]
                assert matches_any_glob(path, [glob]) is expected, (path, glob)

    @pytest.mark.skipif(not hasattr(PurePosixPath, "full_match"), reason="PurePath.full_match is new in Python 3.13")
    def test_an_absolute_glob_matches_as_full_match_does(self):
        for path, glob in product((path for path in PATHS if path.startswith("/")), GLOBS):
            if glob.startswith("/"):
                expected = PurePosixPath(path).full_match(glob)  # type: ignore[attr-defined]
                assert matches_any_glob(path, [glob]) is expected, (path, glob)


def _paths() -> list[str]:
    """Relative and absolute paths of one to four segments, hidden ones included."""
    parts = ("a", "b", ".h")
    relative = ["/".join(chosen) for depth in range(1, 5) for chosen in product(parts, repeat=depth)]
    return [*relative, *(f"/{path}" for path in relative)]


def _globs() -> list[str]:
    """Globs of one to three segments mixing names, wildcards and ``**``, relative and absolute."""
    segments = ("a", "*", "**", "?", "[ab]", ".*", "a**")
    relative = ["/".join(chosen) for depth in range(1, 4) for chosen in product(segments, repeat=depth)]
    return [*relative, *(f"/{glob}" for glob in relative)]


PATHS = _paths()
GLOBS = _globs()


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
