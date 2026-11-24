"""Which imports under ``if TYPE_CHECKING:`` are type-only: the names they bind serve annotations alone.

``TYPE_CHECKING`` is false at runtime (PEP 484), so an import in such a block never runs. But the module may
still need the names it binds at runtime, from somewhere else: a lazy ``__init__`` declares there what it
loads on demand, for ``from pkg import Model`` to find. So an import is type-only only if the module uses
every name it binds, and only where no code runs: in annotations, or in another such block. A name the module
uses at runtime, mentions in a string its code uses (``__all__``, a lazy loader's table), or never uses at all
(declared, then, for others to import) keeps the import. Mirrors ``rust/src/type_only.rs``.
"""

from collections.abc import Iterator

from astroid.nodes import (
    AnnAssign,
    Arguments,
    Attribute,
    ClassDef,
    Const,
    ExceptHandler,
    Expr,
    For,
    FunctionDef,
    If,
    Import,
    ImportFrom,
    JoinedStr,
    Match,
    MatchCase,
    Module,
    Name,
    NodeNG,
    TemplateStr,
    Try,
    TryStar,
    While,
    With,
)


BLOCK_FIELDS = ("body", "cases", "handlers", "orelse", "finalbody")  # source order, in ast's and astroid's trees
_BLOCKS = (If, Try, TryStar, With, ExceptHandler, Match, MatchCase, For, While)


def type_only_imports(tree: Module) -> list[Import | ImportFrom]:
    """The import statements in *tree* whose names only annotations use (see the module docstring)."""
    modules = _module_names(tree)
    guarded = [node for node in tree.nodes_of_class((Import, ImportFrom)) if _in_type_only_code(node, modules)]
    bound = {id(node): _bound_names(node) for node in guarded}
    watched = {name for names in bound.values() if names is not None for name in names}
    if not watched:
        return []
    typed, runtime = _uses(tree, watched, modules)
    return [
        node
        for node in guarded
        if (names := bound[id(node)]) is not None and all(name in typed and name not in runtime for name in names)
    ]


def _module_names(tree: Module) -> frozenset[str]:
    """The names module-level ``import`` statements bind: a module's ``TYPE_CHECKING`` is checked through one."""
    return frozenset(
        alias or name.partition(".")[0]
        for stmt in _module_level(tree.body)
        if isinstance(stmt, Import)
        for name, alias in stmt.names
    )


def _module_level(body: list) -> Iterator[NodeNG]:
    """The statements that run when the module is imported, nested blocks included, but no function or class body."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, _BLOCKS):
            for field in BLOCK_FIELDS:
                yield from _module_level(getattr(stmt, field, None) or [])


def _bound_names(node: Import | ImportFrom) -> frozenset[str] | None:
    """The names an import binds; ``None`` for a star import, whose names cannot be known."""
    if isinstance(node, Import):
        return frozenset(alias or name.partition(".")[0] for name, alias in node.names)
    if any(name == "*" for name, _ in node.names):
        return None
    return frozenset(alias or name for name, alias in node.names)


def _uses(tree: Module, watched: set[str], modules: frozenset[str]) -> tuple[set[str], set[str]]:
    """Which of the *watched* names are used where no code runs, and which where it does."""
    typed: set[str] = set()
    runtime: set[str] = set()
    for node in _names_and_strings(tree):
        if names := _named_by(node, watched):
            (typed if _in_type_only_code(node, modules) else runtime).update(names)
    return typed, runtime


def _names_and_strings(tree: Module) -> Iterator[Name | Const]:
    """Every name and string in *tree*, a class's ``metaclass=`` included.

    astroid keeps the metaclass out of ``ClassDef.get_children`` (ruff, and the ``ast`` module, keep it among
    the keywords), in an attribute of its own; no other expression is left out of its walk.
    """
    yield from tree.nodes_of_class((Name, Const))
    for cls in tree.nodes_of_class(ClassDef):
        if (metaclass := cls._metaclass) is not None:  # private, but the only place astroid keeps it
            yield from metaclass.nodes_of_class((Name, Const))


def _named_by(node: Name | Const, watched: set[str]) -> set[str]:
    """The *watched* names a load of a name, or a string, refers to.

    A string counts wherever it may be read: an annotation's (``"Model | None"``) or one the code uses. Not a
    bare string statement, such as a docstring, which no code reads; nor the text of an f-string or a t-string,
    which is no string of its own (to ruff either).
    """
    if isinstance(node, Name):
        return {node.name} & watched
    if isinstance(node.value, str) and not isinstance(node.parent, Expr | JoinedStr | TemplateStr):
        return {name for name in watched if mentions(node.value, name)}
    return set()


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


def _in_type_only_code(node: NodeNG, modules: frozenset[str]) -> bool:
    """Whether *node* sits where no code runs: in an annotation, or in the body of an ``if TYPE_CHECKING:``."""
    child = node
    for parent in node.node_ancestors():
        if _annotates(parent, child) or (
            isinstance(parent, If) and is_type_checking(parent.test, modules) and any(child is s for s in parent.body)
        ):
            return True
        child = parent
    return False


def _annotates(parent: NodeNG, child: NodeNG) -> bool:
    """Whether *child* is one of *parent*'s annotations: a parameter's, a return, a variable's."""
    if isinstance(parent, AnnAssign):
        return child is parent.annotation
    if isinstance(parent, FunctionDef):  # AsyncFunctionDef too
        return child is parent.returns
    if isinstance(parent, Arguments):
        annotations = (
            *parent.annotations,
            *parent.posonlyargs_annotations,
            *parent.kwonlyargs_annotations,
            parent.varargannotation,
            parent.kwargannotation,
        )
        return any(child is annotation for annotation in annotations)
    return False


def is_type_checking(test: NodeNG, modules: frozenset[str]) -> bool:
    """Whether an ``if`` test is ``TYPE_CHECKING`` alone, as type checkers read it, or a module's.

    The name, wherever it comes from, or the attribute of a module the file imports at module level
    (``typing.TYPE_CHECKING``, ``t.TYPE_CHECKING`` after ``import typing as t``): another object's (a
    ``settings`` instance) may be true at runtime. Anything else may run: an ``else``, ``not TYPE_CHECKING``,
    ``TYPE_CHECKING or X``. An ``elif`` is an ``if`` in astroid's ``orelse``, so it counts too.
    """
    if isinstance(test, Name):
        return test.name == "TYPE_CHECKING"
    return (
        isinstance(test, Attribute)
        and test.attrname == "TYPE_CHECKING"
        and isinstance(test.expr, Name)
        and test.expr.name in modules
    )
