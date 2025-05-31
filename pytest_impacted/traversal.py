"""Python package and module traversal utilities."""

import importlib
import logging
import pkgutil
import types
from functools import lru_cache
from pathlib import Path


def package_name_to_path(package_name: str) -> str:
    """Convert a package name to a path."""
    return package_name.replace(".", "/")


def path_to_package_name(path: Path | str) -> str:
    """Convert a path to a package name."""
    if not isinstance(path, Path):
        path = Path(path)

    return importlib.import_module(path.name).__name__


def iter_namespace(ns_package: str | types.ModuleType) -> list[pkgutil.ModuleInfo]:
    """iterate over all submodules of a namespace package.

    :param ns_package: namespace package (name or actual module)
    :type ns_package: str | module
    :rtype: iterable[types.ModuleType]

    """
    logging.debug("Iterating over namespace for package: %s", ns_package)

    match ns_package:
        case str():
            path = [package_name_to_path(ns_package)]
            prefix = f"{ns_package}."
        case types.ModuleType():
            path = list(ns_package.__path__)
            prefix = f"{ns_package.__name__}."
        case _:
            raise ValueError(f"Invalid namespace package: {ns_package}")

    module_infos = list(pkgutil.iter_modules(path=path, prefix=prefix))

    logging.debug("Materialized module_infos: %s", module_infos)

    return module_infos


@lru_cache
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
                logging.exception(
                    "Encountered ModuleNotFoundError while trying to import module from name: %s",
                    name,
                )
                continue

            if hasattr(results[name], "__path__"):
                # Recursively import submodules
                results.update(import_submodules(name))

    return results


def resolve_files_to_modules(filenames: list[str], ns_module: str, tests_package: str | None = None):
    """Resolve file paths to their corresponding Python module objects."""
    # Get the path to the package
    path = Path(importlib.import_module(ns_module).__path__[0])
    submodules = import_submodules(ns_module)
    if tests_package:
        logging.debug("Adding modules from tests_package: %s", tests_package)
        test_submodules = import_submodules(tests_package)
        submodules.update(test_submodules)

    resolved_modules = []
    for file in filenames:
        # Check if the file is a Python module
        if file.endswith(".py"):
            # TODO: Refactor this to use the path_to_package_name function ideally.
            module_name = file.replace(str(path), "").replace("/", ".").replace(".py", "").lstrip(".")

            if module_name in submodules:
                resolved_modules.append(module_name)
            else:
                logging.warning(
                    "Module %s not found in submodules",
                    module_name,
                )

    return resolved_modules


def resolve_modules_to_files(modules: list[str]) -> list:
    """Resolve module names to their corresponding file paths."""
    return [importlib.import_module(module_path).__file__ for module_path in modules]
