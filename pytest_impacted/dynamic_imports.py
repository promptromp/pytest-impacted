"""Imports made by name: the literal module and object paths given to the known APIs that import them.

``importlib.import_module("pkg.mod")`` imports ``pkg.mod`` as surely as ``import pkg.mod`` does, and
``mock.patch("pkg.mod.func")`` imports ``pkg.mod`` to patch ``func`` in it. Both are dependencies that import
parsing never sees, and the latter appears in nearly every test suite. :data:`LOADERS` lists those APIs as
data: the standard library's importers, the mock patchers and pytest's helpers. One mechanism reads them all
(:func:`parse_dynamic_imports`). Only a literal names a module here; a name the code computes is not followed.

Parsed with the stdlib ``ast`` and independent of the parsing backend, as
:func:`~pytest_impacted.parsing.parse_pytest_plugins` is, so both backends see the same imports.
"""

import ast
import importlib.util
import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto
from typing import NamedTuple

from pytest_impacted.parsing import _package_of, _quiet_parse, read_source


class Names(Enum):
    """What a loader's string argument names."""

    MODULE = auto()  # a module: ``import_module("pkg.mod")``
    # An object, in the module its longest importable prefix names: ``resolve_name("pkg.mod.Class")``.
    OBJECT = auto()
    # An attribute of what the path before it names: ``patch("pkg.mod.func")`` replaces ``func`` on ``pkg.mod``.
    ATTRIBUTE = auto()


@dataclass(frozen=True)
class Argument:
    """One of a call's arguments, passed at its position or by its keyword."""

    position: int
    keyword: str


@dataclass(frozen=True)
class Loader:
    """An API that imports what one of its string arguments names.

    *callee* is matched against the end of the name a call calls, after resolving the module's import aliases.
    ``importlib.import_module`` matches that function under any name an absolute import gives it, but not one
    re-exported by another module, star-imported or assigned, whose origin is not followed. A bare ``patch``
    matches ``mock.patch``, ``unittest.mock.patch`` and ``mocker.patch`` alike: fixtures and context managers
    hand a patcher out under any name. *package* anchors a relative name. *fromlist* names submodules imported
    too. *level* makes the import relative to its caller's package unless it is 0, and a relative one is not
    followed.
    """

    callee: str
    name: Argument
    names: Names
    package: Argument | None = None
    fromlist: Argument | None = None
    level: Argument | None = None


LOADERS: tuple[Loader, ...] = (
    # The standard library's importers.
    Loader("importlib.import_module", Argument(0, "name"), Names.MODULE, package=Argument(1, "package")),
    Loader(
        "__import__", Argument(0, "name"), Names.MODULE, fromlist=Argument(3, "fromlist"), level=Argument(4, "level")
    ),
    Loader("runpy.run_module", Argument(0, "mod_name"), Names.MODULE),
    Loader("pkgutil.resolve_name", Argument(0, "name"), Names.OBJECT),
    # unittest.mock's patchers, also those of the ``mock`` backport and of pytest-mock's ``mocker``. ``patch``
    # splits its target as ``target.rsplit(".", 1)``; the others import theirs whole.
    Loader("patch", Argument(0, "target"), Names.ATTRIBUTE),
    Loader("patch.dict", Argument(0, "in_dict"), Names.OBJECT),
    Loader("patch.multiple", Argument(0, "target"), Names.OBJECT),
    # pytest's helpers: ``monkeypatch.setattr("pkg.mod.attr", value)`` imports ``pkg.mod``.
    Loader("pytest.importorskip", Argument(0, "modname"), Names.MODULE),
    Loader("setattr", Argument(0, "target"), Names.ATTRIBUTE),
    Loader("delattr", Argument(0, "target"), Names.ATTRIBUTE),
)


class DynamicImports(NamedTuple):
    """What a file's loader calls import.

    *modules*: modules, by name. *objects* and *attributes*: dotted paths to something inside a module, where
    only the project can tell which prefix is the module (``pkg.mod.Class.method``). An attribute's module is a
    prefix of the path before its last part.
    """

    modules: list[str]
    objects: list[str]
    attributes: list[str]


_CALLEES = frozenset(loader.callee for loader in LOADERS)
_BY_LAST_PART = {
    part: tuple(loader for loader in LOADERS if loader.callee.rpartition(".")[2] == part)
    for part in {callee.rpartition(".")[2] for callee in _CALLEES}
}
# A file that calls a loader spells its name: the last part, or the first callee it is reached through
# (``patch`` for ``patch.dict``). Only such files are parsed.
_SPELLINGS = sorted(callee.rpartition(".")[2] for callee in _CALLEES if callee.rpartition(".")[0] not in _CALLEES)
_SPELLED = re.compile(rf"\b(?:{'|'.join(map(re.escape, _SPELLINGS))})\b")


def parse_dynamic_imports(file_path: str, module_name: str, is_package: bool = False) -> DynamicImports:
    """The modules and object paths that *file_path*'s loader calls import (see :data:`LOADERS`).

    A call counts when it calls a loader and passes it a literal string spelling a dotted name. For an object
    path, that may be a module's name, then ``:``, then the dotted attributes. A relative module name resolves
    as ``importlib.util.resolve_name`` resolves it. The anchor is the call's literal package, the package of
    the module named *module_name* (``__package__``, ``__spec__.parent``), or that name itself
    (``__name__``). Nothing is ever imported.
    """
    source = read_source(file_path)
    if source is None or not _SPELLED.search(source):  # cheap pre-filter
        return DynamicImports([], [], [])
    try:
        with _quiet_parse():
            tree = ast.parse(source)
        found = set(_imports(tree, module_name, is_package))
    except (SyntaxError, ValueError, RecursionError):  # RecursionError: e.g. a 3000-term attribute chain
        return DynamicImports([], [], [])
    return DynamicImports(
        modules=sorted(name for kind, name in found if kind is Names.MODULE),
        objects=sorted(name for kind, name in found if kind is Names.OBJECT),
        attributes=sorted(name for kind, name in found if kind is Names.ATTRIBUTE),
    )


def _imports(tree: ast.Module, module_name: str, is_package: bool) -> Iterator[tuple[Names, str]]:
    """What each loader call in *tree* names, with what it names."""
    calls: list[ast.Call] = []
    imports: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):  # once: walking every node is most of the cost
        if isinstance(node, ast.Call):
            if node.args or node.keywords:
                calls.append(node)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            imports.append(node)
    bindings = _bindings(imports)
    for call in calls:
        for loader in _loaders_called(call.func, bindings):
            yield from _named(call, loader, module_name, is_package)


def _loaders_called(func: ast.expr, bindings: dict[str, set[str]]) -> Iterator[Loader]:
    """The loaders a call to *func* calls: those whose callee the name it calls ends with (see :class:`Loader`)."""
    if isinstance(func, ast.Attribute):
        last_parts = {func.attr}
    elif isinstance(func, ast.Name):
        last_parts = {func.id, *(bound.rpartition(".")[2] for bound in bindings.get(func.id, ()))}
    else:
        return
    # By the last part first: most calls call no loader, and resolving their whole name costs more.
    if candidates := [loader for part in last_parts for loader in _BY_LAST_PART.get(part, ())]:
        callees = _callees(func, bindings)
        for loader in candidates:
            if any(callee == loader.callee or callee.endswith("." + loader.callee) for callee in callees):
                yield loader


def _bindings(imports: list[ast.Import | ast.ImportFrom]) -> dict[str, set[str]]:
    """``{name: what it is bound to}`` by the module's absolute imports anywhere, in any scope.

    A union over scopes: a name bound in two places may call either, so either may be a loader.
    """
    bound: dict[str, set[str]] = {}
    for node in imports:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name if alias.asname else alias.name.partition(".")[0]
                bound.setdefault(alias.asname or name, set()).add(name)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bound.setdefault(alias.asname or alias.name, set()).add(f"{node.module}.{alias.name}")
    return bound


def _callees(func: ast.expr, bindings: dict[str, set[str]]) -> set[str]:
    """The dotted names a call to *func* may call: ``unittest.mock.patch.dict`` for ``mock.patch.dict``.

    A name no import binds stands for itself (a builtin, a fixture: ``mocker.patch``). An attribute of
    anything else stands for its attributes alone (``.setattr`` for ``MonkeyPatch().setattr``).
    """
    attributes = []
    while isinstance(func, ast.Attribute):  # a loop: an attribute chain may be thousands long
        attributes.append(func.attr)
        func = func.value
    suffix = "".join(f".{attribute}" for attribute in reversed(attributes))
    if isinstance(func, ast.Name):
        return {base + suffix for base in bindings.get(func.id, {func.id})}
    return {suffix}


def _named(call: ast.Call, loader: Loader, module_name: str, is_package: bool) -> Iterator[tuple[Names, str]]:
    """What *call* to *loader* imports, if its argument is a literal naming it."""
    text = _literal(_argument(call, loader.name))
    if text is None or (loader.level and not _is_zero(_argument(call, loader.level))):
        return
    if loader.names is not Names.MODULE:
        yield from _path(text, loader.names)
        return
    anchor = _anchor(_argument(call, loader.package), module_name, is_package) if loader.package else None
    if (name := _absolute(text, anchor)) is None:
        return
    yield Names.MODULE, name
    if loader.fromlist:
        items = _argument(call, loader.fromlist)
        for item in items.elts if isinstance(items, ast.List | ast.Tuple) else ():
            if (submodule := _literal(item)) is not None and submodule.isidentifier():
                yield Names.MODULE, f"{name}.{submodule}"


def _path(text: str, names: Names) -> Iterator[tuple[Names, str]]:
    """What an object or attribute path names: the module before a ``:``; else the path itself.

    An attribute path of one part names nothing: ``patch`` and ``monkeypatch`` split off its attribute and fail
    without a dot, and their rows match any receiver, so ``config.setattr("settings", value)`` is some other
    API's. An object path of one part is a module (``resolve_name("plugins")``).
    """
    module, colon, attributes = text.partition(":")
    if colon:
        if _dotted(module) and (not attributes or _dotted(attributes)):
            yield Names.MODULE, module
    elif _dotted(text):
        if "." in text:
            yield names, text
        elif names is Names.OBJECT:
            yield Names.MODULE, text


def _absolute(name: str, anchor: str | None) -> str | None:
    """The absolute module name *name* means, a relative one resolved against *anchor*; ``None`` if none."""
    if name.startswith("."):
        if anchor is None:
            return None
        try:
            name = importlib.util.resolve_name(name, anchor)  # string work alone: nothing is imported
        except (ImportError, ValueError):  # beyond the top-level package, or no anchor at all
            return None
    return name if _dotted(name) else None


def _anchor(node: ast.expr | None, module_name: str, is_package: bool) -> str | None:
    """The package a relative name resolves against: a literal, or the calling module's own package or name."""
    if (literal := _literal(node)) is not None:
        return literal
    if isinstance(node, ast.Name) and node.id == "__package__":
        return _package_of(module_name, is_package)
    if isinstance(node, ast.Name) and node.id == "__name__":
        return module_name
    if isinstance(node, ast.Attribute) and node.attr == "parent" and isinstance(node.value, ast.Name):
        return _package_of(module_name, is_package) if node.value.id == "__spec__" else None
    return None


def _argument(call: ast.Call, argument: Argument) -> ast.expr | None:
    """The expression *call* passes as *argument*, or ``None`` if it passes none.

    Where ``*args`` or ``**kwargs`` may pass it, the unpacked expression stands in: it cannot be read, like any
    computed argument. So a ``level`` they may pass is not taken for 0.
    """
    for keyword in call.keywords:
        if keyword.arg == argument.keyword:
            return keyword.value
    for position, arg in enumerate(call.args):
        if isinstance(arg, ast.Starred) or position == argument.position:
            return arg
    return next((keyword.value for keyword in call.keywords if keyword.arg is None), None)


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_zero(node: ast.expr | None) -> bool:
    """Whether a ``level`` argument is absent or a literal 0 (``False`` too, as ``__import__`` takes it)."""
    return node is None or (isinstance(node, ast.Constant) and type(node.value) in (int, bool) and node.value == 0)


def _dotted(text: str) -> bool:
    """Whether *text* spells a dotted name, such as a module's: ``pkg.mod``, not ``pkg-x.mod`` or ``/items``."""
    return bool(text) and all(part.isidentifier() for part in text.split("."))
