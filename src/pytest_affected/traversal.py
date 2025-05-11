"""Python package and module traversal utilities."""

import importlib
import pkgutil
import types
from pathlib import Path
from typing import Iterator


def iter_namespace(ns_package: str | types.ModuleType) -> Iterator:
    """iterate over all submodules of a namespace package.

    :param ns_package: namespace package (name or actual module)
    :type ns_package: str | module
    :rtype: iterable[types.ModuleType]

    """
    if isinstance(ns_package, str):
        # If the input is a string, materialize it by importing the module first
        ns_package = importlib.import_module(ns_package)

    return pkgutil.iter_modules(ns_package.__path__, ns_package.__name__ + ".")


def import_submodules(package: str | types.ModuleType) -> dict[str, types.ModuleType]:
    """Import all submodules of a module, recursively, including subpackages,
    and return a dict mapping their fully-qualified names to the module object.

    :param package: package (name or actual module)
    :type package: str | module
    :rtype: dict[str, types.ModuleType]

    """
    results = {}
    for module_info in iter_namespace(package):
        name = module_info.name
        if name not in results:
            try:
                results[name] = importlib.import_module(name)
            except ModuleNotFoundError:
                continue
            if hasattr(results[name], "__path__"):
                results.update(import_submodules(name))
    return results


def resolve_files_to_modules(filenames: list[str], ns_module: str):
    """Resolve file paths to their corresponding Python module objects."""
    # Get the path to the package
    path = Path(importlib.import_module(ns_module).__path__[0])
    submodules = import_submodules(ns_module)
    resolved_modules = []
    for file in filenames:
        # Check if the file is a Python module
        if file.endswith(".py"):
            module_name = (
                file.replace(str(path), "").replace("/", ".").replace(".py", "")
            )
            if module_name in submodules:
                resolved_modules.append(module_name)

    return resolved_modules


def resolve_modules_to_files(modules: list[str]) -> list:
    """Resolve module names to their corresponding file paths."""
    return [importlib.import_module(module_path).__file__ for module_path in modules]
