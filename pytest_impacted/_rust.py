"""Safe import wrapper for the optional Rust extension module.

The Rust extension (pytest_impacted_rs) provides accelerated import parsing
via ruff's Python parser and rayon for parallelism. When unavailable, the
pure-Python astroid-based implementation is used as a fallback.
"""

__all__ = ["RUST_AVAILABLE", "rust_parse_file_imports", "rust_parse_all_imports"]

try:
    from pytest_impacted_rs import (
        parse_all_imports as rust_parse_all_imports,
        parse_file_imports as rust_parse_file_imports,
    )

    RUST_AVAILABLE = True
except ImportError:
    rust_parse_file_imports = None  # type: ignore[assignment]
    rust_parse_all_imports = None  # type: ignore[assignment]
    RUST_AVAILABLE = False
