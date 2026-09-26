"""Which fixtures in a conftest depend on changed code — from its AST, never importing it.

A conftest that imports changed code impacts, at the coarsest, every test in its
directory. Most of those tests never touch the fixtures built on the change, though,
so :func:`affected_fixtures` names the fixtures that may, letting the caller keep only
the tests that request them.

The answer errs one way only. Every name the changed code reaches is reported as a
possible fixture — a ``def`` under any decorator, a name bound by an import (a
re-exported fixture), a name bound by assignment (a fixture built by a factory) —
because a reported name that is not a fixture simply matches no test. And whenever
the analysis cannot be sure — a star import, a hook, a fixture whose name is computed,
changed code run at import time — it answers ``None``, and the caller keeps the whole
directory. An empty answer therefore means no fixture can be affected.
"""

import ast
from collections.abc import Collection, Iterator

from pytest_impacted.parsing import module_level_statements, parse_source, resolve_import_from


_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class _Undecidable(Exception):
    """Raised inside the analysis when only the whole directory is a safe answer."""


def affected_fixtures(source: str, module_name: str, reached: Collection[str]) -> frozenset[str] | None:
    """Names under which a conftest's affected fixtures may be requested.

    Args:
        source: The conftest's source.
        module_name: Its dotted name, for resolving relative imports.
        reached: Every module that changed or depends on a change.

    Returns:
        Candidate fixture names — possibly none — or ``None`` when that cannot be
        determined safely and every test under the conftest must be assumed affected.
    """
    tree = parse_source(source)
    if tree is None:
        return None
    try:
        statements = list(module_level_statements(tree.body))
        changed = _Changed(reached, module_name)
        tainted = {name for stmt in statements for name in changed.bindings(stmt)}
        tainted |= {s.name for s in statements if isinstance(s, _DEFINITIONS) and changed.imported_within(s)}
        _propagate(statements, tainted)
        return frozenset(_candidate_names(statements, tainted))
    except (_Undecidable, RecursionError):
        return None


class _Changed:
    """Answers "does this import bind changed code?" for one conftest."""

    def __init__(self, reached: Collection[str], module_name: str):
        self.module_name = module_name
        self.reached = set(reached)
        # A binding to a package also reaches its submodules by attribute
        # (``import app`` → ``app.db.connect()``), so ancestors count too.
        self.reached_or_ancestor = {
            ".".join(parts[:i]) for r in self.reached for parts in [r.split(".")] for i in range(1, len(parts) + 1)
        }

    def bindings(self, node: ast.AST) -> set[str]:
        """Names one ``import`` statement binds to changed code (empty for any other node)."""
        bound: set[str] = set()
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import a.b.c` binds `a`, through which a.b.c and anything else under a is reachable.
                if alias.asname is None and alias.name.split(".")[0] in self.reached_or_ancestor:
                    bound.add(alias.name.split(".")[0])
                elif alias.asname is not None and alias.name in self.reached_or_ancestor:
                    bound.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            source = resolve_import_from(self.module_name, False, node.level, node.module)
            for alias in node.names:
                if alias.name == "*":
                    if source in self.reached:
                        raise _Undecidable  # which names came from it is unknowable
                    continue
                # A name from a changed module, or a submodule on the way to one.
                if source in self.reached or f"{source}.{alias.name}" in self.reached_or_ancestor:
                    bound.add(alias.asname or alias.name)
        return bound

    def imported_within(self, definition: ast.AST) -> bool:
        """Whether a function or class body imports changed code itself (``def db(): from app.db import …``)."""
        return any(self.bindings(node) for node in ast.walk(definition))


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


#: Names pytest reads from a conftest module besides fixtures and ``pytest_*`` hooks.
_COLLECTION_SETTINGS = frozenset({"collect_ignore", "collect_ignore_glob"})


def _propagate(statements: list[ast.stmt], tainted: set[str]) -> None:
    """Spread taint through module-level definitions and assignments, to a fixed point.

    An allowlist: changed names may flow into function and class *definitions* and
    into plain assignments (``URL = settings.URL``), which run nothing. Anything that
    would *call* changed code while the conftest is imported — an assignment's value,
    a decorator, a default argument, a class body — or any other statement touching
    changed names is undecidable: its effects are not confined to fixtures.
    """
    names = {id(stmt): _names(stmt) for stmt in statements}
    changed = True
    while changed:
        changed = False
        for stmt in statements:
            if isinstance(stmt, ast.Import | ast.ImportFrom) or not (names[id(stmt)] & tainted):
                continue
            if isinstance(stmt, _DEFINITIONS):
                if _runs_at_import(stmt, tainted):
                    raise _Undecidable
                # A function also taints the module globals it assigns (`global ENGINE`).
                new = {stmt.name} | {n for g in ast.walk(stmt) if isinstance(g, ast.Global) for n in g.names}
            elif isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if not all(isinstance(t, ast.Name) for t in targets) or _calls_changed_code(stmt.value, tainted):
                    raise _Undecidable
                walrus = {n.target.id for n in ast.walk(stmt) if isinstance(n, ast.NamedExpr)}
                new = {t.id for t in targets if isinstance(t, ast.Name)} | walrus
            else:
                raise _Undecidable  # code that runs at import time with the changed names
            if not new <= tainted:
                tainted |= new
                changed = True


def _calls_changed_code(node: ast.AST | None, tainted: set[str]) -> bool:
    """Whether evaluating *node* calls something, or passes something, that is changed code."""
    return node is not None and any(isinstance(n, ast.Call) and _names(n) & tainted for n in ast.walk(node))


def _runs_at_import(definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, tainted: set[str]) -> bool:
    """Whether defining a function or class evaluates changed code: decorators, defaults, class bodies."""
    if any(_calls_changed_code(d, tainted) or _names(d) & tainted for d in definition.decorator_list):
        return True  # applying a decorator is a call, even when written without parentheses
    if isinstance(definition, ast.ClassDef):
        header = [*definition.bases, *(k.value for k in definition.keywords)]
        body = [s for s in definition.body if not isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef)]
        methods = [s for s in definition.body if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef)]
        return (
            any(_calls_changed_code(h, tainted) for h in header)
            or any(_names(s) & tainted for s in body)
            or any(_runs_at_import(m, tainted) for m in methods)
        )
    arguments = definition.args
    defaults = [*arguments.defaults, *(d for d in arguments.kw_defaults if d is not None)]
    return any(_calls_changed_code(d, tainted) for d in defaults)


def _candidate_names(statements: list[ast.stmt], tainted: set[str]) -> Iterator[str]:
    """Every tainted name, plus the ``name=`` a tainted fixture is registered under."""
    if any(name.startswith("pytest_") or name in _COLLECTION_SETTINGS for name in tainted):
        raise _Undecidable  # a hook or collection setting, however it is bound, can affect any test
    used = set().union(*(_names(s) for s in statements if not isinstance(s, ast.Import | ast.ImportFrom)))
    imported = {
        alias.asname or alias.name.split(".")[0]
        for s in statements
        if isinstance(s, ast.Import | ast.ImportFrom)
        for alias in s.names
    }
    if (tainted & imported) - used:
        # Imported and never used here: re-exported, most likely as a fixture or hook
        # whose registered name (a `name=` in the other module) cannot be seen from here.
        raise _Undecidable
    yield from tainted
    for stmt in statements:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name in tainted:
            for decorator in stmt.decorator_list:
                yield from _registered_names(decorator)
        elif isinstance(stmt, ast.Assign | ast.AnnAssign) and _names(stmt) & tainted and stmt.value is not None:
            for call in (n for n in ast.walk(stmt.value) if isinstance(n, ast.Call)):
                yield from _registered_names(call)


def _registered_names(decorator: ast.expr) -> Iterator[str]:
    """Names a decorator (or registering call) gives the function, beyond its own name.

    A literal ``name=`` is followed. A computed one, or ``specname=`` (a hook
    registered under a name not starting with ``pytest_``; any other hook
    implementation must be named ``pytest_*``, which is caught by name), cannot be.
    """
    if not isinstance(decorator, ast.Call):
        return
    for keyword in decorator.keywords:
        if keyword.arg == "specname":
            raise _Undecidable
        if keyword.arg == "name":
            if not (isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str)):
                raise _Undecidable
            yield keyword.value.value
