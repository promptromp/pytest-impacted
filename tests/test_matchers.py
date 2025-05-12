"""Unit-tests for the pytest_affected plugin."""

from pytest_affected.matchers import matches_affected_tests


def test_matches_affected_tests_positive():
    """Test that matches_affected_tests returns True for matching paths."""
    affected = ["foo/bar/test_sample.py", "foo/bar/test_other.py"]
    assert matches_affected_tests("test_sample.py", affected_tests=affected)
    assert matches_affected_tests("test_other.py", affected_tests=affected)


def test_matches_affected_tests_negative():
    """Test that matches_affected_tests returns False for non-matching paths."""
    affected = ["foo/bar/test_sample.py", "foo/bar/test_other.py"]
    assert not matches_affected_tests("not_a_test.py", affected_tests=affected)


def test_matches_affected_tests_empty():
    """Test that matches_affected_tests returns False if affected_tests is empty."""
    assert not matches_affected_tests("test_sample.py", affected_tests=[])
