"""Tests for dependency tree caching functionality."""

from unittest.mock import MagicMock, patch

from pytest_impacted.strategies import _cached_build_dep_tree, clear_dep_tree_cache


class TestCaching:
    """Test the caching functionality."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_dep_tree_cache()

    def teardown_method(self):
        """Clear cache after each test."""
        clear_dep_tree_cache()

    @patch("pytest_impacted.strategies.build_dep_tree")
    def test_cache_avoids_duplicate_calls(self, mock_build_tree):
        """Test that the cache avoids duplicate calls to build_dep_tree."""
        mock_dep_tree = MagicMock()
        mock_build_tree.return_value = mock_dep_tree

        # Call the cached function twice with the same parameters
        result1 = _cached_build_dep_tree("mypackage", "tests")
        result2 = _cached_build_dep_tree("mypackage", "tests")

        # build_dep_tree should only be called once due to caching
        mock_build_tree.assert_called_once_with("mypackage", tests_package="tests")

        # Both results should be the same
        assert result1 is result2
        assert result1 is mock_dep_tree

    @patch("pytest_impacted.strategies.build_dep_tree")
    def test_cache_different_parameters(self, mock_build_tree):
        """Test that different parameters result in different cache entries."""
        mock_dep_tree1 = MagicMock()
        mock_dep_tree2 = MagicMock()
        mock_build_tree.side_effect = [mock_dep_tree1, mock_dep_tree2]

        # Call with different parameters
        result1 = _cached_build_dep_tree("mypackage", "tests")
        result2 = _cached_build_dep_tree("mypackage", "other_tests")

        # build_dep_tree should be called twice with different parameters
        assert mock_build_tree.call_count == 2
        mock_build_tree.assert_any_call("mypackage", tests_package="tests")
        mock_build_tree.assert_any_call("mypackage", tests_package="other_tests")

        # Results should be different
        assert result1 is mock_dep_tree1
        assert result2 is mock_dep_tree2
        assert result1 is not result2

    @patch("pytest_impacted.strategies.build_dep_tree")
    def test_cache_clear_functionality(self, mock_build_tree):
        """Test that clearing the cache works correctly."""
        mock_dep_tree = MagicMock()
        mock_build_tree.return_value = mock_dep_tree

        # Call the cached function
        _cached_build_dep_tree("mypackage", "tests")
        assert mock_build_tree.call_count == 1

        # Call again - should use cache
        _cached_build_dep_tree("mypackage", "tests")
        assert mock_build_tree.call_count == 1

        # Clear cache and call again - should call build_dep_tree again
        clear_dep_tree_cache()
        _cached_build_dep_tree("mypackage", "tests")
        assert mock_build_tree.call_count == 2

    @patch("pytest_impacted.strategies.build_dep_tree")
    def test_none_tests_package_caching(self, mock_build_tree):
        """Test caching works correctly with None tests_package."""
        mock_dep_tree = MagicMock()
        mock_build_tree.return_value = mock_dep_tree

        # Call with None tests_package
        result1 = _cached_build_dep_tree("mypackage", None)
        result2 = _cached_build_dep_tree("mypackage", None)

        # Should only call build_dep_tree once
        mock_build_tree.assert_called_once_with("mypackage", tests_package=None)
        assert result1 is result2
