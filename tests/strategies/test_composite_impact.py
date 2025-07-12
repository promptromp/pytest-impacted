"""unit-tests for the composite impact strategy module."""

from unittest.mock import MagicMock

from pytest_impacted.strategies import (
    CompositeImpactStrategy,
    ImpactStrategy,
)


class TestCompositeImpactStrategy:
    """Test the composite strategy that combines multiple strategies."""

    def test_find_impacted_tests_combines_strategies(self):
        """Test that composite strategy combines results from multiple strategies."""
        # Create mock strategies
        strategy1 = MagicMock(spec=ImpactStrategy)
        strategy1.find_impacted_tests.return_value = ["test_a", "test_b"]

        strategy2 = MagicMock(spec=ImpactStrategy)
        strategy2.find_impacted_tests.return_value = ["test_b", "test_c"]

        composite = CompositeImpactStrategy([strategy1, strategy2])
        result = composite.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["module"],
            ns_module="mypackage",
        )

        # Should combine and deduplicate results
        assert sorted(result) == ["test_a", "test_b", "test_c"]

        # Both strategies should have been called with the same parameters
        expected_call_args = {
            "changed_files": ["src/module.py"],
            "impacted_modules": ["module"],
            "ns_module": "mypackage",
            "tests_package": None,
            "root_dir": None,
            "session": None,
        }
        strategy1.find_impacted_tests.assert_called_once_with(**expected_call_args)
        strategy2.find_impacted_tests.assert_called_once_with(**expected_call_args)

    def test_find_impacted_tests_empty_strategies(self):
        """Test composite strategy with no sub-strategies."""
        composite = CompositeImpactStrategy([])
        result = composite.find_impacted_tests(
            changed_files=["src/module.py"],
            impacted_modules=["module"],
            ns_module="mypackage",
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
        )

        assert result == ["test_a"]
        strategy.find_impacted_tests.assert_called_once()
