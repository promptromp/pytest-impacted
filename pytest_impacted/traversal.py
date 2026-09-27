"""Python package and module traversal utilities."""

import logging
import os
import pkgutil
import sys
from collections.abc import Callable, Iterable, Mapping
from functools import cache, lru_cache
from itertools import chain
from pathlib import Path
from typing import NamedTuple


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
        if _is_regular_package(root / candidate):
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
    """Discover *package* and the modules importable under it, the way the import system names them.

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

    Each call names the package it walks, from its own ``__init__.py``, before its
    children: ``pkgutil`` lists only children, and the analysed package — run by
    every ``import pkg`` — has no parent walk to list it.

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
    # os.path, not Path.is_file(): an unsearchable directory has no __init__.py, rather than raising.
    init = root / scan_path / "__init__.py"
    results = {module_name: str(init.resolve())} if os.path.isfile(init) else {}
    for module_info in iter_namespace(module_name, scan_path=str(root / scan_path)):
        name = module_info.name
        if name in results:
            continue
        module_parts = name.split(".")
        if module_info.ispkg:
            sub_scan_path = os.path.join(scan_path, module_parts[-1])
            results.update(_discover_pkgutil_impl(name, sub_scan_path, non_pkg_prefix, root, ancestors=ancestors))
            continue

        # Construct file path: prepend the non-package prefix to module parts
        file_parts = list(Path(non_pkg_prefix).parts) + module_parts if non_pkg_prefix else module_parts
        base = root.joinpath(*file_parts)
        # Not with_suffix(): it would truncate at a dot in the final component.
        file_path = base.parent / f"{base.name}.py"

        # os.path, not Path.exists(): a file in an unsearchable directory is missing, rather than raising.
        if os.path.isfile(file_path):
            results[name] = str(file_path.resolve())
        else:
            logger.warning("Module %s not found at expected path %s", name, file_path)

    for portion in _namespace_portions(root / scan_path, root):
        sub_scan_path = os.path.join(scan_path, portion)
        results.update(
            _discover_pkgutil_impl(f"{module_name}.{portion}", sub_scan_path, non_pkg_prefix, root, ancestors=ancestors)
        )

    return results


#: Identifier-named directories that never hold the project's own modules: walking
#: them costs time and adds phantom graph nodes (``node_modules`` holds thousands).
_NEVER_PACKAGES = frozenset({"__pycache__", "node_modules"})


def _namespace_portions(directory: Path, root: Path) -> list[str]:
    """Sub-directories of *directory* that ``pkgutil`` skips but the import system resolves.

    ``pkgutil.iter_modules`` lists only regular packages, those with an ``__init__.py``.
    Since PEP 420, any directory inside a package whose name is an identifier imports
    as an implicit namespace package — ``import pkg.processors.ocr`` works when
    ``processors/`` has no ``__init__.py`` — so its modules are part of the package.
    A directory shadowed by a same-named module (``tests.py`` beside ``tests/``) is
    walked all the same: pytest still collects tests from it, and a phantom module
    can only add tests, never hide one.

    Like ``pkgutil``, a directory that cannot be listed — missing, or unreadable, such
    as a container's bind-mounted data directory — has no modules; the ``os.path``
    checks likewise treat an unreadable entry as absent rather than raising. A
    symlinked portion is followed only while it stays inside the project and does
    not point back up the tree (see :func:`_is_walkable_link_target`).
    """
    try:
        entries = set(os.listdir(directory))
    except OSError:
        return []
    real_directory = Path(os.path.realpath(directory))
    return [
        name
        for name in sorted(entries)
        if name.isidentifier()
        and name not in _NEVER_PACKAGES
        and os.path.isdir(directory / name)
        and not os.path.isfile(directory / name / "__init__.py")
        and _is_walkable_link_target(directory / name, real_directory, root)
    ]


def _is_walkable_link_target(path: Path, real_parent: Path, root: Path) -> bool:
    """Whether a namespace portion's real location is safe to walk.

    Before namespace portions were walked, a directory without ``__init__.py`` was
    never entered, so a symlink out of the project (``pkg/media -> /mnt/storage``) or
    back up it (``pkg/ns/up -> ../..``) cost nothing. Walking one would list a whole
    external tree, or re-walk the project under a second name. Neither can hold a
    changed project file under that name, so both are skipped.
    """
    real = Path(os.path.realpath(path))
    return real.is_relative_to(root) and not real_parent.is_relative_to(real)


def _discover_via_filesystem(package: str, root: Path) -> dict[str, str]:
    """Discover submodules by walking the filesystem (no __init__.py required).

    Uses Path.rglob to find all .py files regardless of whether intermediate
    directories contain __init__.py. This matches pytest's own filesystem-based
    test discovery behavior.
    """
    base_path = root / package_name_to_path(package)
    # os.path, not Path.is_dir(): a directory inside an unsearchable one is missing, rather than raising.
    if not os.path.isdir(base_path):
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
    """Discover a package and all its submodules by filesystem scanning, without importing them.

    This avoids executing module-level code (e.g. gevent monkey patching,
    application factory calls, global connections) that can corrupt the test
    environment when modules are eagerly imported.

    Args:
        package: Dotted package name (or path-style name like ``"src.predicated"``)
            to scan.  For src-layout projects, non-package prefix directories
            are automatically detected and stripped from module names.
        require_init: If True, discover an importable package — the package itself,
            when it has an ``__init__.py``, and every module under it: module names
            follow the import system, so a non-package prefix such as ``src/`` is
            dropped, and sub-directories without __init__.py count as namespace packages.
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


class ProjectModules(NamedTuple):
    """The modules of a project: one canonical name per file, plus the other names that reach it."""

    #: Canonical dotted name -> absolute file path. Each file appears once. A conftest
    #: above the packages with no free importable name is keyed with a leading dot
    #: (:data:`LAST_RESORT_PREFIX`); parse it under :func:`import_base` of its name.
    modules: dict[str, str]
    #: Another importable name -> the canonical name of the same file.
    aliases: dict[str, str]


class _Discovered(NamedTuple):
    """:class:`ProjectModules`, plus the importable names that more than one file can have."""

    modules: dict[str, str]
    aliases: dict[str, str]
    #: A name some conftests above the package wanted but did not get -> those conftests'
    #: canonical names: an import of it may mean any of them (see :func:`_name_conftests`).
    contested: dict[str, set[str]]


#: Starts the name of a conftest with no free importable name: no import spells one so.
LAST_RESORT_PREFIX = "."


def import_base(module_name: str) -> str:
    """The name *module_name*'s relative imports resolve from: itself, less a :data:`LAST_RESORT_PREFIX`."""
    return module_name.removeprefix(LAST_RESORT_PREFIX)


class _ConftestCandidate(NamedTuple):
    """The names a conftest can take."""

    #: Importable names, preferred first (see :func:`_conftest_candidate`).
    names: list[str]
    #: Its name when every one of *names* is taken: unique, and spelled by no import.
    last_resort: str


def _conftest_candidate(directory: Path, root: Path) -> _ConftestCandidate:
    """The names ``directory/conftest.py`` can be imported under, preferred first.

    Preferably the way package discovery names modules, rooted at the first regular
    package and dropping a non-package prefix like ``src/``, so the conftest's relative
    imports resolve to the modules they refer to; then the name rooted at each other
    directory that could be on ``sys.path``, as for a package's modules (see
    :func:`_rooted_names`). The last resort is its full path with a leading dot.
    """
    parts = directory.relative_to(root).parts
    last_resort = LAST_RESORT_PREFIX + ".".join((*parts, "conftest"))
    if not parts:
        return _ConftestCandidate(["conftest"], last_resort)
    _, importable = find_non_package_prefix(str(Path(*parts)), root)
    preferred = f"{path_to_package_name(importable)}.conftest"
    rooted = _rooted_names(parts, (*parts, "conftest"), root, len(parts) - 1, _is_regular_package)
    return _ConftestCandidate(list(dict.fromkeys([preferred, *rooted])), last_resort)


def discover_ancestor_conftests(
    packages: Iterable[str], root_dir: str | Path | None = None, *, taken: Iterable[str] = ()
) -> dict[str, str]:
    """Find the ``conftest.py`` files between *root_dir* and each package directory.

    Returns:
        Dict mapping each conftest's name (``conftest``, ``backend.conftest``; see
        :func:`_discover_ancestor_conftests`) -> absolute file path, like :func:`discover_submodules`.
        A conftest with no free importable name is keyed by :data:`LAST_RESORT_PREFIX` and its
        path (``.mysite.conftest``): parse its imports under :func:`import_base` of that name.
    """
    return _discover_ancestor_conftests(packages, root_dir, taken=taken).modules


def _discover_ancestor_conftests(
    packages: Iterable[str],
    root_dir: str | Path | None = None,
    *,
    taken: Iterable[str] = (),
    known_paths: Iterable[str] = (),
) -> _Discovered:
    """Find the ``conftest.py`` files between *root_dir* and each package directory, and name them.

    pytest loads every conftest from the rootdir down to a test file, so one
    above the analysed packages — most often at the repository root — still
    provides fixtures to their tests. Package discovery never sees it, so
    without this its imports would be invisible to the dependency graph.

    Each conftest is named from :func:`_conftest_candidate` around every name in *taken*
    (see :func:`_name_conftests`).

    Args:
        taken: Names already in use, aliases included: a conftest never takes one.
        known_paths: Files already named by another walk, which are skipped.

    Returns:
        The conftests (``conftest``, ``backend.conftest``; a non-package prefix such
        as ``src/`` is dropped, as package discovery does), their aliases, and the
        names they contest.
    """
    root = canonical_root(root_dir)
    seen = set(known_paths)
    candidates: dict[str, _ConftestCandidate] = {}
    for package in packages:
        directory = (root / package_name_to_path(package)).parent
        while directory.is_relative_to(root):
            conftest = directory / "conftest.py"
            if os.path.isfile(conftest) and (path := str(conftest.resolve())) not in seen:
                seen.add(path)
                candidates[path] = _conftest_candidate(directory, root)
            if directory == root:
                break
            directory = directory.parent
    return _name_conftests(candidates, taken=taken)


def _name_conftests(candidates: dict[str, _ConftestCandidate], *, taken: Iterable[str]) -> _Discovered:
    """Name each conftest path from its candidates, never with a name in *taken*.

    Names are handed out by rank across all conftests, in walk order, so one conftest's
    second choice never takes another's first; the free names left are aliases, once
    every conftest has its own. A conftest with no free name gets its last resort rather
    than being dropped, which would lose every edge from it.

    Which file a name two files can have means depends on ``sys.path`` and the import
    mode, which analysis cannot know, so each name a conftest wanted but did not get is
    *contested*: an import of it is an import of that conftest too. Every rule that picks
    one winner loses tests in some layout.
    """
    used = set(taken)
    chosen: dict[str, str] = {}
    for rank in range(max((len(candidate.names) for candidate in candidates.values()), default=0)):
        for path, candidate in candidates.items():
            if path not in chosen and rank < len(candidate.names) and candidate.names[rank] not in used:
                chosen[path] = candidate.names[rank]
                used.add(candidate.names[rank])
    modules = {chosen.get(path, candidate.last_resort): path for path, candidate in candidates.items()}
    aliases: dict[str, str] = {}
    for name, path in modules.items():
        for alias in candidates[path].names:
            if alias not in used:
                aliases[alias] = name
                used.add(alias)
    contested: dict[str, set[str]] = {}
    for name, path in modules.items():
        for wanted in candidates[path].names:
            if wanted != name and aliases.get(wanted) != name:
                contested.setdefault(wanted, set()).add(name)
    return _Discovered(modules, aliases, contested)


def discover_project_modules(
    package: str, tests_package: str | None = None, root_dir: str | Path | None = None
) -> ProjectModules:
    """Discover *package* and *tests_package* together, naming every file exactly once.

    A file can be imported under several names, and each must reach it, but it must
    be one graph node — two would double every count and hand each consumer the
    same test twice. The package walk's name is canonical; the others are aliases:

    * A symlinked directory inside the package makes the walk find a file twice;
      the name not reached through the link is canonical.
    * A package below a non-package directory — ``src/app``, or ``src/company/app``
      where ``company/`` is a namespace package — may be imported as ``app.x``,
      ``company.app.x`` or ``src.company.app.x``, depending on ``sys.path``
      (see :func:`_root_aliases`).
    * A tests dir inside the package is walked twice: ``app.tests.x`` by the package
      walk, ``tests.x`` by the tests-dir walk.

    Conftests above the two directories are modules too (see
    :func:`_discover_ancestor_conftests`): pytest loads them, so they are graph nodes,
    and an edit to one must resolve to its node like any other module. They are named
    last, around every name already in use; one left with no free name is keyed with a
    leading dot (see :class:`ProjectModules`).
    """
    modules, aliases, _ = _discover_project(package, tests_package, root_dir)
    return ProjectModules(modules, aliases)


def _discover_project(
    package: str, tests_package: str | None = None, root_dir: str | Path | None = None
) -> _Discovered:
    """:func:`discover_project_modules`, with the names the conftests above the package contest."""
    root = canonical_root(root_dir)
    walked = discover_submodules(package, require_init=True, root_dir=root)
    names_by_path: dict[str, list[str]] = {}
    for name, path in walked.items():
        names_by_path.setdefault(path, []).append(name)
    prefix_parts = Path(find_non_package_prefix(package_name_to_path(package), root)[0]).parts

    modules: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for path, names in names_by_path.items():
        canonical, *others = sorted(names, key=lambda name: (_reached_through_symlink(name, prefix_parts, root), name))
        modules[canonical] = path
        aliases.update(dict.fromkeys(others, canonical))
    for alias, name in _root_aliases(package, modules, root).items():
        aliases.setdefault(alias, name)
    if tests_package:
        canonical_of = {path: name for name, path in modules.items()}
        for name, path in discover_submodules(tests_package, require_init=False, root_dir=root).items():
            if canonical_of.get(path, name) == name:
                modules[name] = path
            else:
                aliases.setdefault(name, canonical_of[path])
    # The walk up from a tests dir inside the package passes conftests the walks already named.
    packages = [package, tests_package] if tests_package else [package]
    ancestors = _discover_ancestor_conftests(
        packages, root_dir=root, taken=chain(modules, aliases), known_paths=modules.values()
    )
    modules = {**ancestors.modules, **modules}
    aliases = {**ancestors.aliases, **aliases}
    aliases = {alias: name for alias, name in aliases.items() if alias not in modules}
    return _Discovered(modules, aliases, ancestors.contested)


def discover_application_files(
    package: str, tests_package: str | None = None, root_dir: str | Path | None = None
) -> frozenset[str]:
    """The files of *package* that are code under test: the package walk's, less the tests dir's.

    A tests dir inside the package (``app/tests``) holds test code, so its files are
    left out. A tests dir holding the whole package cannot tell tests from the
    application and is ignored — decided on the configured directories, not on the two
    walks' results, which differ wherever the package walk follows a symlink the tests
    walk does not. Conftests inside the package are included; callers exclude them by
    file name (see ``strategies._CodeRoles``).
    """
    root = canonical_root(root_dir)
    application = set(discover_submodules(package, require_init=True, root_dir=root).values())
    package_dir = Path(package_name_to_path(package))
    if tests_package and not package_dir.is_relative_to(package_name_to_path(tests_package)):
        application -= set(discover_submodules(tests_package, require_init=False, root_dir=root).values())
    return frozenset(application)


def _reached_through_symlink(name: str, prefix_parts: tuple[str, ...], root: Path) -> bool:
    """Whether the walk reached module *name* through a symlinked directory."""
    parts = (*prefix_parts, *name.split("."))
    return any(root.joinpath(*parts[:end]).is_symlink() for end in range(1, len(parts)))


def _root_aliases(package: str, modules: dict[str, str], root: Path) -> dict[str, str]:
    """Names of *modules* rooted at other directories that could be on ``sys.path``.

    ``find_non_package_prefix`` picks one root, but it cannot know which directories
    are on ``sys.path``: ``src/`` usually is, a namespace package such as
    ``company/`` usually is not. Any directory above the module's first regular
    package can be — never one inside a regular package, which would invent names
    like ``types`` for ``pkg/ns/types.py``. With no regular package on the way, the
    roots stop at the analysed directory itself, which is the package being named.
    """
    is_regular_package = cache(_is_regular_package)
    last_root = len(Path(package_name_to_path(package)).parts) - 1
    aliases: dict[str, str] = {}
    for name, path in modules.items():
        file = Path(path)
        if not file.is_relative_to(root):
            continue
        directories = file.relative_to(root).parent.parts
        parts = directories if file.name == "__init__.py" else (*directories, file.stem)
        for alias in _rooted_names(directories, parts, root, last_root, is_regular_package):
            aliases.setdefault(alias, name)
    return aliases


def _rooted_names(
    directories: tuple[str, ...],
    parts: tuple[str, ...],
    root: Path,
    last_root: int,
    is_regular_package: Callable[[Path], bool],
) -> list[str]:
    """The dotted names of *parts* rooted at each of *directories* that could be on ``sys.path``.

    That is any directory down to the first regular package — never one inside it,
    which would invent names like ``types`` for ``pkg/ns/types.py`` — or down to index
    *last_root* when there is none; never the bare last part.
    """
    first_regular = next(
        (end for end in range(len(directories)) if is_regular_package(root.joinpath(*directories[: end + 1]))),
        last_root,
    )
    return [".".join(parts[start:]) for start in range(min(first_regular, len(parts) - 1) + 1)]


def _is_regular_package(directory: Path) -> bool:
    """Whether *directory* has an ``__init__.py``; an unsearchable one has none, rather than raising."""
    return os.path.isfile(directory / "__init__.py")


def import_roots(packages: Iterable[str], root_dir: str | Path | None = None) -> list[Path]:
    """The directories an import of a module outside the walks is looked up from.

    The rootdir, and each directory down to every analysed directory's non-package
    prefix — ``src/`` for ``src/app``; ``src/`` and ``src/company/`` for
    ``src/company/app`` — any of which can be on ``sys.path`` for the package's own
    imports to work.
    """
    root = canonical_root(root_dir)
    roots = [root]
    for package in packages:
        prefix, _ = find_non_package_prefix(package_name_to_path(package), root)
        parts = Path(prefix).parts
        roots += [root.joinpath(*parts[: end + 1]) for end in range(len(parts))]
    return list(dict.fromkeys(roots))


def locate_module(name: str, roots: Iterable[Path], root: Path) -> list[str]:
    """The files inside *root* that importing *name* from each of *roots* would load.

    Filesystem checks only, never an import; a package directory wins over a module
    file, as in Python. A standard-library name is never a local file: Python imported
    it before the project's code ran.
    """
    if name.partition(".")[0] in sys.stdlib_module_names:
        return []
    relative = package_name_to_path(name)
    found = []
    for base in roots:
        # os.path, not Path.is_file(): a file in an unsearchable directory is missing, rather than raising.
        file = next((f for f in (base / relative / "__init__.py", base / f"{relative}.py") if os.path.isfile(f)), None)
        if file is not None and (real := file.resolve()).is_relative_to(root):
            found.append(str(real))
    return list(dict.fromkeys(found))


def resolve_files_to_modules(
    filenames: list[str],
    ns_module: str,
    tests_package: str | None = None,
    root_dir: str | Path | None = None,
):
    """Resolve file paths to their corresponding Python module names.

    Uses filesystem-based discovery (no imports) to build the module mapping.
    *filenames* are interpreted relative to *root_dir*, as git reports them.
    (A run resolves through its graph instead: see
    :func:`~pytest_impacted.graph.resolve_files_to_nodes`.)
    """
    modules = discover_project_modules(ns_module, tests_package, root_dir=root_dir).modules
    return modules_for_files(filenames, {path: name for name, path in modules.items()}, root_dir=root_dir)


def modules_for_files(
    filenames: Iterable[str], path_to_module: Mapping[str, str], root_dir: str | Path | None = None
) -> list[str]:
    """The module of each of *filenames* (relative to *root_dir*) in *path_to_module*, keyed by absolute path.

    Non-Python files have none; a deleted file has none either, and one that exists
    but is not a known module is logged.
    """
    root = canonical_root(root_dir)
    resolved_modules = []
    for file in filenames:
        if not file.endswith(".py"):
            continue

        abs_path = str((root / file).resolve())
        if abs_path in path_to_module:
            resolved_modules.append(path_to_module[abs_path])
        elif not os.path.exists(abs_path):
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
    project = discover_project_modules(ns_module, tests_package, root_dir=root_dir)
    submodules = {**{alias: project.modules[name] for alias, name in project.aliases.items()}, **project.modules}

    result = []
    for module_name in modules:
        if module_name in submodules:
            result.append(submodules[module_name])
        else:
            logger.warning("Module %s not found in discovered submodules", module_name)
    return result
