"""Multi-package (monorepo) workspace discovery and inter-package dependency graph.

Everything here is filesystem scanning and TOML parsing only — no modules are
imported, no git access happens, consistent with the project's design principle
of never importing analyzed code.
"""

import fnmatch
import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import networkx as nx
from packaging.requirements import InvalidRequirement, Requirement

from pytest_impacted.strategies import matches_dependency_file


logger = logging.getLogger(__name__)

#: Directory names never descended into during filesystem scans.
PRUNE_DIRS = frozenset({"venv", "node_modules", "build", "dist", "site-packages", "__pycache__"})


def normalize_package_name(name: str) -> str:
    """Normalize a distribution name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class PackageInfo:
    """A single package discovered in a monorepo workspace.

    :param name: PEP 503-normalized ``[project].name``.
    :param path: package directory relative to the monorepo root (``"."`` for the root itself).
    :param module: impacted-module path relative to the package dir (e.g. ``"src/pkg_a"``).
    :param tests_dir: tests directory relative to the package dir, if any.
    :param requirements: normalized names of ALL declared dependencies (workspace and external);
        intersected with workspace package names when building the package graph.
    """

    name: str
    path: PurePosixPath
    module: str
    tests_dir: str | None
    requirements: frozenset[str] = frozenset()


def load_package(pkg_dir: Path, root: Path) -> "PackageInfo | None":
    """Build a :class:`PackageInfo` from *pkg_dir*'s ``pyproject.toml``.

    Returns ``None`` (with a log record) when the directory is not a usable
    package: unparseable TOML, no ``[project].name`` (common for workspace-root
    ``pyproject.toml`` files that only hold tooling config), or no resolvable
    module directory.
    """
    try:
        data = tomllib.loads((pkg_dir / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Skipping %s: could not parse pyproject.toml (%s)", pkg_dir, exc)
        return None

    project = data.get("project") or {}
    name = project.get("name")
    if not name:
        logger.debug("Skipping %s: pyproject.toml has no [project].name", pkg_dir)
        return None

    ini_options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    module = ini_options.get("impacted_module")
    if not module:
        module_dir = normalize_package_name(name).replace("-", "_")
        for candidate in (f"src/{module_dir}", module_dir):
            if (pkg_dir / candidate / "__init__.py").is_file():
                module = candidate
                break
    if not module:
        logger.warning(
            "Skipping package %r at %s: no impacted_module configured and neither src/%s/ nor %s/ is a package",
            name,
            pkg_dir,
            normalize_package_name(name).replace("-", "_"),
            normalize_package_name(name).replace("-", "_"),
        )
        return None

    tests_dir = ini_options.get("impacted_tests_dir")
    if not tests_dir and (pkg_dir / "tests").is_dir():
        tests_dir = "tests"

    requirement_strings = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        requirement_strings.extend(group)
    requirements: set[str] = set()
    for requirement_string in requirement_strings:
        try:
            requirements.add(normalize_package_name(Requirement(requirement_string).name))
        except InvalidRequirement:
            logger.debug("Ignoring unparseable requirement %r in %s", requirement_string, pkg_dir)

    resolved_pkg_dir = pkg_dir.resolve()
    resolved_root = root.resolve()
    if resolved_pkg_dir == resolved_root:
        rel_path = PurePosixPath(".")
    else:
        rel_path = PurePosixPath(resolved_pkg_dir.relative_to(resolved_root).as_posix())

    return PackageInfo(
        name=normalize_package_name(name),
        path=rel_path,
        module=module,
        tests_dir=tests_dir,
        requirements=frozenset(requirements),
    )


def discover_packages(root: "Path | str") -> list[PackageInfo]:
    """Discover all packages under *root*.

    Honors ``[tool.uv.workspace]`` members/exclude globs when the root
    ``pyproject.toml`` declares a workspace; otherwise falls back to a
    recursive filesystem scan for ``pyproject.toml`` files (pruning hidden
    directories and PRUNE_DIRS). Results are sorted by path; duplicate
    package names keep the first occurrence.
    """
    root = Path(root).resolve()
    package_dirs = _uv_workspace_member_dirs(root)
    if package_dirs is None:
        package_dirs = _scan_package_dirs(root)

    packages: list[PackageInfo] = []
    seen_names: set[str] = set()
    for pkg_dir in sorted(set(package_dirs)):
        info = load_package(pkg_dir, root)
        if info is None:
            continue
        if info.name in seen_names:
            logger.warning("Duplicate package name %r at %s — keeping the first occurrence", info.name, pkg_dir)
            continue
        seen_names.add(info.name)
        packages.append(info)
    return packages


def _uv_workspace_member_dirs(root: Path) -> "list[Path] | None":
    """Expand ``[tool.uv.workspace]`` member globs, or None when no workspace is declared."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    workspace = data.get("tool", {}).get("uv", {}).get("workspace")
    if workspace is None:
        return None

    excludes = list(workspace.get("exclude") or [])
    # The workspace root itself is a candidate; load_package drops it when it has no [project].
    member_dirs: list[Path] = [root]
    for pattern in workspace.get("members") or []:
        for match in sorted(root.glob(pattern)):
            if not (match / "pyproject.toml").is_file():
                continue
            rel = match.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, exclude) for exclude in excludes):
                continue
            member_dirs.append(match)
    return member_dirs


def _scan_package_dirs(root: Path) -> list[Path]:
    """Recursively find directories containing a ``pyproject.toml``."""
    found: list[Path] = []
    if (root / "pyproject.toml").is_file():
        found.append(root)

    def _walk(directory: Path) -> None:
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name in PRUNE_DIRS:
                continue
            if (child / "pyproject.toml").is_file():
                found.append(child)
            _walk(child)

    _walk(root)
    return found


def build_package_graph(packages: list[PackageInfo]) -> nx.DiGraph:
    """Build the inter-package dependency graph.

    Edge ``B -> A`` means "A depends on workspace package B", so a change in B
    impacts A. Dependencies on packages outside the workspace are ignored.
    """
    graph = nx.DiGraph()
    workspace_names = {pkg.name for pkg in packages}
    graph.add_nodes_from(workspace_names)
    for pkg in packages:
        for dependency in pkg.requirements & workspace_names:
            if dependency != pkg.name:
                graph.add_edge(dependency, pkg.name)
    return graph


#: Order in which impact reasons are reported in the composite reason string.
REASON_ORDER = ("direct", "dependency", "dep-files")


@dataclass
class ImpactedPackage:
    """A package marked impacted, with the reasons it was selected."""

    package: PackageInfo
    reasons: set[str] = field(default_factory=set)

    @property
    def reason(self) -> str:
        return "+".join(reason for reason in REASON_ORDER if reason in self.reasons)


def _owning_package(file_path: str, packages_longest_first: list[PackageInfo]) -> "PackageInfo | None":
    path = PurePosixPath(file_path)
    for pkg in packages_longest_first:
        if pkg.path == PurePosixPath(".") or path.is_relative_to(pkg.path):
            return pkg
    return None


def _by_longest_path(packages: list[PackageInfo]) -> list[PackageInfo]:
    return sorted(packages, key=lambda pkg: len(pkg.path.parts), reverse=True)


def map_files_to_packages(changed_files: list[str], packages: list[PackageInfo]) -> dict[str, list[str]]:
    """Map root-relative changed files to the name of their owning package (longest path prefix wins)."""
    ordered = _by_longest_path(packages)
    mapping: dict[str, list[str]] = {}
    for file_path in changed_files:
        owner = _owning_package(file_path, ordered)
        if owner is not None:
            mapping.setdefault(owner.name, []).append(file_path)
    return mapping


def compute_impacted_packages(
    changed_files: list[str],
    packages: list[PackageInfo],
    *,
    watch_dep_files: bool = True,
) -> dict[str, ImpactedPackage]:
    """Compute which packages are impacted by *changed_files* and why.

    Reasons: ``direct`` (files changed inside the package), ``dependency``
    (a workspace package it depends on changed), ``dep-files`` (a dependency
    file changed at the monorepo root, outside every non-root package —
    dependency files *inside* a package are covered by that package's own
    per-package analysis).
    """
    graph = build_package_graph(packages)
    by_name = {pkg.name: pkg for pkg in packages}
    impacted: dict[str, ImpactedPackage] = {}

    def _mark(name: str, reason: str) -> None:
        impacted.setdefault(name, ImpactedPackage(package=by_name[name])).reasons.add(reason)

    for name in map_files_to_packages(changed_files, packages):
        _mark(name, "direct")
        for dependent in nx.descendants(graph, name):
            _mark(dependent, "dependency")

    if watch_dep_files:
        ordered = _by_longest_path(packages)
        for file_path in changed_files:
            owner = _owning_package(file_path, ordered)
            is_root_level = owner is None or owner.path == PurePosixPath(".")
            if is_root_level and matches_dependency_file(file_path):
                for pkg in packages:
                    _mark(pkg.name, "dep-files")
                break

    return impacted
