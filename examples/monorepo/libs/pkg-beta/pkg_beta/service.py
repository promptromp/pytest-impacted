"""Service layer for pkg-beta, built on pkg-alpha."""

from pkg_alpha.core import add


def double_add(a: int, b: int) -> int:
    return add(a, b) * 2
