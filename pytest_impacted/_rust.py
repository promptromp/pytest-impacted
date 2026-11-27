"""Safe import wrapper for the optional Rust extension module.

The Rust extension (pytest_impacted_rs) provides accelerated import parsing
via ruff's Python parser and rayon for parallelism. When unavailable, the
pure-Python implementation (the standard library's ``ast``) is used as a fallback.
"""

__all__ = [
    "PYTHON_LABEL",
    "RUST_AVAILABLE",
    "RUST_LABEL",
    "RUST_PARSES_DYNAMIC_IMPORTS",
    "RUST_PARSES_EACH",
    "RUST_SPLITS_TYPE_ONLY",
    "outdated_for",
    "parses_with_rust",
    "rust_parse_all_dynamic_imports",
    "rust_parse_all_imports",
    "rust_parse_all_imports_split",
    "rust_parse_imports_each",
    "takes",
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


# From 0.41.0 it parses each file or hands it back (``None``): one ruff rejects though CPython compiles it (nested
# deeper than 202 levels), one not UTF-8, one too large. The Python backend reads those; an older extension
# returned no imports for them, so a test was lost.
try:
    from pytest_impacted_rs import parse_imports_each as rust_parse_imports_each

    RUST_PARSES_EACH = True
except ImportError:
    rust_parse_imports_each = None  # type: ignore[assignment]
    RUST_PARSES_EACH = False


def takes(*texts: str) -> bool:
    """Whether the extension can be handed these strings: a file's path and module name.

    Its strings are UTF-8, so PyO3 raises UnicodeEncodeError on a surrogate escape, Python's spelling of a
    file name that is not UTF-8 (on Linux, a Latin-1 ``café.py``). The Python backend reads such a file instead.
    """
    try:
        for text in texts:
            text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def uses_rust(ignore_type_checking_imports: bool = False) -> bool:
    """Whether the Rust extension parses with these options: installed, and new enough for them."""
    return RUST_AVAILABLE and (RUST_SPLITS_TYPE_ONLY or not ignore_type_checking_imports)


#: How the report header names each backend (``backend=``): here, not in ``backends``, as the header is written on
#: every run and must not load the parsers.
RUST_LABEL = "rust (ruff parser + rayon)"
PYTHON_LABEL = "python (ast)"


def parses_with_rust(ignore_type_checking_imports: bool = False, requested: str = "auto") -> bool:
    """Whether a run parses with the extension: :func:`uses_rust`, unless ``--impacted-backend=python`` asks for
    Python (``backends.backend_for`` and the report header both decide here)."""
    return requested != "python" and uses_rust(ignore_type_checking_imports)


def outdated_for(ignore_type_checking_imports: bool) -> str | None:
    """Why the installed extension should be upgraded, if it should: it leaves the parsing to the slower Python
    backend with these options, or it predates handing back the files it cannot read.

    The ``fast`` extra stays unpinned: the extension's version is stamped from the release tag, and a
    development build is ``0.1.0``, which a pin would refuse. So it is said instead, by whoever reports.
    """
    if RUST_AVAILABLE and not uses_rust(ignore_type_checking_imports):
        return (
            "the installed pytest-impacted-rs predates ignoring type-only imports, so the slower pure-Python "
            "parser reads the files: upgrade it (pip install -U pytest-impacted-rs)."
        )
    if RUST_AVAILABLE and not RUST_PARSES_EACH:
        return (
            "the installed pytest-impacted-rs predates 0.41: a file it cannot read (not in UTF-8, or one its parser "
            "rejects) imports nothing there, and a long generated expression can crash the run: upgrade it "
            "(pip install -U pytest-impacted-rs)."
        )
    return None
