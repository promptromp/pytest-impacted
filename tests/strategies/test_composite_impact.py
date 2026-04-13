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


class _Spy(ImpactStrategy):
    """Test double that records every lifecycle call it receives."""

    def __init__(self, name, events, *, setup_raises=False, teardown_raises=False):
        self.name = name
        self.events = events
        self.setup_raises = setup_raises
        self.teardown_raises = teardown_raises

    def setup(self, *, ns_module, tests_package=None, root_dir=None, session=None, dep_tree):
        self.events.append(("setup", self.name))
        if self.setup_raises:
            raise RuntimeError(f"{self.name} setup boom")

    def teardown(self):
        self.events.append(("teardown", self.name))
        if self.teardown_raises:
            raise RuntimeError(f"{self.name} teardown boom")

    def find_impacted_tests(
        self,
        changed_files,
        impacted_modules,
        ns_module,
        tests_package=None,
        root_dir=None,
        session=None,
        *,
        dep_tree,
    ):
        self.events.append(("find", self.name))
        return [f"test_from_{self.name}"]


class TestCompositeLifecycle:
    """Tests for setup/teardown propagation through CompositeImpactStrategy."""

    def test_setup_propagates_to_children_in_list_order(self):
        events = []
        composite = CompositeImpactStrategy([_Spy("a", events), _Spy("b", events), _Spy("c", events)])
        composite.setup(ns_module="pkg", dep_tree=nx.DiGraph())
        assert events == [("setup", "a"), ("setup", "b"), ("setup", "c")]

    def test_teardown_propagates_to_children_in_reverse_order(self):
        events = []
        composite = CompositeImpactStrategy([_Spy("a", events), _Spy("b", events), _Spy("c", events)])
        composite.teardown()
        # LIFO: last set up is first torn down
        assert events == [("teardown", "c"), ("teardown", "b"), ("teardown", "a")]

    def test_setup_exception_does_not_abort_remaining_children(self, caplog):
        events = []
        composite = CompositeImpactStrategy(
            [_Spy("a", events), _Spy("b", events, setup_raises=True), _Spy("c", events)]
        )
        with caplog.at_level("WARNING", logger="pytest_impacted.strategies"):
            composite.setup(ns_module="pkg", dep_tree=nx.DiGraph())

        # All three setups were attempted
        assert events == [("setup", "a"), ("setup", "b"), ("setup", "c")]
        # And the failure was logged
        assert any("raised in setup()" in rec.getMessage() for rec in caplog.records)

    def test_teardown_exception_does_not_abort_remaining_children(self, caplog):
        events = []
        composite = CompositeImpactStrategy(
            [_Spy("a", events), _Spy("b", events, teardown_raises=True), _Spy("c", events)]
        )
        with caplog.at_level("WARNING", logger="pytest_impacted.strategies"):
            composite.teardown()

        # Reverse order, all three attempted even though b raised
        assert events == [("teardown", "c"), ("teardown", "b"), ("teardown", "a")]
        assert any("raised in teardown()" in rec.getMessage() for rec in caplog.records)

    def test_default_setup_teardown_are_no_ops_on_base_class(self):
        """Existing strategies that don't override the hooks must keep working.

        Regression guard: adding setup/teardown to ImpactStrategy must not
        force any downstream subclass to implement them.
        """

        class LegacyStrategy(ImpactStrategy):
            def find_impacted_tests(self, *args, **kwargs):
                return []

        s = LegacyStrategy()
        # Neither call should raise
        s.setup(ns_module="pkg", dep_tree=nx.DiGraph())
        s.teardown()

    def test_setup_passes_all_context_kwargs_to_children(self):
        """All kwargs supplied to the composite's setup should reach each child."""
        received = {}

        class Capturing(ImpactStrategy):
            def setup(self, **kwargs):
                received.update(kwargs)

            def find_impacted_tests(self, *args, **kwargs):
                return []

        dep_tree = nx.DiGraph()
        session = object()
        composite = CompositeImpactStrategy([Capturing()])
        composite.setup(
            ns_module="mypkg",
            tests_package="tests",
            root_dir=None,
            session=session,
            dep_tree=dep_tree,
        )
        assert received == {
            "ns_module": "mypkg",
            "tests_package": "tests",
            "root_dir": None,
            "session": session,
            "dep_tree": dep_tree,
        }
