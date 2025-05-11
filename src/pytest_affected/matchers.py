"""Matchers used for pattern matching and unit-tests."""


def matches_affected_tests(item_path: str, *, affected_tests: list[str]) -> bool:
    """Check if the item path matches any of the affected tests."""
    for test in affected_tests:
        if test.endswith(item_path):
            return True

    return False
