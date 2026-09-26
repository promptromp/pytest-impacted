"""Python package and module traversal utilities."""

import logging
import os
import pkgutil
from collections.abc import Collection, Iterable
from functools import lru_cache
from pathlib import Path


logger = logging.getLogger(__name__)


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


def canonical_root(root_dir: str | Path | None) -> Path:
    """Resolve *root_dir* (default: the current directory) to one canonical absolute path.

    Every discovered module path and every changed-file path is built from this
    single value, so the two always compare equal — including when the project
    is reached through a symlink.
    """
    return Path(root_dir if root_dir is not None else Path.cwd()).resolve()


def find_non_package_prefix(fs_path: str, root: Path) -> tuple[str, str]:
    """Split a filesystem path into non-package prefix and importable package root.

    Directories that do not contain ``__init__.py`` are treated as non-package
    path prefixes (e.g. the ``src/`` in a src-layout project). *fs_path* is
    relative to *root*.

    Returns:
        A ``(prefix, importable_root)`` tuple.

        * ``'src/predicated'`` → ``('src', 'predicated')``  when ``src/`` has no ``__init__.py``
        * ``'mypackage'``      → ``('', 'mypackage')``      when ``mypackage/`` has ``__init__.py``
        * ``'src/lib/pkg'``    → ``('src/lib', 'pkg')``     when neither ``src/`` nor ``src/lib/`` has ``__init__.py``
    """
    parts = Path(fs_path).parts
    for i in range(len(parts)):
        candidate = Path(*parts[: i + 1])
        if (root / candidate / "__init__.py").exists():
            if i == 0:
                return "", fs_path
            prefix = str(Path(*parts[:i]))
            rest = str(Path(*parts[i:]))
            return prefix, rest
    # No __init__.py found at any level — treat whole path as importable (namespace package fallback)
    return "", fs_path


def iter_namespace(ns_package: str, *, scan_path: str) -> list[pkgutil.ModuleInfo]:
    """Iterate over all submodules of a namespace package.

    :param ns_package: dotted package name, used only for the module-name prefix
    :param scan_path: absolute filesystem path to scan
    """
    logger.debug("Iterating over namespace for package: %s", ns_package)

    module_infos = list(pkgutil.iter_modules(path=[scan_path], prefix=f"{ns_package}."))

    logger.debug("Materialized module_infos: %s", module_infos)

    return module_infos


def _discover_via_pkgutil(package: str, root: Path) -> dict[str, str]:
    """Discover the modules importable under *package*, the way the import system names them.

    ``pkgutil`` lists modules and regular packages; :func:`_namespace_portions` adds
    the sub-directories without ``__init__.py`` it skips. Handles src-layout
    projects by detecting non-package prefix directories (e.g. ``src/``) and
    stripping them from module names while keeping them in filesystem paths.
    """
    fs_path = package_name_to_path(package)
    non_pkg_prefix, importable_path = find_non_package_prefix(fs_path, root)
    importable_name = path_to_package_name(importable_path)
    return _discover_pkgutil_impl(importable_name, fs_path, non_pkg_prefix, root, ancestors=frozenset())


def _discover_pkgutil_impl(
    module_name: str, scan_path: str, non_pkg_prefix: str, root: Path, *, ancestors: frozenset[str]
) -> dict[str, str]:
    """Recursive implementation of pkgutil-based submodule discovery.

    Args:
        module_name: Dotted importable module name used as prefix (e.g. ``"predicated"``).
        scan_path: Path to scan, relative to *root* (e.g. ``"src/predicated"``).
        non_pkg_prefix: Non-package path prefix to prepend when constructing file paths
            (e.g. ``"src"``).  Empty string when there is no prefix.
        root: Project root every path is resolved against.
        ancestors: Real paths of the directories above this one in the walk. A directory
            symlinked to one of them would recurse forever, so it is not entered again;
            a symlink elsewhere is followed, as the import system follows it.
    """
    real_path = os.path.realpath(root / scan_path)
    if real_path in ancestors:
        return {}
    ancestors |= {real_path}
    results: dict[str, str] = {}
    for module_info in iter_namespace(module_name, scan_path=str(root / scan_path)):
        name = module_info.name
        if name not in results:
            # Construct file path: prepend the non-package prefix to module parts
            module_parts = name.split(".")
            file_parts = list(Path(non_pkg_prefix).parts) + module_parts if non_pkg_prefix else module_parts

            base = root.joinpath(*file_parts)
            # Not with_suffix(): it would truncate at a dot in the final component.
            file_path = base / "__init__.py" if module_info.ispkg else base.parent / f"{base.name}.py"

            if file_path.exists():
                results[name] = str(file_path.resolve())
            else:
                logger.warning("Module %s not found at expected path %s", name, file_path)

            if module_info.ispkg:
                sub_scan_path = os.path.join(scan_path, module_parts[-1])
                results.update(_discover_pkgutil_impl(name, sub_scan_path, non_pkg_prefix, root, ancestors=ancestors))

    for portion in _namespace_portions(root / scan_path):
        sub_scan_path = os.path.join(scan_path, portion)
        results.update(
            _discover_pkgutil_impl(f"{module_name}.{portion}", sub_scan_path, non_pkg_prefix, root, ancestors=ancestors)
        )

    return results


#: Identifier-named directories that never hold the project's own modules: walking
#: them costs time and adds phantom graph nodes (``node_modules`` holds thousands).
_NEVER_PACKAGES = frozenset({"__pycache__", "node_modules"})


def _namespace_portions(directory: Path) -> list[str]:
    """Sub-directories of *directory* that ``pkgutil`` skips but the import system resolves.

    ``pkgutil.iter_modules`` lists only regular packages, those with an ``__init__.py``.
    Since PEP 420, any directory inside a package whose name is an identifier imports
    as an implicit namespace package — ``import pkg.processors.ocr`` works when
    ``processors/`` has no ``__init__.py`` — so its modules are part of the package.

    Like ``pkgutil``, a directory that cannot be listed — missing, or unreadable, such
    as a container's bind-mounted data directory — has no modules; the ``os.path``
    checks likewise treat an unreadable entry as absent rather than raising.
    """
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return []
    return [
        name
        for name in entries
        if name.isidentifier()
        and name not in _NEVER_PACKAGES
        and os.path.isdir(directory / name)
        and not os.path.isfile(directory / name / "__init__.py")
    ]


def _discover_via_filesystem(package: str, root: Path) -> dict[str, str]:
    """Discover submodules by walking the filesystem (no __init__.py required).

    Uses Path.rglob to find all .py files regardless of whether intermediate
    directories contain __init__.py. This matches pytest's own filesystem-based
    test discovery behavior.
    """
    base_path = root / package_name_to_path(package)
    if not base_path.is_dir():
        return {}

    results: dict[str, str] = {}
    for py_file in base_path.rglob("*.py"):
        rel = py_file.relative_to(base_path.parent)
        if py_file.name == "__init__.py":
            module_name = ".".join(rel.parent.parts)
        else:
            module_name = ".".join(rel.with_suffix("").parts)

        results[module_name] = str(py_file.resolve())

    return results


@lru_cache
def _discover_submodules(package: str, require_init: bool, root: Path) -> dict[str, str]:
    """Cached discovery, keyed on the canonical *root* so two projects cannot share an entry."""
    if require_init:
        return _discover_via_pkgutil(package, root)
    return _discover_via_filesystem(package, root)


def discover_submodules(package: str, require_init: bool = True, root_dir: str | Path | None = None) -> dict[str, str]:
    """Discover all submodules by filesystem scanning, without importing them.

    This avoids executing module-level code (e.g. gevent monkey patching,
    application factory calls, global connections) that can corrupt the test
    environment when modules are eagerly imported.

    Args:
        package: Dotted package name (or path-style name like ``"src.predicated"``)
            to scan.  For src-layout projects, non-package prefix directories
            are automatically detected and stripped from module names.
        require_init: If True, discover an importable package: module names follow
            the import system, so a non-package prefix such as ``src/`` is dropped,
            and sub-directories without __init__.py count as namespace packages.
            If False, use filesystem walking which finds all .py files
            regardless of __init__.py (matching pytest's discovery behavior).
        root_dir: Project root *package* is relative to. Defaults to the current
            working directory.

    Returns:
        Dict mapping fully-qualified module name -> absolute file path.
    """
    return _discover_submodules(package, require_init, canonical_root(root_dir))


def clear_discovery_cache() -> None:
    """Drop every cached discovery result (see :func:`discover_submodules`)."""
    _discover_submodules.cache_clear()


# The cache moved to the private inner function when ``root_dir`` was added, but
# ``discover_submodules`` is part of the public surface — keep the old call working.
discover_submodules.cache_clear = _discover_submodules.cache_clear  # type: ignore[attr-defined]


def _conftest_module_name(directory: Path, root: Path, taken: Collection[str]) -> str:
    """Dotted name for ``directory/conftest.py`` that is not already in *taken*.

    Preferably named the way package discovery names modules, dropping a
    non-package prefix like ``src/``, so the conftest's relative imports resolve
    to the modules they refer to. Dropping the prefix can clash with another
    module, and a clash would drop the conftest from the graph, so the full
    path from the root — unique among conftests — is the fallback.
    """
    relative = directory.relative_to(root)
    if not relative.parts:
        return "conftest"
    _, importable = find_non_package_prefix(str(relative), root)
    preferred = f"{path_to_package_name(importable)}.conftest"
    return preferred if preferred not in taken else ".".join((*relative.parts, "conftest"))


def discover_ancestor_conftests(
    packages: Iterable[str], root_dir: str | Path | None = None, *, taken: Collection[str] = ()
) -> dict[str, str]:
    """Find the ``conftest.py`` files between *root_dir* and each package directory.

    pytest loads every conftest from the rootdir down to a test file, so one
    above the analysed packages — most often at the repository root — still
    provides fixtures to their tests. Package discovery never sees it, so
    without this its imports would be invisible to the dependency graph.

    Args:
        taken: Module names already in use (see :func:`_conftest_module_name`).

    Returns:
        Dict mapping a dotted name (``conftest``, ``backend.conftest``; a non-package
        prefix such as ``src/`` is dropped, as package discovery does) -> absolute
        file path, like :func:`discover_submodules`.
    """
    root = canonical_root(root_dir)
    found: dict[str, str] = {}
    for package in packages:
        directory = (root / package_name_to_path(package)).parent
        while directory.is_relative_to(root):
            conftest = directory / "conftest.py"
            if conftest.is_file() and (path := str(conftest.resolve())) not in found.values():
                found[_conftest_module_name(directory, root, taken={*taken, *found})] = path
            if directory == root:
                break
            directory = directory.parent
    return found


def resolve_files_to_modules(
    filenames: list[str],
    ns_module: str,
    tests_package: str | None = None,
    root_dir: str | Path | None = None,
):
    """Resolve file paths to their corresponding Python module names.

    Uses filesystem-based discovery (no imports) to build the module mapping.
    *filenames* are interpreted relative to *root_dir*, as git reports them.
    """
    root = canonical_root(root_dir)
    submodules = discover_submodules(ns_module, require_init=True, root_dir=root)
    if tests_package:
        logger.debug("Adding modules from tests_package: %s", tests_package)
        test_submodules = discover_submodules(tests_package, require_init=False, root_dir=root)
        submodules = {**submodules, **test_submodules}

    # Reverse mapping: absolute file path -> every module name it was discovered under.
    # A tests dir inside the package is found twice (``app.tests.x`` by the package
    # walk, ``tests.x`` by the tests-dir walk) and importers may use either name.
    path_to_modules: dict[str, list[str]] = {}
    for name, path in submodules.items():
        path_to_modules.setdefault(path, []).append(name)

    resolved_modules = []
    for file in filenames:
        if not file.endswith(".py"):
            continue

        abs_path = str((root / file).resolve())
        if abs_path in path_to_modules:
            resolved_modules.extend(path_to_modules[abs_path])
        elif not Path(abs_path).exists():
            logger.debug("File %s no longer exists; nothing to resolve", file)
        else:
            logger.warning(
                "File %s could not be resolved to a known module",
                file,
            )

    return resolved_modules


def resolve_modules_to_files(
    modules: list[str],
    ns_module: str,
    tests_package: str | None = None,
    root_dir: str | Path | None = None,
) -> list[str]:
    """Resolve module names to their corresponding file paths.

    Uses filesystem-based discovery (no imports) to find module files.
    """
    root = canonical_root(root_dir)
    submodules = discover_submodules(ns_module, require_init=True, root_dir=root)
    if tests_package:
        submodules = {**submodules, **discover_submodules(tests_package, require_init=False, root_dir=root)}

    result = []
    for module_name in modules:
        if module_name in submodules:
            result.append(submodules[module_name])
        else:
            logger.warning("Module %s not found in discovered submodules", module_name)
    return result
