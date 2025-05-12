"""Matchers used for pattern matching and unit-tests."""


def matches_impacted_tests(item_path: str, *, impacted_tests: list[str]) -> bool:
    """Check if the item path matches any of the impacted tests."""
    for test in impacted_tests:
        if test.endswith(item_path):
            return True

    return False
