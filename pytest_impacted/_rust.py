"""Safe import wrapper for the optional Rust extension module.

The Rust extension (pytest_impacted_rs) provides accelerated import parsing
via ruff's Python parser and rayon for parallelism. When unavailable, the
pure-Python astroid-based implementation is used as a fallback.
"""

import inspect
from collections.abc import Callable


__all__ = ["RUST_AVAILABLE", "RUST_IGNORES_TYPE_CHECKING", "outdated_for", "rust_parse_all_imports", "uses_rust"]

try:
    from pytest_impacted_rs import parse_all_imports as rust_parse_all_imports

    RUST_AVAILABLE = True
except ImportError:
    rust_parse_all_imports = None  # type: ignore[assignment]
    RUST_AVAILABLE = False


def _accepts(function: Callable | None, parameter: str) -> bool:
    """Whether *function*'s signature names *parameter*; ``False`` when it has none to read."""
    try:
        return parameter in inspect.signature(function).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


# The extension is a separate distribution, which an install may hold at an older release than this
# package: it reads ``ignore_type_checking_imports`` only from 0.39.0. Asked to ignore type-only imports,
# an older one would raise TypeError, so the Python parser reads the files instead (see uses_rust).
RUST_IGNORES_TYPE_CHECKING = RUST_AVAILABLE and _accepts(rust_parse_all_imports, "ignore_type_checking_imports")


def uses_rust(ignore_type_checking_imports: bool = False) -> bool:
    """Whether the Rust extension parses with these options: installed, and new enough for them."""
    return RUST_AVAILABLE and (RUST_IGNORES_TYPE_CHECKING or not ignore_type_checking_imports)


def outdated_for(ignore_type_checking_imports: bool) -> str | None:
    """Why the installed extension leaves the parsing to the slower Python backend with these options, if it does.

    The ``fast`` extra stays unpinned: the extension's version is stamped from the release tag, and a
    development build is ``0.1.0``, which a pin would refuse. So it is said instead, by whoever reports.
    """
    if RUST_AVAILABLE and not uses_rust(ignore_type_checking_imports):
        return (
            "the installed pytest-impacted-rs predates ignoring type-only imports, so the slower pure-Python "
            "parser reads the files: upgrade it (pip install -U pytest-impacted-rs)."
        )
    return None
