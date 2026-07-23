"""Multi-package (monorepo) workspace discovery and inter-package dependency graph.

Everything here is filesystem scanning and TOML parsing only — no modules are
imported, no git access happens, consistent with the project's design principle
of never importing analyzed code.
"""

import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from packaging.requirements import InvalidRequirement, Requirement


logger = logging.getLogger(__name__)


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
