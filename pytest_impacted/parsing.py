"""Python code parsing (AST) utilities."""

import logging
import os
from pathlib import Path
from typing import Any

import astroid
from astroid.nodes import Import, ImportFrom


logger = logging.getLogger(__name__)


class _ModuleProxy:
    """Lightweight stand-in for a real module object.

    Provides just the ``__name__`` and ``__package__`` attributes needed by
    :func:`_resolve_relative_import` and :func:`_extract_imports_from_node`,
    without actually importing the module (avoiding side-effects).
    """

    __slots__ = ("__name__", "__package__")

    def __init__(self, name: str, *, is_package: bool = False) -> None:
        self.__name__ = name
        if is_package:
            self.__package__ = name
        elif "." in name:
            self.__package__ = name.rsplit(".", 1)[0]
        else:
            self.__package__ = ""


def normalize_path(path_like: Any) -> Path:
    """Normalize various path-like objects to pathlib.Path.

    Handles different path types that might be returned by GitPython:
    - Regular strings
    - pathlib.Path objects
    - py.path.local.LocalPath objects (with .strpath attribute)
    - Objects implementing the filesystem path protocol (__fspath__)

    Args:
        path_like: A path-like object of various types

    Returns:
        A pathlib.Path object

    Raises:
        ValueError: If the path cannot be normalized
    """
    if isinstance(path_like, Path):
        return path_like

    if hasattr(path_like, "strpath"):
        # py.path.local.LocalPath object
        return Path(path_like.strpath)

    if hasattr(path_like, "__fspath__"):
        # Objects implementing filesystem path protocol
        return Path(path_like.__fspath__())

    # Fallback: try string conversion
    try:
        return Path(str(path_like))
    except Exception as e:
        raise ValueError(f"Cannot normalize path-like object {path_like!r} of type {type(path_like)}") from e


def _resolve_relative_import(module: _ModuleProxy, node: ImportFrom) -> str:
    """Resolve a relative import to its absolute module path.

    Args:
        module: A module proxy providing __name__ and __package__ context
        node: The ImportFrom AST node with relative import

    Returns:
        The resolved absolute module name
    """
    # Get the package context from the module
    package = getattr(module, "__package__", None)
    if not package:
        # Fall back to getting package from module name
        package = module.__name__.rsplit(".", 1)[0] if "." in module.__name__ else ""

    # Calculate the base package for the relative import
    # Each level represents going up one package level
    if node.level == 1:
        # Single dot: same package
        base_package = package
    else:
        # Multiple dots: go up (level - 1) packages
        package_parts = package.split(".")
        levels_to_go_up = node.level - 1

        base_package_parts = package_parts[:-levels_to_go_up] if len(package_parts) >= levels_to_go_up else []

        base_package = ".".join(base_package_parts) if base_package_parts else ""

    # Resolve the module name
    if node.modname:
        # from .module import something
        return f"{base_package}.{node.modname}" if base_package else node.modname
    else:
        # from . import something
        return base_package


def _extract_imports_from_node(node: Import | ImportFrom, module: _ModuleProxy) -> set[str]:
    """Extract import module names from an AST node.

    Args:
        node: The import AST node
        module: A module proxy providing name/package context

    Returns:
        Set of imported module names
    """
    imports = set()

    if isinstance(node, Import):
        for name in node.names:
            imports.add(name[0])

    elif isinstance(node, ImportFrom):
        resolved_modname = _resolve_relative_import(module, node) if node.level and node.level > 0 else node.modname

        # ``from pkg import name`` may bind a submodule or a symbol. Telling
        # them apart would mean importing ``pkg`` (importlib.util.find_spec
        # imports every parent package), which must never happen at analysis
        # time. Emit both candidates instead: the graph builder keeps only the
        # ones that name discovered modules, so the extra edge to ``pkg``
        # itself is a deliberate false positive. Mirrors the Rust backend.
        if resolved_modname:
            imports.add(resolved_modname)
        for name, *_ in node.names:
            imports.add(f"{resolved_modname}.{name}" if resolved_modname else name)

    return imports


def parse_file_imports(file_path: str, module_name: str, is_package: bool = False) -> list[str]:
    """Parse imports from a source file without importing the module.

    Reads the file directly and uses a :class:`_ModuleProxy` to supply the
    module metadata required for relative-import resolution.  This avoids
    executing any module-level code.

    Args:
        file_path: Absolute path to the ``.py`` file.
        module_name: Fully-qualified module name (e.g. ``"pkg.sub.mod"``).
        is_package: ``True`` when the file is an ``__init__.py``.

    Returns:
        Sorted list of imported module names (absolute paths).
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        if os.path.exists(file_path) and os.stat(file_path).st_size == 0:
            return []
        logger.error("Error reading file %s", file_path)
        return []

    if not source.strip():
        return []

    module_proxy = _ModuleProxy(module_name, is_package=is_package)

    try:
        tree = astroid.parse(source)
    except astroid.exceptions.AstroidSyntaxError:
        logger.warning("Syntax error while parsing %s", file_path)
        return []

    imports: set[str] = set()
    for node in tree.nodes_of_class((Import, ImportFrom)):
        imports.update(_extract_imports_from_node(node, module_proxy))

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
