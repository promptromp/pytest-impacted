"""Python package and module traversal utilities."""

import importlib
import logging
import os
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
def discover_submodules(package: str) -> dict[str, str]:
    """Discover all submodules by filesystem scanning, without importing them.

    Uses pkgutil.iter_modules for directory scanning and constructs file paths
    from module names. This avoids executing module-level code (e.g. gevent
    monkey patching, application factory calls, global connections) that can
    corrupt the test environment when modules are eagerly imported.

    Returns:
        Dict mapping fully-qualified module name -> absolute file path.
    """
    results: dict[str, str] = {}
    for module_info in iter_namespace(package):
        name = module_info.name
        if name not in results:
            # Construct file path from module name
            parts = name.split(".")
            if module_info.ispkg:
                file_path = os.path.join(*parts, "__init__.py")
            else:
                file_path = os.path.join(*parts) + ".py"

            abs_path = os.path.abspath(file_path)
            if os.path.exists(abs_path):
                results[name] = abs_path
            else:
                logging.warning("Module %s not found at expected path %s", name, abs_path)

            if module_info.ispkg:
                results.update(discover_submodules(name))

    return results


def resolve_files_to_modules(filenames: list[str], ns_module: str, tests_package: str | None = None):
    """Resolve file paths to their corresponding Python module names.

    Uses filesystem-based discovery (no imports) to build the module mapping.
    """
    submodules = discover_submodules(ns_module)
    if tests_package:
        logging.debug("Adding modules from tests_package: %s", tests_package)
        test_submodules = discover_submodules(tests_package)
        submodules = {**submodules, **test_submodules}

    # Build reverse mapping: absolute file path -> module name
    path_to_module = {path: name for name, path in submodules.items()}

    resolved_modules = []
    for file in filenames:
        if not file.endswith(".py"):
            continue

        abs_path = os.path.abspath(file)
        if abs_path in path_to_module:
            resolved_modules.append(path_to_module[abs_path])
        else:
            logging.warning(
                "File %s could not be resolved to a known module",
                file,
            )

    return resolved_modules


def resolve_modules_to_files(
    modules: list[str],
    ns_module: str,
    tests_package: str | None = None,
) -> list[str]:
    """Resolve module names to their corresponding file paths.

    Uses filesystem-based discovery (no imports) to find module files.
    """
    submodules = discover_submodules(ns_module)
    if tests_package:
        submodules = {**submodules, **discover_submodules(tests_package)}

    result = []
    for module_name in modules:
        if module_name in submodules:
            result.append(submodules[module_name])
        else:
            logging.warning("Module %s not found in discovered submodules", module_name)
    return result
