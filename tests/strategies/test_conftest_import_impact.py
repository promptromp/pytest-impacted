"""Unit tests for the opt-in rule: a conftest that imports changed code impacts its directory."""

from pathlib import Path

import networkx as nx
import pytest

from pytest_impacted.strategies import (
    ConftestImportImpactStrategy,
    DependencyFileImpactStrategy,
    InvalidationFileImpactStrategy,
    PytestImpactStrategy,
    get_default_strategies,
)


@pytest.fixture
def project(tmp_path: Path) -> nx.DiGraph:
    """``app.db`` → ``app.helpers`` → ``suite/db/conftest.py``, beside an unrelated ``suite/other``.

    Edges run from the imported module to its importer, as in the graph ``build_dep_tree`` returns.
    """
    files = {
        "app.db": "app/db.py",
        "app.utils": "app/utils.py",
        "app.helpers": "app/helpers.py",
        "suite.db.conftest": "suite/db/conftest.py",
        "suite.db.test_db": "suite/db/test_db.py",
        "suite.db.deep.test_deep": "suite/db/deep/test_deep.py",
        "suite.other.test_other": "suite/other/test_other.py",
    }
    dep_tree = nx.DiGraph()
    for module, rel in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
        dep_tree.add_node(module, path=str(tmp_path / rel))
    dep_tree.add_edges_from(
        [("app.db", "app.helpers"), ("app.helpers", "suite.db.conftest"), ("app.utils", "suite.other.test_other")]
    )
    return dep_tree


def find(strategy, dep_tree: nx.DiGraph, changed: str) -> list[str]:
    root = Path(dep_tree.nodes["app.db"]["path"]).parents[1]
    module = changed.removesuffix(".py").replace("/", ".")
    return strategy.find_impacted_tests(
        changed_files=[changed],
        impacted_modules=[module],
        ns_module="app",
        tests_package="suite",
        root_dir=root,
        dep_tree=dep_tree,
    )


class TestConftestImportImpactStrategy:
    def test_a_conftest_importing_changed_code_impacts_its_directory_and_below(self, project):
        """Through a helper module too: the conftest imports ``app.db`` only transitively."""
        assert find(ConftestImportImpactStrategy(), project, "app/db.py") == [
            "suite.db.deep.test_deep",
            "suite.db.test_db",
        ]

    def test_a_change_the_conftest_does_not_import_impacts_nothing(self, project):
        """Test modules reached by imports are the AST strategy's business, not this one's."""
        assert find(ConftestImportImpactStrategy(), project, "app/utils.py") == []

    def test_a_package_named_conftest_is_not_a_conftest(self, tmp_path):
        """pytest loads only files named ``conftest.py``; ``app/conftest/__init__.py`` is an ordinary package."""
        (tmp_path / "app/conftest").mkdir(parents=True)
        (tmp_path / "app/conftest/__init__.py").touch()
        (tmp_path / "app/conftest/test_inner.py").touch()
        dep_tree = nx.DiGraph()
        dep_tree.add_node("app.db", path=str(tmp_path / "app/db.py"))
        dep_tree.add_node("app.conftest", path=str(tmp_path / "app/conftest/__init__.py"))
        dep_tree.add_node("app.conftest.test_inner", path=str(tmp_path / "app/conftest/test_inner.py"))
        dep_tree.add_edge("app.db", "app.conftest")

        assert find(ConftestImportImpactStrategy(), dep_tree, "app/db.py") == []


def test_by_default_a_conftest_importing_changed_code_impacts_nothing_beyond_its_imports(project):
    """The default pipeline leaves the reached conftest's directory alone: that is the opt-in rule."""
    assert find(PytestImpactStrategy(), project, "app/db.py") == []


class TestGetDefaultStrategiesWithConftestImports:
    def test_not_included_by_default(self):
        assert not any(isinstance(s, ConftestImportImpactStrategy) for s in get_default_strategies())

    def test_included_right_after_the_pytest_strategy_when_configured(self):
        strategies = get_default_strategies(conftest_imports=True)

        kinds = [type(s) for s in strategies]
        assert kinds.index(ConftestImportImpactStrategy) == kinds.index(PytestImpactStrategy) + 1

    @pytest.mark.parametrize("watch_dep_files", [True, False])
    def test_independent_of_the_other_switches(self, watch_dep_files):
        strategies = get_default_strategies(
            watch_dep_files=watch_dep_files, invalidate_all_patterns=["*.json"], conftest_imports=True
        )

        kinds = {type(s) for s in strategies}
        assert {ConftestImportImpactStrategy, InvalidationFileImpactStrategy} <= kinds
        assert (DependencyFileImpactStrategy in kinds) is watch_dep_files
