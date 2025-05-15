"""Unit-tests for the matchers module."""

from pytest_impacted.api import matches_impacted_tests


def test_matches_impacted_tests_positive():
    """Test that matches_impacted_tests returns True for matching paths."""
    impacted = ["foo/bar/test_sample.py", "foo/bar/test_other.py"]
    assert matches_impacted_tests("test_sample.py", impacted_tests=impacted)
    assert matches_impacted_tests("test_other.py", impacted_tests=impacted)


def test_matches_impacted_tests_negative():
    """Test that matches_impacted_tests returns False for non-matching paths."""
    impacted = ["foo/bar/test_sample.py", "foo/bar/test_other.py"]
    assert not matches_impacted_tests("not_a_test.py", impacted_tests=impacted)


def test_matches_impacted_tests_empty():
    """Test that matches_impacted_tests returns False if impacted_tests is empty."""
    assert not matches_impacted_tests("test_sample.py", impacted_tests=[])
