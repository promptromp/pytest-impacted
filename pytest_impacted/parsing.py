"""Python code parsing (AST) utilities."""

import ast
import io
import logging
import os
import tokenize
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import astroid
from astroid.nodes import Import, ImportFrom

from pytest_impacted.type_only import BLOCK_FIELDS, type_only_imports


logger = logging.getLogger(__name__)


def normalize_path(path_like: str | os.PathLike[str]) -> Path:
    """Normalize a string or any :class:`os.PathLike` (``pathlib.Path``, ``py.path.local``) to a Path.

    Raises:
        ValueError: If the object is not path-like.
    """
    if isinstance(path_like, Path):
        return path_like
    try:
        return Path(os.fspath(path_like))
    except TypeError as e:
        raise ValueError(f"Cannot normalize path-like object {path_like!r} of type {type(path_like)}") from e


def read_source(file_path: str) -> str | None:
    """A source file's text, decoded as Python decodes it, or ``None`` if it cannot be read.

    By its PEP 263 coding cookie (``# -*- coding: latin-1 -*-``), else as UTF-8, a BOM dropped as ruff drops it — a
    parity point with the Rust backend, which hands a file not in UTF-8 back to this side.
    """
    try:
        data = Path(file_path).read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        return data.decode(encoding)
    except (OSError, SyntaxError, LookupError, ValueError):  # unreadable; an unknown or wrong encoding
        return None  # ValueError: UnicodeDecodeError, and the plain UnicodeError some codecs raise (punycode)


@contextmanager
def _quiet_parse() -> Iterator[None]:
    """Silence warnings while parsing source.

    An invalid escape such as ``"\\d"`` is a SyntaxWarning, which ``-W error`` or
    ``filterwarnings = error`` turns into a SyntaxError — silently dropping every
    import in the file. The warning is the user's to see when Python compiles the
    module, not ours to raise while only reading it.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def is_package_file(path: str) -> bool:
    """Whether the file at *path* is a package's ``__init__.py`` — by its name, not a suffix (``test__init__.py``)."""
    return os.path.basename(path) == "__init__.py"


def is_conftest_module(module_name: str) -> bool:
    """Whether a dotted module name names a ``conftest`` (by name; callers check the file where it matters)."""
    return module_name.rpartition(".")[2] == "conftest"


def _package_of(module_name: str, is_package: bool) -> str:
    """The package that *module_name*'s relative imports resolve against.

    A package's own ``__init__.py`` resolves against itself; any other module
    resolves against its parent. Mirrors ``resolve_relative_import`` in the
    Rust backend.
    """
    return module_name if is_package else module_name.rpartition(".")[0]


def _resolve_relative_import(package: str, level: int, modname: str | None) -> str:
    """Resolve ``from <level dots><modname> import …`` to an absolute module name."""
    if level == 1:
        base_package = package
    else:
        # Each extra dot climbs one package.
        package_parts = package.split(".")
        levels_to_go_up = level - 1
        base_package_parts = package_parts[:-levels_to_go_up] if len(package_parts) >= levels_to_go_up else []
        base_package = ".".join(base_package_parts)

    if modname:
        return f"{base_package}.{modname}" if base_package else modname
    return base_package


def _extract_imports_from_node(node: Import | ImportFrom, package: str) -> set[str]:
    """Extract candidate module names from an import node.

    Args:
        node: The import AST node
        package: The package relative imports resolve against (see :func:`_package_of`)

    Returns:
        Candidate module names. For ``from pkg import name`` both ``pkg`` and
        ``pkg.name`` are returned: ``pkg`` is always a real dependency (its
        ``__init__`` runs on import), while ``pkg.name`` is a submodule only if
        the graph builder finds a module of that name — deciding here would
        mean importing ``pkg``, which must never happen at analysis time.
        Mirrors the Rust backend.
    """
    imports = set()

    if isinstance(node, Import):
        for name in node.names:
            imports.add(name[0])

    elif isinstance(node, ImportFrom):
        resolved_modname = (
            _resolve_relative_import(package, node.level, node.modname) if node.level else (node.modname or "")
        )
        if resolved_modname:
            imports.add(resolved_modname)
        for name, *_ in node.names:
            if name == "*":
                continue  # ``pkg.*`` is not a module name
            imports.add(f"{resolved_modname}.{name}" if resolved_modname else name)

    return imports


def parse_file_imports(
    file_path: str, module_name: str, is_package: bool = False, *, ignore_type_checking_imports: bool = False
) -> list[str]:
    """Parse imports from a source file without importing the module.

    Reads the file directly and resolves relative imports from *module_name*
    and *is_package* alone, so no module-level code ever executes.

    Args:
        file_path: Absolute path to the ``.py`` file.
        module_name: Fully-qualified module name (e.g. ``"pkg.sub.mod"``).
        is_package: ``True`` when the file is an ``__init__.py``.
        ignore_type_checking_imports: Leave out the type-only imports, those under ``if TYPE_CHECKING:``
            whose names only annotations use (see :mod:`pytest_impacted.type_only`).

    Returns:
        Sorted list of *candidate* absolute module names. ``from pkg import
        name`` contributes both ``pkg`` and ``pkg.name`` because the parser
        cannot tell a submodule from a symbol without importing ``pkg``;
        callers map them through the graph's aliases and keep those that are nodes;
        :func:`~pytest_impacted.graph.build_dep_tree` also looks the rest up on disk.
    """
    runtime, _ = split_file_imports(file_path, module_name, is_package, find_type_only=ignore_type_checking_imports)
    return runtime


def split_file_imports(
    file_path: str, module_name: str, is_package: bool = False, *, find_type_only: bool = True
) -> tuple[list[str], list[str]]:
    """The candidates of :func:`parse_file_imports`, split into runtime and type-only ones.

    The type-only ones come from type-only imports (see :mod:`pytest_impacted.type_only`) and no other
    import; with *find_type_only* false, every candidate is a runtime one. The graph links the runtime ones
    alone, but counts both as evidence of how the project imports its modules. Mirrors
    ``parse_file_imports_core`` in the Rust backend.
    """
    source = read_source(file_path)
    if source is None:
        logger.error("Error reading file %s", file_path)
        return [], []
    if not source.strip():
        return [], []

    package = _package_of(module_name, is_package)

    try:
        with _quiet_parse():
            tree = astroid.parse(source)
    except astroid.exceptions.AstroidSyntaxError:
        logger.warning("Syntax error while parsing %s", file_path)
        return [], []
    except RecursionError:
        # astroid's tree builder recurses in Python, giving up near 500 nested terms (a long ``a + b + ...`` in
        # generated code) where CPython's parser, and ruff, go on: read the imports as they do, every one at
        # runtime (no type-only split without astroid's tree; keeping an edge only selects more).
        return _imports_by_ast(source, file_path, package), []

    type_only = {id(node) for node in type_only_imports(tree)} if find_type_only else set()
    runtime: set[str] = set()
    typed: set[str] = set()
    for node in tree.nodes_of_class((Import, ImportFrom)):
        (typed if id(node) in type_only else runtime).update(_extract_imports_from_node(node, package))
    return sorted(runtime), sorted(typed - runtime)


def _imports_by_ast(source: str, file_path: str, package: str) -> list[str]:
    """The import candidates of *source* by the stdlib parser, as :func:`_extract_imports_from_node` reads them.

    ``ast.walk`` is iterative, so only the parse itself can give up; then the file imports nothing, logged.
    """
    try:
        with _quiet_parse():
            module = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as err:
        logger.warning("Could not parse %s: %s", file_path, type(err).__name__)
        return []
    found: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_relative_import(package, node.level, node.module) if node.level else (node.module or "")
            if base:
                found.add(base)
            found.update(f"{base}.{alias.name}" if base else alias.name for alias in node.names if alias.name != "*")
    return sorted(found)


def parse_pytest_plugins(file_path: str) -> list[str]:
    """Module names a file loads through a module-level ``pytest_plugins`` declaration.

    ``pytest_plugins = ["pkg.fixtures"]`` makes pytest import ``pkg.fixtures`` and
    register its fixtures and hooks, so it is a dependency exactly like an import —
    but as strings, which import parsing never sees. Follows what pytest accepts:
    a comma-separated string or a list/tuple of strings, assigned, annotated,
    extended with ``+=``, ``.append`` or ``.extend``, including inside
    module-level ``if``/``try``/``with``/``match``/``for``/``while`` blocks. Computed entries are ignored.

    Parsed with the stdlib ``ast`` and independent of the parsing backend, so both
    backends see the same edges.
    """
    return pytest_plugins_in(read_source(file_path))


def pytest_plugins_in(source: str | None) -> list[str]:
    """:func:`parse_pytest_plugins` of a source already read (``None``: unreadable)."""
    if source is None or "pytest_plugins" not in source:  # cheap pre-filter
        return []
    try:
        with _quiet_parse():
            tree = ast.parse(source)
        return [name for stmt in _module_level_statements(tree.body) for name in _declared_plugins(stmt)]
    # A 3000-term chain: RecursionError (``+``), or MemoryError ("parser stack overflowed", ``**``).
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []


_BLOCKS = (ast.If, ast.Try, ast.TryStar, ast.With, ast.ExceptHandler, ast.Match, ast.match_case, ast.For, ast.While)


def _module_level_statements(body: list) -> Iterator[ast.stmt]:
    """Statements that run at import, including nested blocks — but not functions or classes."""
    for stmt in body:
        if isinstance(stmt, ast.stmt):
            yield stmt
        if isinstance(stmt, _BLOCKS):
            for field in BLOCK_FIELDS:
                yield from _module_level_statements(getattr(stmt, field, []))


def _declared_plugins(stmt: ast.stmt) -> list[str]:
    """Plugin names one statement adds to ``pytest_plugins``."""
    return _declared(stmt, "pytest_plugins", _plugin_specs)


def _declared(stmt: ast.stmt, variable: str, specs: Callable[[ast.expr], list[str]]) -> list[str]:
    """The strings one statement adds to the module-level list *variable*, its values read by *specs*: the single
    matcher for every form — assigned, annotated, extended with ``+=``, ``.append`` or ``.extend``."""

    def named(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id == variable

    if isinstance(stmt, ast.Assign) and any(named(target) for target in stmt.targets):
        return specs(stmt.value)
    if isinstance(stmt, ast.AnnAssign | ast.AugAssign) and named(stmt.target) and stmt.value:
        return specs(stmt.value)
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(call := stmt.value, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and named(call.func.value)
        and call.func.attr in ("append", "extend")
        and call.args
    ):
        # append takes one name (never comma-split); extend takes a sequence.
        return specs(call.args[0]) if call.func.attr == "extend" else _string_items([call.args[0]])
    return []


def _may_star_import(source: str) -> bool:
    """Whether ``import`` is followed by ``*`` somewhere (spaces, tabs or a line continuation between): a scan of
    the few ``import`` words, cheaper than a regex over the whole source, which most files hold a ``*`` in."""
    start = source.find("import")
    while start != -1:
        end = start + len("import")
        while end < len(source) and source[end] in " \t\\\r\n":
            end += 1
        if end < len(source) and source[end] == "*":
            return True
        start = source.find("import", end)
    return False


def parse_star_imports(file_path: str, module_name: str, is_package: bool = False) -> list[str]:
    """The modules a file star-imports anywhere (``from pkg import *``), relative ones resolved from *module_name*.

    The import parsers skip a star (``pkg.*`` is no module); ``from pkg import *`` imports each submodule
    ``pkg.__all__`` names, though, and the graph links those (see :func:`parse_all_names`). Parsed with the stdlib
    ``ast`` outside the backends, as ``pytest_plugins`` declarations are, so both see the same edges.
    """
    return star_imports_in(read_source(file_path), module_name, is_package)


def star_imports_in(source: str | None, module_name: str, is_package: bool = False) -> list[str]:
    """:func:`parse_star_imports` of a source already read (``None``: unreadable)."""
    if source is None or not _may_star_import(source):  # cheap pre-filter: few files star-import
        return []
    try:
        with _quiet_parse():
            tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    package = _package_of(module_name, is_package)
    return sorted(
        {
            _resolve_relative_import(package, node.level, node.module) if node.level else (node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        }
        - {""}
    )


def parse_all_names(file_path: str) -> list[str]:
    """The names a module's literal ``__all__`` lists, as a star import of it reads them: a list or tuple of
    strings, assigned, extended (``+=``, ``.extend``, ``.append``, ``A + B``) at module level. Computed ones
    are none."""
    source = read_source(file_path)
    if source is None or "__all__" not in source:  # cheap pre-filter
        return []
    try:
        with _quiet_parse():
            tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    return [name for stmt in _module_level_statements(tree.body) for name in _declared(stmt, "__all__", _literals)]


def _literals(value: ast.expr) -> list[str]:
    """The strings of a literal sequence, or of a concatenation of them (``__all__`` never splits on commas)."""
    if isinstance(value, ast.List | ast.Tuple):
        return _string_items(value.elts)
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        return _literals(value.left) + _literals(value.right)
    return []


def _plugin_specs(value: ast.expr) -> list[str]:
    """What pytest makes of a ``pytest_plugins`` value: a string splits on commas, a sequence does not."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return [spec.strip() for spec in value.value.split(",") if spec.strip()]
    if isinstance(value, ast.List | ast.Tuple):
        return _string_items(value.elts)
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):  # BASE + ["extra"]
        return _plugin_specs(value.left) + _plugin_specs(value.right)
    return []


def _string_items(nodes: list[ast.expr]) -> list[str]:
    return [node.value for node in nodes if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def is_test_module(module_name: str) -> bool:
    """Check if a module is a test module using naming conventions.

    Heuristics:
    - Module name starts with 'test_'
    - Module name ends with '_test'
    - Module path contains 'test' or 'tests' directory
    - ...except ``conftest``, which pytest loads for fixtures and hooks and
      which holds no tests

    Args:
        module_name: Fully qualified module name (e.g., 'package.tests.test_foo')

    Returns:
        True if the module appears to be a test module
    """
    module_parts = module_name.split(".")
    last_part = module_parts[-1] if module_parts else ""

    # Check naming patterns
    is_test = not is_conftest_module(module_name) and (
        last_part.startswith("test_")
        or last_part.endswith("_test")
        or "test" in module_parts
        or "tests" in module_parts
    )

    logger.debug("Module %s is a test module: %s", module_name, is_test)
    return is_test
