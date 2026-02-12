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
            except Exception:
                logging.exception(
                    "Encountered error while trying to import module from name: %s",
                    name,
                )
                continue

            if hasattr(results[name], "__path__"):
                # Recursively import submodules
                results.update(import_submodules(name))

    return results


def resolve_files_to_modules(filenames: list[str], ns_module: str, tests_package: str | None = None):
    """Resolve file paths to their corresponding Python module objects."""
    submodules = import_submodules(ns_module)
    if tests_package:
        logging.debug("Adding modules from tests_package: %s", tests_package)
        test_submodules = import_submodules(tests_package)
        submodules.update(test_submodules)

    # Build a mapping of package name -> package root path for resolution
    package_paths: list[tuple[str, Path]] = []

    ns_mod = importlib.import_module(ns_module)
    ns_path = Path(ns_mod.__path__[0])
    package_paths.append((ns_module, ns_path))

    if tests_package:
        try:
            tests_mod = importlib.import_module(tests_package)
            tests_path = Path(tests_mod.__path__[0])
            package_paths.append((tests_package, tests_path))
        except (ModuleNotFoundError, AttributeError):
            logging.warning("Could not import tests_package: %s", tests_package)

    resolved_modules = []
    for file in filenames:
        if not file.endswith(".py"):
            continue

        file_path = Path(file)
        resolved = False

        # Try matching against each known package path
        for pkg_name, pkg_path in package_paths:
            try:
                rel = file_path.relative_to(pkg_path)
            except ValueError:
                continue

            # Convert relative path to module name
            parts = list(rel.parts)
            # Remove .py extension from last part
            if parts:
                parts[-1] = parts[-1].removesuffix(".py")
            # Handle __init__.py -> use the package name itself
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]

            if parts:
                module_name = f"{pkg_name}.{'.'.join(parts)}"
            else:
                module_name = pkg_name

            if module_name in submodules:
                resolved_modules.append(module_name)
                resolved = True
                break

        if resolved:
            continue

        # Fallback: try matching relative paths (e.g. "pytest_impacted/foo.py" from git)
        # by checking if the file path contains a known package directory name
        for pkg_name, pkg_path in package_paths:
            pkg_dir_name = pkg_path.name
            file_str = str(file_path)
            # Find the package directory in the file path
            sep = "/"
            prefix = pkg_dir_name + sep
            if file_str.startswith(prefix) or (sep + prefix) in file_str:
                # Extract the part starting from the package directory
                idx = file_str.find(prefix)
                if idx >= 0:
                    rel_from_pkg = file_str[idx + len(prefix) :]
                    rel_path = Path(rel_from_pkg)
                    parts = list(rel_path.parts)
                    if parts:
                        parts[-1] = parts[-1].removesuffix(".py")
                    if parts and parts[-1] == "__init__":
                        parts = parts[:-1]

                    if parts:
                        module_name = f"{pkg_name}.{'.'.join(parts)}"
                    else:
                        module_name = pkg_name

                    if module_name in submodules:
                        resolved_modules.append(module_name)
                        resolved = True
                        break

        if not resolved:
            logging.warning(
                "File %s could not be resolved to a known module",
                file,
            )

    return resolved_modules


def resolve_modules_to_files(modules: list[str]) -> list:
    """Resolve module names to their corresponding file paths."""
    return [importlib.import_module(module_path).__file__ for module_path in modules]
