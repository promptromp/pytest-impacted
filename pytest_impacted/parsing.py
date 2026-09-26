"""Python code parsing (AST) utilities."""

import ast
import logging
import os
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import astroid
from astroid.nodes import Import, ImportFrom


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
    """A source file's text, or ``None`` if it cannot be read.

    ``utf-8-sig`` drops a BOM, as ruff does — a parity point with the Rust backend.
    """
    try:
        return Path(file_path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


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


def parse_file_imports(file_path: str, module_name: str, is_package: bool = False) -> list[str]:
    """Parse imports from a source file without importing the module.

    Reads the file directly and resolves relative imports from *module_name*
    and *is_package* alone, so no module-level code ever executes.

    Args:
        file_path: Absolute path to the ``.py`` file.
        module_name: Fully-qualified module name (e.g. ``"pkg.sub.mod"``).
        is_package: ``True`` when the file is an ``__init__.py``.

    Returns:
        Sorted list of *candidate* absolute module names. ``from pkg import
        name`` contributes both ``pkg`` and ``pkg.name`` because the parser
        cannot tell a submodule from a symbol without importing ``pkg``;
        callers filter against :func:`~pytest_impacted.traversal.discover_submodules`
        as :func:`~pytest_impacted.graph.build_dep_tree` does.
    """
    source = read_source(file_path)
    if source is None:
        logger.error("Error reading file %s", file_path)
        return []
    if not source.strip():
        return []

    package = _package_of(module_name, is_package)

    try:
        with _quiet_parse():
            tree = astroid.parse(source)
    except astroid.exceptions.AstroidSyntaxError:
        logger.warning("Syntax error while parsing %s", file_path)
        return []

    imports: set[str] = set()
    for node in tree.nodes_of_class((Import, ImportFrom)):
        imports.update(_extract_imports_from_node(node, package))

    return sorted(imports)


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
    source = read_source(file_path)
    if source is None or "pytest_plugins" not in source:  # cheap pre-filter
        return []
    try:
        with _quiet_parse():
            tree = ast.parse(source)
        return [name for stmt in _module_level_statements(tree.body) for name in _declared_plugins(stmt)]
    except (SyntaxError, ValueError, RecursionError):  # RecursionError: e.g. a 3000-term `+` chain
        return []


_BLOCK_FIELDS = ("body", "cases", "handlers", "orelse", "finalbody")  # source order
_BLOCKS = (ast.If, ast.Try, ast.TryStar, ast.With, ast.ExceptHandler, ast.Match, ast.match_case, ast.For, ast.While)


def _module_level_statements(body: list) -> Iterator[ast.stmt]:
    """Statements that run at import, including nested blocks — but not functions or classes."""
    for stmt in body:
        if isinstance(stmt, ast.stmt):
            yield stmt
        if isinstance(stmt, _BLOCKS):
            for field in _BLOCK_FIELDS:
                yield from _module_level_statements(getattr(stmt, field, []))


def _declared_plugins(stmt: ast.stmt) -> list[str]:
    """Plugin names one statement adds to ``pytest_plugins`` (the single matcher for every form)."""
    if isinstance(stmt, ast.Assign) and any(_is_plugins_name(t) for t in stmt.targets):
        return _plugin_specs(stmt.value)
    if isinstance(stmt, ast.AnnAssign | ast.AugAssign) and _is_plugins_name(stmt.target) and stmt.value:
        return _plugin_specs(stmt.value)
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(call := stmt.value, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and _is_plugins_name(call.func.value)
        and call.func.attr in ("append", "extend")
        and call.args
    ):
        # append takes one name (never comma-split); extend takes a sequence.
        return _plugin_specs(call.args[0]) if call.func.attr == "extend" else _string_items([call.args[0]])
    return []


def _is_plugins_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "pytest_plugins"


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
