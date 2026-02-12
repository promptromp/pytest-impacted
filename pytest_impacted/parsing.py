"""Python code parsing (AST) utilities."""

import importlib.util
import inspect
import logging
import os
import types
from pathlib import Path
from typing import Any

import astroid


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


def should_silently_ignore_oserror(file_path: str) -> bool:
    """Check if the file should be silently ignored.

    Zero-byte files (often __init__.py) raise OSError in inspect.getsource().
    We silently ignore these cases.

    Args:
        file_path: Path to the file to check

    Returns:
        True if the file has zero bytes and should be ignored
    """
    return os.stat(file_path).st_size == 0


def _resolve_relative_import(module: types.ModuleType, node: astroid.ImportFrom) -> str:
    """Resolve a relative import to its absolute module path.

    Args:
        module: The module containing the relative import
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

        if len(package_parts) >= levels_to_go_up:
            base_package_parts = package_parts[:-levels_to_go_up]
        else:
            base_package_parts = []

        base_package = ".".join(base_package_parts) if base_package_parts else ""

    # Resolve the module name
    if node.modname:
        # from .module import something
        return f"{base_package}.{node.modname}" if base_package else node.modname
    else:
        # from . import something
        return base_package


def _extract_imports_from_node(node: astroid.Import | astroid.ImportFrom, module: types.ModuleType) -> set[str]:
    """Extract import module names from an AST node.

    Args:
        node: The import AST node
        module: The module being parsed (for context)

    Returns:
        Set of imported module names
    """
    imports = set()

    if isinstance(node, astroid.Import):
        for name in node.names:
            imports.add(name[0])

    elif isinstance(node, astroid.ImportFrom):
        # Handle relative imports
        if node.level and node.level > 0:
            resolved_modname = _resolve_relative_import(module, node)
        else:
            # Absolute import
            resolved_modname = node.modname

        # Check if imported names are modules or just symbols
        for name, *_ in node.names:
            full_name = f"{resolved_modname}.{name}" if resolved_modname else name
            if is_module_path(full_name, package=module.__name__):
                imports.add(full_name)
            else:
                imports.add(resolved_modname)

    return imports


def parse_module_imports(module: types.ModuleType) -> list[str]:
    """Parse the module to find all import statements.

    Args:
        module: The module to parse for imports

    Returns:
        List of imported module names (absolute paths)

    Raises:
        OSError: If source code cannot be retrieved (except for zero-byte files)
    """
    # Get the source code of the module
    source = None
    try:
        source = inspect.getsource(module)
    except OSError:
        if module.__file__ and should_silently_ignore_oserror(module.__file__):
            return []
        else:
            logging.error("Exception raised while trying to get source code for module %s", module)
            raise

    if not source:
        return []

    # Parse the source code into an AST
    tree = astroid.parse(source)

    # Find all import statements in the AST (including nested ones in try/except, if blocks, etc.)
    imports = set()
    for node in tree.nodes_of_class((astroid.Import, astroid.ImportFrom)):
        imports.update(_extract_imports_from_node(node, module))

    return sorted(list(imports))


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
        logging.error("Error reading file %s", file_path)
        return []

    if not source.strip():
        return []

    module_proxy = _ModuleProxy(module_name, is_package=is_package)

    try:
        tree = astroid.parse(source)
    except astroid.exceptions.AstroidSyntaxError:
        logging.warning("Syntax error while parsing %s", file_path)
        return []

    imports: set[str] = set()
    for node in tree.nodes_of_class((astroid.Import, astroid.ImportFrom)):
        imports.update(_extract_imports_from_node(node, module_proxy))  # type: ignore[arg-type]

    return sorted(imports)


def is_module_path(module_path: str, package: str | None = None) -> bool:
    """
    Checks if a given string represents a valid module path.

    Args:
        module_path: The string representing the module path (e.g., "pkg.foo.bar").
        package: The package to search for the module in. used for relative imports.

    Returns:
        True if the path points to a module, False otherwise.
    """
    try:
        spec = importlib.util.find_spec(module_path, package=package)
        return spec is not None
    except ModuleNotFoundError:
        return False
    except ValueError:
        # find_spec raises ValueError for invalid module names (empty strings, leading dots, etc.)
        logging.debug(
            "ValueError while trying to find spec for module %s in package %s",
            module_path,
            package,
        )
        return False
    except ImportError:
        logging.exception(
            "ImportError while trying to find spec for module %s in package %s",
            module_path,
            package,
        )
        return False


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

    logging.debug("Module %s is a test module: %s", module_name, is_test)
    return is_test
