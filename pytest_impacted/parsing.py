"""Python code parsing (AST) utilities."""

import importlib.util
import inspect
import logging
import os
import types
from pathlib import Path
from typing import Any

import astroid


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
    """Check if the file should be silently ignored."""
    # Nb. __init__ files often have zero bytes in which case inspect.getsource()
    # raises an OSError. we ignore those cases as well as any other file thats explicitly
    # zero bytes in size.
    return any((os.stat(file_path).st_size == 0,))


def parse_module_imports(module: types.ModuleType) -> list[str]:
    """Parse the module to find all import statements."""
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

    # Find all import statements in the AST
    imports = set()
    for node in tree.body:
        if isinstance(node, astroid.Import):
            for name in node.names:
                imports.add(name[0])
        elif isinstance(node, astroid.ImportFrom):
            # Nb. with `from x import y` statements we need to check
            # if x.y is a module path or if y is a function/class/variable.
            for name, *_ in node.names:
                full_name = f"{node.modname}.{name}"
                if is_module_path(full_name, package=module.__name__):
                    imports.add(full_name)
                else:
                    imports.add(node.modname)

    return list(imports)


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
    except ImportError:
        logging.exception(
            "ImportError while trying to find spec for module %s in package %s",
            module_path,
            package,
        )
        return False


def is_test_module(module_name):
    """Check if a module is a test module using a battery of heuristics.

    Currently this simply looks at file / modul name conventions, but
    could be extended to look at the contents of the module and use
    static analysis (AST) to determine if the module is a test module.

    """
    module_name_chunks = module_name.split(".")

    match module_name_chunks:
        case _ if module_name_chunks[-1].startswith("test_"):
            is_test = True

        case _ if module_name_chunks[-1].endswith("_test"):
            is_test = True

        case _ if "test" in module_name_chunks:
            is_test = True

        case _ if "tests" in module_name_chunks:
            is_test = True

        case _:
            is_test = False

    logging.debug("Module %s is a test module: %s", module_name, is_test)

    return is_test
