"""Safe import wrapper for the optional Rust extension module.

The Rust extension (pytest_impacted_rs) provides accelerated import parsing
via ruff's Python parser and rayon for parallelism. When unavailable, the
pure-Python astroid-based implementation is used as a fallback.
"""

__all__ = [
    "RUST_AVAILABLE",
    "RUST_PARSES_DYNAMIC_IMPORTS",
    "RUST_SPLITS_TYPE_ONLY",
    "outdated_for",
    "rust_parse_all_dynamic_imports",
    "rust_parse_all_imports",
    "rust_parse_all_imports_split",
    "uses_rust",
]

try:
    from pytest_impacted_rs import parse_all_imports as rust_parse_all_imports

    RUST_AVAILABLE = True
except ImportError:
    rust_parse_all_imports = None  # type: ignore[assignment]
    RUST_AVAILABLE = False

# The extension is a separate distribution, which an install may hold at an older release than this
# package: it tells type-only imports apart only from 0.39.0. Asked to, an older one leaves the parsing
# to the Python backend (see uses_rust).
try:
    from pytest_impacted_rs import parse_all_imports_split as rust_parse_all_imports_split

    RUST_SPLITS_TYPE_ONLY = True
except ImportError:
    rust_parse_all_imports_split = None  # type: ignore[assignment]
    RUST_SPLITS_TYPE_ONLY = False


# It finds the calls that import by name (see pytest_impacted.dynamic_imports) only from 0.40.1. With an older
# one, the stdlib ``ast`` pass finds them, file by file: the same imports, only slower.
try:
    from pytest_impacted_rs import parse_all_dynamic_imports as rust_parse_all_dynamic_imports

    RUST_PARSES_DYNAMIC_IMPORTS = True
except ImportError:
    rust_parse_all_dynamic_imports = None  # type: ignore[assignment]
    RUST_PARSES_DYNAMIC_IMPORTS = False


def uses_rust(ignore_type_checking_imports: bool = False) -> bool:
    """Whether the Rust extension parses with these options: installed, and new enough for them."""
    return RUST_AVAILABLE and (RUST_SPLITS_TYPE_ONLY or not ignore_type_checking_imports)


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
