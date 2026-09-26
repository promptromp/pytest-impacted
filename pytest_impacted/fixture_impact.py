"""Which fixtures in a conftest depend on changed code — from its AST, never importing it.

A conftest that imports changed code impacts, at the coarsest, every test in its
directory. Most of those tests never touch the fixtures built on the change, though,
so :func:`affected_fixtures` names the fixtures that do, letting the caller keep only
the tests that request them.

The analysis is deliberately one-sided: whenever it cannot be sure — a star import,
an affected hook, a side effect at import time — it answers ``None``, and the caller
falls back to the whole directory. It never claims a fixture is unaffected without
having seen every use of the changed names.
"""

import ast
from collections.abc import Collection, Iterator

from pytest_impacted.parsing import parse_source, resolve_import_from


_COMPOUND = (ast.If, ast.Try, ast.TryStar, ast.With, ast.For, ast.While, ast.Match)


class _Undecidable(Exception):
    """Raised inside the analysis when only the whole directory is a safe answer."""


def affected_fixtures(source: str, module_name: str, reached: Collection[str]) -> frozenset[str] | None:
    """Names of the fixtures in a conftest whose code depends on any module in *reached*.

    Args:
        source: The conftest's source.
        module_name: Its dotted name, for resolving relative imports.
        reached: Every module that changed or depends on a change.

    Returns:
        The affected fixture names — possibly none — or ``None`` when that cannot be
        determined safely and every test under the conftest must be assumed affected.
    """
    tree = parse_source(source)
    if tree is None:
        return None
    try:
        statements = list(_simple_statements(tree.body))
        tainted = _import_bindings(statements, module_name, reached)
        tainted |= {
            stmt.name
            for stmt in statements
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and _imports_changed_code(stmt, module_name, reached)
        }
        _propagate(statements, tainted)
        return frozenset(_fixture_names(statements, tainted))
    except (_Undecidable, RecursionError):
        return None


def _simple_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Module-level statements, flattened through compound blocks.

    A block's own header (an ``if`` test, a ``with`` item, …) runs at import time
    too, so it is yielded as an expression statement for the taint check to see.
    """
    for stmt in body:
        if not isinstance(stmt, _COMPOUND):
            yield stmt
            continue
        for header in _headers(stmt):
            yield ast.Expr(value=header)
        for field in ("body", "orelse", "finalbody"):
            yield from _simple_statements(getattr(stmt, field, []))
        for handler in getattr(stmt, "handlers", []):
            yield from _simple_statements(handler.body)
        for case in getattr(stmt, "cases", []):
            yield from _simple_statements(case.body)


def _headers(stmt: ast.stmt) -> list[ast.expr]:
    if isinstance(stmt, ast.If | ast.While):
        return [stmt.test]
    if isinstance(stmt, ast.For):
        return [stmt.iter]
    if isinstance(stmt, ast.With):
        return [item.context_expr for item in stmt.items]
    if isinstance(stmt, ast.Match):
        return [stmt.subject]
    return []


def _import_bindings(statements: list[ast.stmt], module_name: str, reached: Collection[str]) -> set[str]:
    """Local names the module-level imports bind to a module in *reached*, or to something from one."""
    return {name for stmt in statements for name in _affected_bindings(stmt, module_name, reached)}


def _affected_bindings(node: ast.AST, module_name: str, reached: Collection[str]) -> set[str]:
    """Names one ``import`` statement binds to changed code (empty for any other node)."""
    bound: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if {".".join(parts[: i + 1]) for i in range(len(parts))} & set(reached):
                bound.add(alias.asname or parts[0])
    elif isinstance(node, ast.ImportFrom):
        source = resolve_import_from(module_name, False, node.level, node.module)
        for alias in node.names:
            if alias.name == "*":
                if source in reached:
                    raise _Undecidable  # which names came from it is unknowable
                continue
            if {source, f"{source}.{alias.name}"} & set(reached):
                bound.add(alias.asname or alias.name)
    return bound


def _imports_changed_code(definition: ast.AST, module_name: str, reached: Collection[str]) -> bool:
    """Whether a function or class body imports changed code itself (``def db(): from app.db import …``)."""
    return any(_affected_bindings(node, module_name, reached) for node in ast.walk(definition))


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _propagate(statements: list[ast.stmt], tainted: set[str]) -> None:
    """Spread taint through module-level definitions and assignments, to a fixed point."""
    changed = True
    while changed:
        changed = False
        for stmt in statements:
            if isinstance(stmt, ast.Import | ast.ImportFrom) or not (_names(stmt) & tainted):
                continue
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                new = {stmt.name}
            elif isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if not all(isinstance(t, ast.Name) for t in targets) or _mentions_fixture(stmt.value):
                    # `os.environ[...] = f()` is a side effect; `db = pytest.fixture(f)` is a
                    # fixture defined without a def — neither can be narrowed safely.
                    raise _Undecidable
                new = {t.id for t in targets if isinstance(t, ast.Name)}
            else:
                raise _Undecidable  # code that runs at import time with the changed names
            if not new <= tainted:
                tainted |= new
                changed = True


def _fixture_names(statements: list[ast.stmt], tainted: set[str]) -> Iterator[str]:
    for stmt in statements:
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) or stmt.name not in tainted:
            continue
        if stmt.name.startswith("pytest_"):
            raise _Undecidable  # a hook can change any test's collection or run
        for decorator in stmt.decorator_list:
            if _is_fixture(decorator):
                yield _declared_name(decorator) or stmt.name


def _mentions_fixture(node: ast.AST | None) -> bool:
    return node is not None and any(_is_fixture(n) for n in ast.walk(node))


def _is_fixture(node: ast.AST) -> bool:
    """``@fixture``, ``@pytest.fixture``, ``@pytest_asyncio.fixture``, called or not."""
    target = node.func if isinstance(node, ast.Call) else node
    return (isinstance(target, ast.Name) and target.id == "fixture") or (
        isinstance(target, ast.Attribute) and target.attr == "fixture"
    )


def _declared_name(decorator: ast.expr) -> str | None:
    """The ``name=`` a fixture decorator gives, if any."""
    if not isinstance(decorator, ast.Call):
        return None
    for keyword in decorator.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None
