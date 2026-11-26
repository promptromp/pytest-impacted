"""No module of the package imports another's private name: a name another module needs is public."""

import ast
from pathlib import Path

import pytest_impacted


PACKAGE = Path(pytest_impacted.__file__).parent


def private_imports(source: Path) -> list[str]:
    """``from pytest_impacted.<module> import _name`` in *source*; a private *module* (``from pytest_impacted import
    _rust``) is the package's own business, not another module's internals."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pytest_impacted.")
        for alias in node.names
        if alias.name.startswith("_")
    ]


def test_no_module_imports_another_s_private_name():
    found = {source.name: names for source in sorted(PACKAGE.glob("*.py")) if (names := private_imports(source))}

    assert found == {}
