"""unit-tests for the composite impact strategy module."""

from unittest.mock import MagicMock

import networkx as nx

from pytest_impacted.strategies import (
    ASTImpactStrategy,
    CompositeImpactStrategy,
    DependencyFileImpactStrategy,
    ImpactStrategy,
    PytestImpactStrategy,
    get_default_strategies,
)


class TestCompositeImpactStrategy:
    """Test the composite strategy that combines multiple strategies."""

    def test_find_impacted_tests_combines_strategies(self):
        """Test that composite strategy combines results from multiple strategies."""
        strategy1 = MagicMock(spec=ImpactStrategy)
        strategy1.find_impacted_tests.return_value = ["test_a", "test_b"]

        strategy2 = MagicMock(spec=ImpactStrategy)
        strategy2.find_impacted_tests.return_value = ["test_b", "test_c"]

        dep_tree = nx.DiGraph()
        composite = CompositeImpactStrategy([strategy1, strategy2])
        result = composite.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["module"],
            ns_module="mypackage",
            dep_tree=dep_tree,
        )

        # Should combine and deduplicate results
        assert sorted(result) == ["test_a", "test_b", "test_c"]

        # Both strategies should receive the same dep_tree instance
        dep_tree_1 = strategy1.find_impacted_tests.call_args[1]["dep_tree"]
        dep_tree_2 = strategy2.find_impacted_tests.call_args[1]["dep_tree"]
        assert dep_tree_1 is dep_tree
        assert dep_tree_2 is dep_tree

    def test_find_impacted_tests_empty_strategies(self):
        """Test composite strategy with no sub-strategies."""
        composite = CompositeImpactStrategy([])
        result = composite.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["module"],
            ns_module="mypackage",
            dep_tree=nx.DiGraph(),
        )
        assert result == []

    def test_find_impacted_tests_single_strategy(self):
        """Test composite strategy with single sub-strategy."""
        strategy = MagicMock(spec=ImpactStrategy)
        strategy.find_impacted_tests.return_value = ["test_a"]

        composite = CompositeImpactStrategy([strategy])
        result = composite.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["module"],
            ns_module="mypackage",
            dep_tree=nx.DiGraph(),
        )

        assert result == ["test_a"]
        strategy.find_impacted_tests.assert_called_once()


class TestGetDefaultStrategies:
    """Test the get_default_strategies factory function."""

    def test_default_includes_all_three_strategies(self):
        """Default composition includes AST, Pytest, and DependencyFile strategies."""
        strategies = get_default_strategies()
        assert len(strategies) == 3
        assert isinstance(strategies[0], ASTImpactStrategy)
        assert isinstance(strategies[1], PytestImpactStrategy)
        assert isinstance(strategies[2], DependencyFileImpactStrategy)

    def test_watch_dep_files_false_excludes_dependency_strategy(self):
        """When watch_dep_files=False, DependencyFileImpactStrategy is excluded."""
        strategies = get_default_strategies(watch_dep_files=False)
        assert len(strategies) == 2
        assert isinstance(strategies[0], ASTImpactStrategy)
        assert isinstance(strategies[1], PytestImpactStrategy)
        assert not any(isinstance(s, DependencyFileImpactStrategy) for s in strategies)

    def test_returns_new_instances_each_call(self):
        """Each call should return fresh strategy instances."""
        strategies_a = get_default_strategies()
        strategies_b = get_default_strategies()
        assert strategies_a is not strategies_b
        assert strategies_a[0] is not strategies_b[0]
