"""Python package and module traversal utilities."""

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
    """Convert a directory path to a dotted package name.

    Uses pure path manipulation — no imports are performed.
    E.g. "tests" -> "tests", "tests/unit" -> "tests.unit".
    """
    normalized = os.path.normpath(str(path))
    return ".".join(Path(normalized).parts)


def _find_non_package_prefix(fs_path: str) -> tuple[str, str]:
    """Split a filesystem path into non-package prefix and importable package root.

    Directories that do not contain ``__init__.py`` are treated as non-package
    path prefixes (e.g. the ``src/`` in a src-layout project).

    Returns:
        A ``(prefix, importable_root)`` tuple.

        * ``'src/predicated'`` → ``('src', 'predicated')``  when ``src/`` has no ``__init__.py``
        * ``'mypackage'``      → ``('', 'mypackage')``      when ``mypackage/`` has ``__init__.py``
        * ``'src/lib/pkg'``    → ``('src/lib', 'pkg')``     when neither ``src/`` nor ``src/lib/`` has ``__init__.py``
    """
    parts = Path(fs_path).parts
    for i in range(len(parts)):
        candidate = Path(*parts[: i + 1])
        if (candidate / "__init__.py").exists():
            if i == 0:
                return "", fs_path
            prefix = str(Path(*parts[:i]))
            rest = str(Path(*parts[i:]))
            return prefix, rest
    # No __init__.py found at any level — treat whole path as importable (namespace package fallback)
    return "", fs_path


def iter_namespace(ns_package: str | types.ModuleType, *, scan_path: str | None = None) -> list[pkgutil.ModuleInfo]:
    """Iterate over all submodules of a namespace package.

    :param ns_package: namespace package (name or actual module)
    :param scan_path: optional filesystem path to scan instead of deriving from *ns_package*.
        When provided, the filesystem search uses *scan_path* while module name
        prefixes are still derived from *ns_package*.
    """
    logging.debug("Iterating over namespace for package: %s", ns_package)

    match ns_package:
        case str():
            path = [scan_path or package_name_to_path(ns_package)]
            prefix = f"{ns_package}."
        case types.ModuleType():
            path = list(ns_package.__path__)
            prefix = f"{ns_package.__name__}."
        case _:
            raise ValueError(f"Invalid namespace package: {ns_package}")

    module_infos = list(pkgutil.iter_modules(path=path, prefix=prefix))

    logging.debug("Materialized module_infos: %s", module_infos)

    return module_infos


def _discover_via_pkgutil(package: str) -> dict[str, str]:
    """Discover submodules using pkgutil (requires __init__.py in directories).

    Handles src-layout projects by detecting non-package prefix directories
    (e.g. ``src/``) and stripping them from module names while keeping them
    in filesystem paths.
    """
    fs_path = package_name_to_path(package)
    non_pkg_prefix, importable_path = _find_non_package_prefix(fs_path)
    importable_name = path_to_package_name(importable_path)
    return _discover_pkgutil_impl(importable_name, fs_path, non_pkg_prefix)


def _discover_pkgutil_impl(module_name: str, scan_path: str, non_pkg_prefix: str) -> dict[str, str]:
    """Recursive implementation of pkgutil-based submodule discovery.

    Args:
        module_name: Dotted importable module name used as prefix (e.g. ``"predicated"``).
        scan_path: Filesystem path to scan (e.g. ``"src/predicated"``).
        non_pkg_prefix: Non-package path prefix to prepend when constructing file paths
            (e.g. ``"src"``).  Empty string when there is no prefix.
    """
    results: dict[str, str] = {}
    for module_info in iter_namespace(module_name, scan_path=scan_path):
        name = module_info.name
        if name not in results:
            # Construct file path: prepend the non-package prefix to module parts
            module_parts = name.split(".")
            if non_pkg_prefix:
                file_parts = list(Path(non_pkg_prefix).parts) + module_parts
            else:
                file_parts = module_parts

            if module_info.ispkg:
                file_path = os.path.join(*file_parts, "__init__.py")
            else:
                file_path = os.path.join(*file_parts) + ".py"

            abs_path = os.path.abspath(file_path)
            if os.path.exists(abs_path):
                results[name] = abs_path
            else:
                logging.warning("Module %s not found at expected path %s", name, abs_path)

            if module_info.ispkg:
                sub_scan_path = os.path.join(scan_path, module_parts[-1])
                results.update(_discover_pkgutil_impl(name, sub_scan_path, non_pkg_prefix))

    return results


def _discover_via_filesystem(package: str) -> dict[str, str]:
    """Discover submodules by walking the filesystem (no __init__.py required).

    Uses Path.rglob to find all .py files regardless of whether intermediate
    directories contain __init__.py. This matches pytest's own filesystem-based
    test discovery behavior.
    """
    base_path = Path(package_name_to_path(package))
    if not base_path.is_dir():
        return {}

    results: dict[str, str] = {}
    for py_file in base_path.rglob("*.py"):
        rel = py_file.relative_to(base_path.parent)
        if py_file.name == "__init__.py":
            module_name = ".".join(rel.parent.parts)
        else:
            module_name = ".".join(rel.with_suffix("").parts)

        abs_path = str(py_file.resolve())
        results[module_name] = abs_path

    return results


@lru_cache
def discover_submodules(package: str, require_init: bool = True) -> dict[str, str]:
    """Discover all submodules by filesystem scanning, without importing them.

    This avoids executing module-level code (e.g. gevent monkey patching,
    application factory calls, global connections) that can corrupt the test
    environment when modules are eagerly imported.

    Args:
        package: Dotted package name (or path-style name like ``"src.predicated"``)
            to scan.  For src-layout projects, non-package prefix directories
            are automatically detected and stripped from module names.
        require_init: If True, use pkgutil-based discovery which requires
            __init__.py in directories (correct for importable Python packages).
            If False, use filesystem walking which finds all .py files
            regardless of __init__.py (matching pytest's discovery behavior).

    Returns:
        Dict mapping fully-qualified module name -> absolute file path.
    """
    if require_init:
        return _discover_via_pkgutil(package)
    else:
        return _discover_via_filesystem(package)


def resolve_files_to_modules(filenames: list[str], ns_module: str, tests_package: str | None = None):
    """Resolve file paths to their corresponding Python module names.

    Uses filesystem-based discovery (no imports) to build the module mapping.
    """
    submodules = discover_submodules(ns_module, require_init=True)
    if tests_package:
        logging.debug("Adding modules from tests_package: %s", tests_package)
        test_submodules = discover_submodules(tests_package, require_init=False)
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
    submodules = discover_submodules(ns_module, require_init=True)
    if tests_package:
        submodules = {**submodules, **discover_submodules(tests_package, require_init=False)}

    result = []
    for module_name in modules:
        if module_name in submodules:
            result.append(submodules[module_name])
        else:
            logging.warning("Module %s not found in discovered submodules", module_name)
    return result
