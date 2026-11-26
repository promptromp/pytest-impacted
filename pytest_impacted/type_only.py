"""Which imports under ``if TYPE_CHECKING:`` are type-only: the names they bind serve annotations alone.

``TYPE_CHECKING`` is false at runtime (PEP 484), so an import in such a block never runs. But the module may
still need the names it binds at runtime, from somewhere else: a lazy ``__init__`` declares there what it
loads on demand, for ``from pkg import Model`` to find. So an import is type-only only if the module uses
every name it binds, and only where no code runs: in annotations, or in another such block. A name the module
uses at runtime, mentions in a string its code uses (``__all__``, a lazy loader's table), or never uses at all
(declared, then, for others to import) keeps the import.

Mirrors ``rust/src/type_only.rs``, walk for walk: the same annotations (a parameter's, a return, a variable's —
not a type parameter's bound, nor a ``type`` alias's value), the same strings (no bare string statement, no
f-string's text), and the same depth, past which no import of the module is type-only.
"""

import ast
from collections.abc import Iterator
from typing import NamedTuple, cast


#: How deeply nested an expression the walk enters, as ``DEEPEST`` in the Rust backend: in a module nested deeper,
#: no import is type-only, the safe side (an edge kept selects more tests, never fewer).
DEEPEST = 2_000

BLOCK_FIELDS = ("body", "cases", "handlers", "orelse", "finalbody")  # source order, in ast's tree
_BLOCKS = (
    ast.If,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.ExceptHandler,
    ast.Match,
    ast.match_case,
    ast.For,
    ast.AsyncFor,
    ast.While,
)
#: An f-string's (or a t-string's, Python 3.14) placeholder: no expression to ruff, so it counts no nesting level.
_PLACEHOLDERS = tuple(getattr(ast, name) for name in ("FormattedValue", "Interpolation") if hasattr(ast, name))
_TEMPLATES = tuple(getattr(ast, name) for name in ("JoinedStr", "TemplateStr") if hasattr(ast, name))

Imports = ast.Import | ast.ImportFrom


class _Uses(NamedTuple):
    """Where a module uses names: in type-only code (annotations, ``if TYPE_CHECKING:`` bodies) or at runtime."""

    typed: set[str]
    runtime: set[str]
    strings: list[tuple[str, bool]]  # each string code may read, and whether it sits in type-only code


def type_only_imports(tree: ast.Module) -> list[Imports]:
    """The import statements in *tree* whose names only annotations use (see the module docstring)."""
    modules = _module_names(tree)
    guarded = [(node, _bound_names(node)) for node in _guarded_imports(tree, modules)]
    watched = {name for _, names in guarded if names is not None for name in names}
    if not watched or (uses := _uses(tree, modules)) is None:
        return []
    typed, runtime = uses.typed & watched, uses.runtime & watched
    for text, in_type_only_code in uses.strings:
        (typed if in_type_only_code else runtime).update(name for name in watched if mentions(text, name))
    return [
        node
        for node, names in guarded
        if names is not None and all(name in typed and name not in runtime for name in names)
    ]


def module_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements that run when the module is imported, nested blocks included, but no function or class body,
    in source order. Without recursion: ``ast`` nests each ``elif`` in the ``orelse`` before it, so a generated
    1,000-branch dispatch is 1,000 deep (ruff keeps the clauses flat)."""
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        stmt = stack.pop()
        if isinstance(stmt, ast.stmt):
            yield stmt
        if isinstance(stmt, _BLOCKS):
            for field in reversed(BLOCK_FIELDS):
                stack.extend(reversed(getattr(stmt, field, None) or []))


def _module_names(tree: ast.Module) -> frozenset[str]:
    """The names module-level ``import`` statements bind: a module's ``TYPE_CHECKING`` is checked through one."""
    return frozenset(
        alias.asname or alias.name.partition(".")[0]
        for stmt in module_level(tree.body)
        if isinstance(stmt, ast.Import)
        for alias in stmt.names
    )


def _bound_names(node: Imports) -> frozenset[str] | None:
    """The names an import binds; ``None`` for a star import, whose names cannot be known."""
    if isinstance(node, ast.Import):
        return frozenset(alias.asname or alias.name.partition(".")[0] for alias in node.names)
    if any(alias.name == "*" for alias in node.names):
        return None
    return frozenset(alias.asname or alias.name for alias in node.names)


def _guarded_imports(tree: ast.Module, modules: frozenset[str]) -> Iterator[Imports]:
    """The import statements inside an ``if TYPE_CHECKING:`` body, at any depth: statements alone hold them."""
    stack: list[tuple[ast.AST, bool]] = [(stmt, False) for stmt in reversed(tree.body)]
    while stack:
        node, guarded = stack.pop()
        if isinstance(node, Imports):
            if guarded:
                yield node
            continue
        for field in reversed(BLOCK_FIELDS):
            inside = guarded or (field == "body" and isinstance(node, ast.If) and is_type_checking(node.test, modules))
            stack.extend((child, inside) for child in reversed(getattr(node, field, None) or []))


def _uses(tree: ast.Module, modules: frozenset[str]) -> _Uses | None:
    """Every name the module loads and every string its code may read, each with where it sits; ``None`` for a
    module nested deeper than :data:`DEEPEST`.

    Walked without recursion, as the Rust visitor walks, with the nesting it counts: one level for each expression,
    but an f-string's placeholder (ruff has none), and none for a statement, a parameter or a keyword.
    """
    uses = _Uses(set(), set(), [])
    stack: list[tuple[ast.AST, bool, int]] = [(stmt, False, 0) for stmt in reversed(tree.body)]
    while stack:
        node, type_only, nesting = stack.pop()
        if isinstance(node, ast.expr):
            if nesting == DEEPEST:
                return None
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                (uses.typed if type_only else uses.runtime).add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                uses.strings.append((node.value, type_only))
        stack.extend(reversed(list(_children(node, type_only, nesting, modules))))
    return uses


def _children(
    node: ast.AST, type_only: bool, nesting: int, modules: frozenset[str]
) -> Iterator[tuple[ast.AST, bool, int]]:
    """*node*'s children for :func:`_uses`, each with whether it sits in type-only code and its nesting."""
    inner = nesting + 1 if isinstance(node, ast.expr) else nesting
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return  # no code reads a bare string, such as a docstring
    if isinstance(node, _TEMPLATES):
        yield from ((value, type_only, inner) for value in _placeholders(cast(ast.JoinedStr, node)))
        return  # its text is no string of its own (to ruff either)
    for field, value in ast.iter_fields(node):
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, ast.AST):
                yield child, type_only or _types(node, field, modules), inner


def _types(node: ast.AST, field: str, modules: frozenset[str]) -> bool:
    """Whether *node*'s *field* is type-only code: an annotation, or the body of an ``if TYPE_CHECKING:``."""
    if field == "annotation":
        return isinstance(node, ast.arg | ast.AnnAssign)
    if field == "returns":
        return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    return field == "body" and isinstance(node, ast.If) and is_type_checking(node.test, modules)


def _placeholders(template: ast.JoinedStr) -> Iterator[ast.expr]:
    """The expressions an f-string (or a t-string, of the same shape) interpolates, its format specs' included:
    all at one level below it, as ruff walks them."""
    for value in template.values:
        if isinstance(value, _PLACEHOLDERS):
            placeholder = cast(ast.FormattedValue, value)  # an Interpolation has the same fields
            yield placeholder.value
            if placeholder.format_spec is not None:
                yield from _placeholders(cast(ast.JoinedStr, placeholder.format_spec))


def mentions(text: str, name: str) -> bool:
    """Whether *text* holds *name* as a whole identifier: ``Model`` in ``"Model | None"``, not in ``"Models"``."""
    start = text.find(name)
    while start != -1:
        end = start + len(name)
        if (start == 0 or not _identifier_char(text[start - 1])) and (
            end == len(text) or not _identifier_char(text[end])
        ):
            return True
        start = text.find(name, start + 1)
    return False


def _identifier_char(char: str) -> bool:
    """A character an identifier may hold: any non-ASCII one counts, so both backends agree on every script."""
    return char == "_" or char.isalnum() if char.isascii() else True


def is_type_checking(test: ast.expr, modules: frozenset[str]) -> bool:
    """Whether an ``if`` test is ``TYPE_CHECKING`` alone, as type checkers read it, or a module's.

    The name, wherever it comes from, or the attribute of a module the file imports at module level
    (``typing.TYPE_CHECKING``, ``t.TYPE_CHECKING`` after ``import typing as t``): another object's (a
    ``settings`` instance) may be true at runtime. Anything else may run: an ``else``, ``not TYPE_CHECKING``,
    ``TYPE_CHECKING or X``. An ``elif`` is an ``if`` in the ``orelse``, so it counts too.
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id in modules
    )
