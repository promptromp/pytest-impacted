"""Python code parsing (AST) utilities."""

import logging
import os
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
    try:
        source = Path(file_path).read_text(encoding="utf-8-sig")  # -sig: drop a BOM, as ruff does
    except (OSError, UnicodeDecodeError):
        logger.error("Error reading file %s", file_path)
        return []

    if not source.strip():
        return []

    package = _package_of(module_name, is_package)

    try:
        tree = astroid.parse(source)
    except astroid.exceptions.AstroidSyntaxError:
        logger.warning("Syntax error while parsing %s", file_path)
        return []

    imports: set[str] = set()
    for node in tree.nodes_of_class((Import, ImportFrom)):
        imports.update(_extract_imports_from_node(node, package))

    return sorted(imports)


def is_test_module(module_name: str) -> bool:
    """Check if a module is a test module using naming conventions.

    Heuristics:
    - Module name starts with 'test_'
    - Module name ends with '_test'
    - Module path contains 'test' or 'tests' directory

    Args:
        module_name: Fully qualified module name (e.g., 'package.tests.test_foo')

    Returns:
        True if the module appears to be a test module
    """
    module_parts = module_name.split(".")
    last_part = module_parts[-1] if module_parts else ""

    # Check naming patterns
    is_test = (
        last_part.startswith("test_")
        or last_part.endswith("_test")
        or "test" in module_parts
        or "tests" in module_parts
    )

    logger.debug("Module %s is a test module: %s", module_name, is_test)
    return is_test
