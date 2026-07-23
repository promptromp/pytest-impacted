# `impacted-packages` Monorepo CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `impacted-packages` console script that discovers all packages in a monorepo, computes cross-package impact from git changes + inter-package dependency metadata, and emits per-package impacted test lists (text or JSON) for CI fan-out.

**Architecture:** A new pure module `pytest_impacted/workspace.py` handles package discovery (uv-workspace globs first, filesystem scan fallback), per-package config resolution, and the inter-package dependency graph. A new Click command in `cli.py` orchestrates: changed files computed once at the monorepo root → files mapped to packages → transitive expansion over the package graph → per-package analysis reusing the existing `get_impacted_tests()` (directly-changed packages) or all-tests enumeration (dependency-impacted packages). A checked-in mock monorepo at `examples/monorepo/` serves as both a manual playground and the test fixture.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`, `contextlib.chdir`), Click, NetworkX, `packaging.requirements`, GitPython (tests only), pytest + CliRunner.

**Spec:** `docs/superpowers/specs/2026-07-23-impacted-packages-cli-design.md`

## Global Constraints

- Python floor: 3.11 (`tomllib` and `contextlib.chdir` are stdlib there — do NOT add `tomli`).
- Ruff: line-length=120, double quotes, isort with `lines-after-imports = 2`; pre-commit is `fail_fast: true` and runs ruff + mypy (`pytest_impacted` only) + pytest (`tests` dir, `-m 'not slow'`) on every commit.
- New runtime dependency: `packaging` (currently only transitive via pytest) — added via `uv add packaging` in Task 1.
- `workspace.py` must not import analyzed code, touch git, or use pytest APIs — filesystem + TOML only.
- `discover_submodules` and `get_impacted_tests` resolve paths relative to the CURRENT WORKING DIRECTORY and are LRU-cached by package name only. Any per-package analysis MUST chdir into the package and clear caches before AND after (`clear_dep_tree_cache()` clears both the dep-tree and `discover_submodules` caches).
- All output test paths are monorepo-root-relative POSIX paths. Stdout carries only results; diagnostics go to stderr.
- Run tests with: `uv run python -m pytest tests/test_workspace.py -v` (etc.). Commit messages follow the repo's conventional style (`feat:`, `test:`, `docs:`).

## File Structure

- Create: `pytest_impacted/workspace.py` — discovery, config resolution, package graph, impact computation (pure)
- Modify: `pytest_impacted/cli.py` — per-package analysis helpers + `impacted_packages_cli` command
- Modify: `pyproject.toml` — `packaging` dep, `impacted-packages` console script, `[tool.pytest.ini_options] testpaths`
- Create: `examples/monorepo/` — mock monorepo fixture (uv workspace, 2 packages, src + flat layouts)
- Create: `tests/test_workspace.py` — unit tests for workspace.py
- Create: `tests/test_monorepo_cli.py` — CLI helper/command unit tests + slow git integration test
- Modify: `docs/usage.md`, `README.md`, `CLAUDE.md` — documentation

---

### Task 1: `workspace.py` — name normalization, `PackageInfo`, `load_package`

**Files:**
- Create: `pytest_impacted/workspace.py`
- Create: `tests/test_workspace.py`
- Modify: `pyproject.toml` (via `uv add packaging`)

**Interfaces:**
- Produces: `normalize_package_name(name: str) -> str`; frozen dataclass `PackageInfo(name: str, path: PurePosixPath, module: str, tests_dir: str | None, requirements: frozenset[str])`; `load_package(pkg_dir: Path, root: Path) -> PackageInfo | None`

- [ ] **Step 1: Add the `packaging` runtime dependency**

```bash
cd /Users/adamhadani/Development/pytest-impacted && uv add packaging
```

Expected: `pyproject.toml` `[project].dependencies` gains a `packaging>=...` entry; `uv.lock` updated.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_workspace.py`:

```python
"""Unit tests for the workspace (monorepo) discovery module."""

from pathlib import PurePosixPath

from pytest_impacted.workspace import (
    PackageInfo,
    load_package,
    normalize_package_name,
)


def _write_pyproject(pkg_dir, text):
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "pyproject.toml").write_text(text)


def _make_module(pkg_dir, module_rel):
    module_dir = pkg_dir / module_rel
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "__init__.py").write_text("")


class TestNormalizePackageName:
    def test_pep503_normalization(self):
        assert normalize_package_name("My_Package.Name") == "my-package-name"

    def test_already_normalized(self):
        assert normalize_package_name("pkg-alpha") == "pkg-alpha"


class TestLoadPackage:
    def test_explicit_config_is_used_verbatim(self, tmp_path):
        pkg = tmp_path / "libs" / "beta"
        _write_pyproject(
            pkg,
            '[project]\nname = "pkg-beta"\nversion = "0.1.0"\ndependencies = ["pkg-alpha>=1.0", "click"]\n'
            '[tool.pytest.ini_options]\nimpacted_module = "pkg_beta"\nimpacted_tests_dir = "tests"\n',
        )
        info = load_package(pkg, tmp_path)
        assert info == PackageInfo(
            name="pkg-beta",
            path=PurePosixPath("libs/beta"),
            module="pkg_beta",
            tests_dir="tests",
            requirements=frozenset({"pkg-alpha", "click"}),
        )

    def test_infers_src_layout_module_and_tests_dir(self, tmp_path):
        pkg = tmp_path / "libs" / "alpha"
        _write_pyproject(pkg, '[project]\nname = "pkg-alpha"\nversion = "0.1.0"\n')
        _make_module(pkg, "src/pkg_alpha")
        (pkg / "tests").mkdir()
        info = load_package(pkg, tmp_path)
        assert info is not None
        assert info.module == "src/pkg_alpha"
        assert info.tests_dir == "tests"

    def test_infers_flat_layout_without_tests_dir(self, tmp_path):
        pkg = tmp_path / "libs" / "alpha"
        _write_pyproject(pkg, '[project]\nname = "pkg-alpha"\nversion = "0.1.0"\n')
        _make_module(pkg, "pkg_alpha")
        info = load_package(pkg, tmp_path)
        assert info is not None
        assert info.module == "pkg_alpha"
        assert info.tests_dir is None

    def test_root_package_gets_dot_path(self, tmp_path):
        _write_pyproject(tmp_path, '[project]\nname = "root-pkg"\nversion = "0.1.0"\n')
        _make_module(tmp_path, "root_pkg")
        info = load_package(tmp_path, tmp_path)
        assert info is not None
        assert info.path == PurePosixPath(".")

    def test_missing_project_name_returns_none(self, tmp_path):
        _write_pyproject(tmp_path, '[tool.uv.workspace]\nmembers = ["libs/*"]\n')
        assert load_package(tmp_path, tmp_path) is None

    def test_unresolvable_module_returns_none_with_warning(self, tmp_path, caplog):
        pkg = tmp_path / "libs" / "ghost"
        _write_pyproject(pkg, '[project]\nname = "pkg-ghost"\nversion = "0.1.0"\n')
        with caplog.at_level("WARNING", logger="pytest_impacted.workspace"):
            assert load_package(pkg, tmp_path) is None
        assert "pkg-ghost" in caplog.text

    def test_optional_dependencies_and_bad_requirements(self, tmp_path):
        pkg = tmp_path / "libs" / "beta"
        _write_pyproject(
            pkg,
            '[project]\nname = "pkg-beta"\nversion = "0.1.0"\ndependencies = ["!!!not-a-req!!!"]\n'
            '[project.optional-dependencies]\nextra = ["pkg-alpha[fast]>=1.0"]\n',
        )
        _make_module(pkg, "pkg_beta")
        info = load_package(pkg, tmp_path)
        assert info is not None
        assert info.requirements == frozenset({"pkg-alpha"})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_workspace.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'pytest_impacted.workspace'`

- [ ] **Step 4: Write the implementation**

Create `pytest_impacted/workspace.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_workspace.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pytest_impacted/workspace.py tests/test_workspace.py pyproject.toml uv.lock
git commit -m "feat: add workspace module with package config resolution (#61)"
```

---

### Task 2: `discover_packages` — uv-workspace mode + filesystem-scan fallback

**Files:**
- Modify: `pytest_impacted/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `load_package(pkg_dir, root)` from Task 1
- Produces: `discover_packages(root: Path | str) -> list[PackageInfo]` (sorted by path; deduplicated by name)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workspace.py` (add `discover_packages` to the existing import from `pytest_impacted.workspace`):

```python
class TestDiscoverPackages:
    def _make_package(self, root, rel, name, layout="flat"):
        pkg = root / rel
        _write_pyproject(pkg, f'[project]\nname = "{name}"\nversion = "0.1.0"\n')
        module_dir = name.replace("-", "_")
        _make_module(pkg, f"src/{module_dir}" if layout == "src" else module_dir)
        return pkg

    def test_uv_workspace_members_and_exclude(self, tmp_path):
        _write_pyproject(tmp_path, '[tool.uv.workspace]\nmembers = ["libs/*"]\nexclude = ["libs/skipme"]\n')
        self._make_package(tmp_path, "libs/alpha", "pkg-alpha", layout="src")
        self._make_package(tmp_path, "libs/beta", "pkg-beta")
        self._make_package(tmp_path, "libs/skipme", "pkg-skipme")
        self._make_package(tmp_path, "unlisted", "pkg-unlisted")  # not in members -> ignored
        packages = discover_packages(tmp_path)
        assert [p.name for p in packages] == ["pkg-alpha", "pkg-beta"]

    def test_uv_workspace_includes_root_when_it_has_project(self, tmp_path):
        _write_pyproject(
            tmp_path,
            '[project]\nname = "root-pkg"\nversion = "0.1.0"\n[tool.uv.workspace]\nmembers = ["libs/*"]\n',
        )
        _make_module(tmp_path, "root_pkg")
        self._make_package(tmp_path, "libs/alpha", "pkg-alpha")
        packages = discover_packages(tmp_path)
        assert {p.name for p in packages} == {"root-pkg", "pkg-alpha"}

    def test_scan_fallback_finds_nested_packages_and_prunes(self, tmp_path):
        self._make_package(tmp_path, "libs/alpha", "pkg-alpha")
        self._make_package(tmp_path, "services/deep/gamma", "pkg-gamma")
        self._make_package(tmp_path, ".hidden/secret", "pkg-secret")
        self._make_package(tmp_path, "node_modules/junk", "pkg-junk")
        packages = discover_packages(tmp_path)
        assert [p.name for p in packages] == ["pkg-alpha", "pkg-gamma"]

    def test_duplicate_package_names_keep_first(self, tmp_path, caplog):
        self._make_package(tmp_path, "a/dupe", "pkg-dupe")
        self._make_package(tmp_path, "b/dupe", "pkg-dupe")
        with caplog.at_level("WARNING", logger="pytest_impacted.workspace"):
            packages = discover_packages(tmp_path)
        assert [str(p.path) for p in packages] == ["a/dupe"]
        assert "pkg-dupe" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_workspace.py::TestDiscoverPackages -v`
Expected: FAIL with `ImportError: cannot import name 'discover_packages'`

- [ ] **Step 3: Write the implementation**

Add to `pytest_impacted/workspace.py` (add `import fnmatch` to the stdlib imports; add `PRUNE_DIRS` below `logger`):

```python
#: Directory names never descended into during filesystem scans.
PRUNE_DIRS = frozenset({"venv", "node_modules", "build", "dist", "site-packages", "__pycache__"})


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_workspace.py -v`
Expected: all PASS (sorting note: `sorted(set(package_dirs))` sorts by full path, so `libs/alpha` precedes `libs/beta` and `services/...`).

- [ ] **Step 5: Commit**

```bash
git add pytest_impacted/workspace.py tests/test_workspace.py
git commit -m "feat: discover monorepo packages via uv workspace globs or filesystem scan (#61)"
```

---

### Task 3: `build_package_graph` — inter-package dependency graph

**Files:**
- Modify: `pytest_impacted/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `PackageInfo.requirements` from Task 1
- Produces: `build_package_graph(packages: list[PackageInfo]) -> nx.DiGraph` — nodes are package names; edge `B -> A` when A depends on workspace package B (external deps ignored; self-edges ignored)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workspace.py` (import `build_package_graph`; add `import networkx as nx` at the top):

```python
def _pkg(name, deps=()):
    return PackageInfo(
        name=name,
        path=PurePosixPath(f"libs/{name}"),
        module=name.replace("-", "_"),
        tests_dir="tests",
        requirements=frozenset(deps),
    )


class TestBuildPackageGraph:
    def test_edges_point_from_dependency_to_dependent(self):
        packages = [_pkg("pkg-alpha"), _pkg("pkg-beta", deps={"pkg-alpha", "click"})]
        graph = build_package_graph(packages)
        assert set(graph.nodes) == {"pkg-alpha", "pkg-beta"}
        assert list(graph.edges) == [("pkg-alpha", "pkg-beta")]

    def test_external_dependencies_are_ignored(self):
        graph = build_package_graph([_pkg("pkg-alpha", deps={"requests", "numpy"})])
        assert list(graph.edges) == []

    def test_transitive_chain_descendants(self):
        packages = [_pkg("a"), _pkg("b", deps={"a"}), _pkg("c", deps={"b"})]
        graph = build_package_graph(packages)
        assert nx.descendants(graph, "a") == {"b", "c"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_workspace.py::TestBuildPackageGraph -v`
Expected: FAIL with `ImportError: cannot import name 'build_package_graph'`

- [ ] **Step 3: Write the implementation**

Add to `pytest_impacted/workspace.py` (add `import networkx as nx` to the third-party imports):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_workspace.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pytest_impacted/workspace.py tests/test_workspace.py
git commit -m "feat: build inter-package dependency graph from project metadata (#61)"
```

---

### Task 4: Impact computation — `map_files_to_packages` + `compute_impacted_packages`

**Files:**
- Modify: `pytest_impacted/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `build_package_graph` (Task 3); `matches_dependency_file(file_path) -> bool` from `pytest_impacted.strategies`
- Produces:
  - dataclass `ImpactedPackage(package: PackageInfo, reasons: set[str])` with property `reason -> str` joining reasons in the fixed order `direct`, `dependency`, `dep-files` with `+`
  - `map_files_to_packages(changed_files: list[str], packages) -> dict[str, list[str]]` (root-relative POSIX file paths → owning package name, longest path prefix wins, root package `"."` matches last)
  - `compute_impacted_packages(changed_files, packages, *, watch_dep_files: bool = True) -> dict[str, ImpactedPackage]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workspace.py` (import `ImpactedPackage`, `compute_impacted_packages`, `map_files_to_packages`):

```python
class TestMapFilesToPackages:
    def test_longest_prefix_wins_over_root_package(self):
        root_pkg = PackageInfo(name="root-pkg", path=PurePosixPath("."), module="root_pkg", tests_dir=None)
        alpha = _pkg("pkg-alpha")
        mapping = map_files_to_packages(
            ["libs/pkg-alpha/pkg_alpha/core.py", "tools/script.py"], [root_pkg, alpha]
        )
        assert mapping == {
            "pkg-alpha": ["libs/pkg-alpha/pkg_alpha/core.py"],
            "root-pkg": ["tools/script.py"],
        }

    def test_files_outside_any_package_are_unowned(self):
        mapping = map_files_to_packages(["README.md"], [_pkg("pkg-alpha")])
        assert mapping == {}


class TestComputeImpactedPackages:
    def test_direct_and_transitive_dependency_impact(self):
        packages = [_pkg("pkg-alpha"), _pkg("pkg-beta", deps={"pkg-alpha"}), _pkg("pkg-gamma")]
        impacted = compute_impacted_packages(["libs/pkg-alpha/pkg_alpha/core.py"], packages)
        assert impacted["pkg-alpha"].reason == "direct"
        assert impacted["pkg-beta"].reason == "dependency"
        assert "pkg-gamma" not in impacted

    def test_direct_plus_dependency_reason_ordering(self):
        packages = [_pkg("pkg-alpha"), _pkg("pkg-beta", deps={"pkg-alpha"})]
        impacted = compute_impacted_packages(
            ["libs/pkg-alpha/pkg_alpha/core.py", "libs/pkg-beta/pkg_beta/service.py"], packages
        )
        assert impacted["pkg-beta"].reason == "direct+dependency"

    def test_root_dependency_file_impacts_all_packages(self):
        packages = [_pkg("pkg-alpha"), _pkg("pkg-gamma")]
        impacted = compute_impacted_packages(["uv.lock"], packages)
        assert {name: entry.reason for name, entry in impacted.items()} == {
            "pkg-alpha": "dep-files",
            "pkg-gamma": "dep-files",
        }

    def test_package_local_dependency_file_is_not_global(self):
        packages = [_pkg("pkg-alpha"), _pkg("pkg-gamma")]
        impacted = compute_impacted_packages(["libs/pkg-alpha/pyproject.toml"], packages)
        assert set(impacted) == {"pkg-alpha"}
        assert impacted["pkg-alpha"].reason == "direct"

    def test_no_dep_files_flag_disables_global_impact(self):
        impacted = compute_impacted_packages(["uv.lock"], [_pkg("pkg-alpha")], watch_dep_files=False)
        assert impacted == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_workspace.py::TestComputeImpactedPackages -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write the implementation**

Add to `pytest_impacted/workspace.py` (add `field` to the `dataclasses` import; add `from pytest_impacted.strategies import matches_dependency_file` — this creates no import cycle, `strategies` does not import `workspace`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_workspace.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pytest_impacted/workspace.py tests/test_workspace.py
git commit -m "feat: compute impacted packages with direct, dependency, and dep-files reasons (#61)"
```

---

### Task 5: Mock monorepo fixture at `examples/monorepo/`

**Files:**
- Create: `examples/monorepo/pyproject.toml`
- Create: `examples/monorepo/uv.lock`
- Create: `examples/monorepo/libs/pkg-alpha/{pyproject.toml,src/pkg_alpha/{__init__.py,core.py,util.py},tests/{test_core.py,test_util.py}}`
- Create: `examples/monorepo/libs/pkg-beta/{pyproject.toml,pkg_beta/{__init__.py,service.py},tests/test_service.py}`
- Modify: `pyproject.toml` (add `[tool.pytest.ini_options] testpaths`)
- Test: `tests/test_workspace.py`

**Interfaces:**
- Produces: the fixture directory, referenced by later tasks as `EXAMPLE_MONOREPO = Path(__file__).parent.parent / "examples" / "monorepo"`. Layout: pkg-alpha (src-layout, zero config → exercises inference), pkg-beta (flat layout, explicit config, depends on pkg-alpha).

- [ ] **Step 1: Write the failing test (discovery against the fixture)**

Append to `tests/test_workspace.py` (add `from pathlib import Path, PurePosixPath` to the top import):

```python
EXAMPLE_MONOREPO = Path(__file__).parent.parent / "examples" / "monorepo"


class TestExampleMonorepoFixture:
    def test_discovers_both_packages_with_correct_config(self):
        packages = discover_packages(EXAMPLE_MONOREPO)
        assert [(p.name, str(p.path), p.module, p.tests_dir) for p in packages] == [
            ("pkg-alpha", "libs/pkg-alpha", "src/pkg_alpha", "tests"),
            ("pkg-beta", "libs/pkg-beta", "pkg_beta", "tests"),
        ]
        assert packages[1].requirements == frozenset({"pkg-alpha"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_workspace.py::TestExampleMonorepoFixture -v`
Expected: FAIL (`discover_packages` raises or returns `[]` — fixture directory does not exist yet)

- [ ] **Step 3: Create the fixture files**

`examples/monorepo/pyproject.toml`:

```toml
# Workspace root for the mock monorepo used in tests and docs examples.
[tool.uv.workspace]
members = ["libs/*"]
```

`examples/monorepo/uv.lock`:

```toml
# Placeholder lockfile — exists so dependency-file impact detection can be exercised.
```

`examples/monorepo/libs/pkg-alpha/pyproject.toml` (no pytest config on purpose — exercises convention-based inference):

```toml
[project]
name = "pkg-alpha"
version = "0.1.0"
dependencies = []
```

`examples/monorepo/libs/pkg-alpha/src/pkg_alpha/__init__.py`: empty file.

`examples/monorepo/libs/pkg-alpha/src/pkg_alpha/core.py`:

```python
"""Core arithmetic helpers for pkg-alpha."""


def add(a: int, b: int) -> int:
    return a + b
```

`examples/monorepo/libs/pkg-alpha/src/pkg_alpha/util.py`:

```python
"""String helpers for pkg-alpha."""


def shout(text: str) -> str:
    return text.upper()
```

`examples/monorepo/libs/pkg-alpha/tests/test_core.py`:

```python
from pkg_alpha.core import add


def test_add():
    assert add(1, 2) == 3
```

`examples/monorepo/libs/pkg-alpha/tests/test_util.py`:

```python
from pkg_alpha.util import shout


def test_shout():
    assert shout("hey") == "HEY"
```

`examples/monorepo/libs/pkg-beta/pyproject.toml` (explicit config — exercises the config-read path):

```toml
[project]
name = "pkg-beta"
version = "0.1.0"
dependencies = ["pkg-alpha"]

[tool.uv.sources]
pkg-alpha = { workspace = true }

[tool.pytest.ini_options]
impacted_module = "pkg_beta"
impacted_tests_dir = "tests"
```

`examples/monorepo/libs/pkg-beta/pkg_beta/__init__.py`: empty file.

`examples/monorepo/libs/pkg-beta/pkg_beta/service.py`:

```python
"""Service layer for pkg-beta, built on pkg-alpha."""

from pkg_alpha.core import add


def double_add(a: int, b: int) -> int:
    return add(a, b) * 2
```

`examples/monorepo/libs/pkg-beta/tests/test_service.py`:

```python
from pkg_beta.service import double_add


def test_double_add():
    assert double_add(1, 2) == 6
```

- [ ] **Step 4: Guard the main repo's pytest collection**

The pre-commit pytest hook targets `tests` explicitly, but a bare `pytest` run would collect `examples/` and fail (`pkg_alpha` is not installed). Add to the repo root `pyproject.toml`, after the `[tool.ruff.*]` sections:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 5: Run tests to verify they pass (and collection is safe)**

Run: `uv run python -m pytest tests/test_workspace.py -v && uv run python -m pytest --collect-only -q | tail -3`
Expected: workspace tests PASS; collection summary mentions only `tests/` paths (nothing under `examples/`).

- [ ] **Step 6: Commit**

```bash
git add examples/ tests/test_workspace.py pyproject.toml
git commit -m "test: add mock monorepo fixture and pytest testpaths guard (#61)"
```

---

### Task 6: Per-package analysis helpers in `cli.py`

**Files:**
- Modify: `pytest_impacted/cli.py`
- Create: `tests/test_monorepo_cli.py`

**Interfaces:**
- Consumes: `PackageInfo` (Task 1); `clear_dep_tree_cache` from `pytest_impacted.strategies`; `discover_submodules`, `path_to_package_name` from `pytest_impacted.traversal`; existing `get_impacted_tests`, `build_strategy_with_extensions`
- Produces (all in `cli.py`, used by Task 7):
  - `_package_analysis_context(package_dir: Path)` — context manager: chdir + cache clear on entry and exit
  - `_rebase_paths(paths: list[str], root: Path) -> list[str]` — absolute paths → sorted root-relative POSIX
  - `_is_test_file_module(module_name: str) -> bool` — leaf segment `test_*` / `*_test`
  - `_all_tests_for_package(pkg: PackageInfo, root: Path) -> list[str]`
  - `_analyze_direct_package(pkg: PackageInfo, *, root: Path, git_mode, base_branch, watch_dep_files: bool, disable_ext: tuple, ext_config: dict) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_monorepo_cli.py`:

```python
"""Tests for the impacted-packages monorepo CLI."""

from pathlib import Path, PurePosixPath
from unittest.mock import patch

from pytest_impacted.cli import (
    _all_tests_for_package,
    _analyze_direct_package,
    _is_test_file_module,
    _rebase_paths,
)
from pytest_impacted.git import GitMode
from pytest_impacted.workspace import PackageInfo


EXAMPLE_MONOREPO = Path(__file__).parent.parent / "examples" / "monorepo"

PKG_ALPHA = PackageInfo(
    name="pkg-alpha",
    path=PurePosixPath("libs/pkg-alpha"),
    module="src/pkg_alpha",
    tests_dir="tests",
)
PKG_BETA = PackageInfo(
    name="pkg-beta",
    path=PurePosixPath("libs/pkg-beta"),
    module="pkg_beta",
    tests_dir="tests",
    requirements=frozenset({"pkg-alpha"}),
)


class TestHelpers:
    def test_is_test_file_module(self):
        assert _is_test_file_module("tests.test_core")
        assert _is_test_file_module("pkg.core_test")
        assert not _is_test_file_module("tests.conftest")
        assert not _is_test_file_module("pkg_alpha.core")

    def test_rebase_paths(self, tmp_path):
        (tmp_path / "a").mkdir()
        target = tmp_path / "a" / "t.py"
        target.write_text("")
        assert _rebase_paths([str(target)], tmp_path) == ["a/t.py"]


class TestAllTestsForPackage:
    def test_enumerates_all_test_files_src_layout(self):
        result = _all_tests_for_package(PKG_ALPHA, EXAMPLE_MONOREPO)
        assert result == [
            "libs/pkg-alpha/tests/test_core.py",
            "libs/pkg-alpha/tests/test_util.py",
        ]

    def test_enumerates_all_test_files_flat_layout(self):
        result = _all_tests_for_package(PKG_BETA, EXAMPLE_MONOREPO)
        assert result == ["libs/pkg-beta/tests/test_service.py"]

    def test_cwd_is_restored(self, tmp_path):
        import os

        before = os.getcwd()
        _all_tests_for_package(PKG_ALPHA, EXAMPLE_MONOREPO)
        assert os.getcwd() == before


class TestAnalyzeDirectPackage:
    def test_rebases_results_and_passes_package_config(self):
        expected_abs = str((EXAMPLE_MONOREPO / "libs/pkg-alpha/tests/test_core.py").resolve())
        with patch("pytest_impacted.cli.get_impacted_tests", return_value=[expected_abs]) as mock_git:
            result = _analyze_direct_package(
                PKG_ALPHA,
                root=EXAMPLE_MONOREPO,
                git_mode=GitMode.UNSTAGED,
                base_branch="main",
                watch_dep_files=True,
                disable_ext=(),
                ext_config={},
            )
        assert result == ["libs/pkg-alpha/tests/test_core.py"]
        kwargs = mock_git.call_args.kwargs
        assert kwargs["ns_module"] == "src/pkg_alpha"
        assert kwargs["tests_dir"] == "tests"
        assert kwargs["root_dir"] == "."

    def test_none_result_is_empty_list(self):
        with patch("pytest_impacted.cli.get_impacted_tests", return_value=None):
            result = _analyze_direct_package(
                PKG_ALPHA,
                root=EXAMPLE_MONOREPO,
                git_mode=GitMode.UNSTAGED,
                base_branch="main",
                watch_dep_files=True,
                disable_ext=(),
                ext_config={},
            )
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_monorepo_cli.py -v`
Expected: FAIL with `ImportError: cannot import name '_all_tests_for_package'`

- [ ] **Step 3: Write the implementation**

Add to `pytest_impacted/cli.py`. New imports at the top (merged into the existing import block, isort-ordered):

```python
import contextlib
from pathlib import Path

from pytest_impacted.strategies import clear_dep_tree_cache
from pytest_impacted.traversal import discover_submodules, path_to_package_name
from pytest_impacted.workspace import PackageInfo
```

New code after `configure_logging`:

```python
@contextlib.contextmanager
def _package_analysis_context(package_dir: Path):
    """Run analysis from inside a package directory with fresh caches.

    ``discover_submodules`` and the dep-tree cache are keyed by package *name*
    but resolve paths against the current working directory, so analyzing
    multiple packages from one process requires both a chdir and a cache clear
    on entry AND exit (the exit clear keeps later callers from seeing entries
    resolved against this package's directory).
    """
    with contextlib.chdir(package_dir):
        clear_dep_tree_cache()
        try:
            yield
        finally:
            clear_dep_tree_cache()


def _rebase_paths(paths: list[str], root: Path) -> list[str]:
    """Convert absolute file paths to sorted monorepo-root-relative POSIX paths."""
    resolved_root = root.resolve()
    return sorted(Path(path).resolve().relative_to(resolved_root).as_posix() for path in paths)


def _is_test_file_module(module_name: str) -> bool:
    """True when the module's leaf name matches pytest's default test file conventions."""
    leaf = module_name.rsplit(".", 1)[-1]
    return leaf.startswith("test_") or leaf.endswith("_test")


def _all_tests_for_package(pkg: PackageInfo, root: Path) -> list[str]:
    """Enumerate every test file in a package (dependency/dep-files impact selects them all)."""
    with _package_analysis_context(root / pkg.path):
        modules: dict[str, str] = {}
        if pkg.tests_dir:
            modules.update(discover_submodules(path_to_package_name(pkg.tests_dir), require_init=False))
        modules.update(discover_submodules(path_to_package_name(pkg.module), require_init=True))
        test_paths = [path for name, path in modules.items() if _is_test_file_module(name)]
        return _rebase_paths(test_paths, root)


def _analyze_direct_package(
    pkg: PackageInfo,
    *,
    root: Path,
    git_mode,
    base_branch: str,
    watch_dep_files: bool,
    disable_ext: tuple,
    ext_config: dict,
) -> list[str]:
    """Run the full single-package impact analysis for a directly-changed package."""
    strategy = build_strategy_with_extensions(
        watch_dep_files=watch_dep_files,
        disabled=disable_ext,
        ext_config=ext_config,
    )
    with _package_analysis_context(root / pkg.path):
        impacted = get_impacted_tests(
            impacted_git_mode=git_mode,
            impacted_base_branch=base_branch,
            root_dir=".",
            ns_module=pkg.module,
            tests_dir=pkg.tests_dir,
            strategy=strategy,
        )
        return _rebase_paths(impacted or [], root)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_monorepo_cli.py tests/test_cli.py -v`
Expected: all PASS (including the pre-existing `test_cli.py` suite — no regression).

- [ ] **Step 5: Commit**

```bash
git add pytest_impacted/cli.py tests/test_monorepo_cli.py
git commit -m "feat: add per-package analysis helpers for monorepo CLI (#61)"
```

---

### Task 7: `impacted_packages_cli` command, output formats, console script

**Files:**
- Modify: `pytest_impacted/cli.py`
- Modify: `pyproject.toml` (console script)
- Test: `tests/test_monorepo_cli.py`

**Interfaces:**
- Consumes: Task 6 helpers; `discover_packages`, `compute_impacted_packages` (workspace.py); `find_impacted_files_in_repo` (git.py)
- Produces: Click command `impacted_packages_cli` exported from `pytest_impacted.cli`; console script `impacted-packages`. JSON schema: `{"packages": [{"name", "path", "reason", "impacted_tests": [...]} | {"name", "path", "reason", "error"}]}`, packages sorted by name, only impacted-with-tests (or errored) packages included. Exit code 0 on success (impacted or not); `click.ClickException` (exit 1) when no packages are discovered.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_monorepo_cli.py` (add imports: `import json`, `from click.testing import CliRunner`, and add `impacted_packages_cli` to the `pytest_impacted.cli` import):

```python
class TestImpactedPackagesCli:
    def _invoke(self, args, changed_files):
        runner = CliRunner()
        with patch("pytest_impacted.cli.find_impacted_files_in_repo", return_value=changed_files):
            with patch(
                "pytest_impacted.cli._analyze_direct_package",
                return_value=["libs/pkg-alpha/tests/test_core.py"],
            ):
                return runner.invoke(
                    impacted_packages_cli, ["--root-dir", str(EXAMPLE_MONOREPO), *args]
                )

    def test_json_output_direct_and_dependency(self):
        result = self._invoke(["--format", "json"], ["libs/pkg-alpha/src/pkg_alpha/core.py"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "packages": [
                {
                    "name": "pkg-alpha",
                    "path": "libs/pkg-alpha",
                    "reason": "direct",
                    "impacted_tests": ["libs/pkg-alpha/tests/test_core.py"],
                },
                {
                    "name": "pkg-beta",
                    "path": "libs/pkg-beta",
                    "reason": "dependency",
                    "impacted_tests": ["libs/pkg-beta/tests/test_service.py"],
                },
            ]
        }

    def test_text_output_groups_by_package(self):
        result = self._invoke([], ["libs/pkg-alpha/src/pkg_alpha/core.py"])
        assert result.exit_code == 0, result.output
        assert "== pkg-alpha (libs/pkg-alpha) [direct]" in result.output
        assert "libs/pkg-alpha/tests/test_core.py" in result.output
        assert "== pkg-beta (libs/pkg-beta) [dependency]" in result.output
        assert "libs/pkg-beta/tests/test_service.py" in result.output

    def test_root_dep_file_marks_all_packages(self):
        result = self._invoke(["--format", "json"], ["uv.lock"])
        data = json.loads(result.stdout)
        assert [(p["name"], p["reason"]) for p in data["packages"]] == [
            ("pkg-alpha", "dep-files"),
            ("pkg-beta", "dep-files"),
        ]
        # dep-files impact selects ALL tests, not just the direct-analysis result
        assert data["packages"][0]["impacted_tests"] == [
            "libs/pkg-alpha/tests/test_core.py",
            "libs/pkg-alpha/tests/test_util.py",
        ]

    def test_no_changes_yields_empty_result(self):
        result = self._invoke(["--format", "json"], [])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"packages": []}

    def test_no_packages_discovered_is_an_error(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(impacted_packages_cli, ["--root-dir", str(tmp_path)])
        assert result.exit_code != 0

    def test_failing_package_does_not_sink_the_others(self):
        runner = CliRunner()
        with patch("pytest_impacted.cli.find_impacted_files_in_repo", return_value=["libs/pkg-alpha/src/pkg_alpha/core.py"]):
            with patch("pytest_impacted.cli._analyze_direct_package", side_effect=RuntimeError("boom")):
                result = runner.invoke(
                    impacted_packages_cli, ["--root-dir", str(EXAMPLE_MONOREPO), "--format", "json"]
                )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        by_name = {p["name"]: p for p in data["packages"]}
        assert by_name["pkg-alpha"]["error"] == "boom"
        assert by_name["pkg-beta"]["impacted_tests"] == ["libs/pkg-beta/tests/test_service.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_monorepo_cli.py::TestImpactedPackagesCli -v`
Expected: FAIL with `ImportError: cannot import name 'impacted_packages_cli'`

- [ ] **Step 3: Write the implementation**

Add to `pytest_impacted/cli.py`. Extend imports: `import json`; `import logging` is already imported; extend the workspace import to `from pytest_impacted.workspace import PackageInfo, compute_impacted_packages, discover_packages`; add `from pytest_impacted.git import GitMode, find_impacted_files_in_repo` (replacing the existing `GitMode`-only import). Add a module logger after the import block: `logger = logging.getLogger(__name__)`.

Append after `impacted_tests_cli` (before `_register_extension_options`):

```python
@click.command(context_settings={"show_default": True})
@click.option("--git-mode", default=GitMode.UNSTAGED, help="Git mode.")
@click.option("--base-branch", default="main", help="Base branch.")
@click.option(
    "--root-dir",
    default=".",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Monorepo root directory.",
)
@click.option("--format", "output_format", type=click.Choice(["text", "json"]), default="text", help="Output format.")
@click.option("--verbose", is_flag=True, help="Verbose output.")
@click.option("--no-dep-files", is_flag=True, default=False, help="Disable dependency file change detection.")
@click.option("--disable-ext", multiple=True, default=(), help="Disable a strategy extension by name (repeatable).")
def impacted_packages_cli(git_mode, base_branch, root_dir, output_format, verbose, no_dep_files, disable_ext, **ext_kwargs):
    """Discover all packages in a monorepo and report impacted tests for each.

    Packages are discovered from [tool.uv.workspace] globs when present,
    otherwise by scanning for pyproject.toml files. Output goes to stdout;
    all diagnostics go to stderr.
    """
    configure_logging(verbose=verbose)
    root = Path(root_dir).resolve()

    packages = discover_packages(root)
    if not packages:
        raise click.ClickException(f"No packages discovered under {root}")
    click.secho(
        "Discovered {} package(s): {}".format(len(packages), ", ".join(pkg.name for pkg in packages)),
        fg="blue",
        bold=True,
        err=True,
    )

    changed_files = find_impacted_files_in_repo(root, git_mode=git_mode, base_branch=base_branch) or []
    impacted = compute_impacted_packages(changed_files, packages, watch_dep_files=not no_dep_files)

    results = []
    for name in sorted(impacted):
        entry = impacted[name]
        pkg = entry.package
        record = {"name": pkg.name, "path": str(pkg.path), "reason": entry.reason}
        try:
            if entry.reasons == {"direct"}:
                tests = _analyze_direct_package(
                    pkg,
                    root=root,
                    git_mode=git_mode,
                    base_branch=base_branch,
                    watch_dep_files=not no_dep_files,
                    disable_ext=disable_ext,
                    ext_config=ext_kwargs,
                )
            else:
                # Any dependency/dep-files reason selects ALL of the package's tests,
                # a superset of what direct analysis could return.
                tests = _all_tests_for_package(pkg, root)
        except Exception as exc:  # noqa: BLE001 — one broken package must not sink the others
            logger.warning("Analysis failed for package %r: %s", pkg.name, exc)
            record["error"] = str(exc)
            results.append(record)
            continue
        if tests:
            record["impacted_tests"] = tests
            results.append(record)

    if output_format == "json":
        print(json.dumps({"packages": results}, indent=2))
        return

    if not results:
        click.secho("No impacted packages found.", fg="red", bold=True, err=True)
    for record in results:
        print(f"== {record['name']} ({record['path']}) [{record['reason']}]")
        if "error" in record:
            click.secho(f"analysis failed: {record['error']}", fg="red", bold=True, err=True)
        for test_path in record.get("impacted_tests", ()):
            print(test_path)
```

Also register extension options for the new command — change the last line of the module to:

```python
_register_extension_options(impacted_tests_cli)
_register_extension_options(impacted_packages_cli)
```

- [ ] **Step 4: Register the console script**

In the root `pyproject.toml`, extend `[project.scripts]`:

```toml
[project.scripts]
impacted-tests = "pytest_impacted.cli:impacted_tests_cli"
impacted-packages = "pytest_impacted.cli:impacted_packages_cli"
```

- [ ] **Step 5: Run tests and a smoke run to verify**

Run: `uv run python -m pytest tests/test_monorepo_cli.py tests/test_cli.py -v`
Expected: all PASS

Run: `uv sync --all-extras --dev && uv run impacted-packages --root-dir examples/monorepo || true`
Expected: stderr shows `Discovered 2 package(s): pkg-alpha, pkg-beta`; since `examples/monorepo` sits inside the main repo's git tree, changed-file behavior depends on the working tree state — the smoke check only validates discovery + wiring, not impact results.

- [ ] **Step 6: Commit**

```bash
git add pytest_impacted/cli.py tests/test_monorepo_cli.py pyproject.toml uv.lock
git commit -m "feat: add impacted-packages CLI for monorepo-wide impact analysis (#61)"
```

---

### Task 8: End-to-end git integration test (slow)

**Files:**
- Test: `tests/test_monorepo_cli.py`

**Interfaces:**
- Consumes: the `examples/monorepo/` fixture, `impacted_packages_cli`, GitPython

- [ ] **Step 1: Write the integration tests**

Append to `tests/test_monorepo_cli.py` (add imports: `import shutil`, `import pytest`, `from git import Repo`):

```python
def _make_monorepo_git_repo(tmp_path):
    repo_dir = tmp_path / "monorepo"
    shutil.copytree(EXAMPLE_MONOREPO, repo_dir)
    repo = Repo.init(repo_dir, initial_branch="main")
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test User")
        config.set_value("user", "email", "test@example.com")
    repo.git.add(A=True)
    repo.index.commit("initial")
    return repo_dir


@pytest.mark.slow
class TestMonorepoEndToEnd:
    def test_change_in_alpha_impacts_alpha_directly_and_beta_transitively(self, tmp_path):
        repo_dir = _make_monorepo_git_repo(tmp_path)
        core = repo_dir / "libs/pkg-alpha/src/pkg_alpha/core.py"
        core.write_text(core.read_text() + "\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n")

        result = CliRunner().invoke(impacted_packages_cli, ["--root-dir", str(repo_dir), "--format", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        by_name = {p["name"]: p for p in data["packages"]}

        assert by_name["pkg-alpha"]["reason"] == "direct"
        # AST analysis: test_core.py imports pkg_alpha.core (changed); test_util.py does not.
        assert by_name["pkg-alpha"]["impacted_tests"] == ["libs/pkg-alpha/tests/test_core.py"]
        assert by_name["pkg-beta"]["reason"] == "dependency"
        assert by_name["pkg-beta"]["impacted_tests"] == ["libs/pkg-beta/tests/test_service.py"]

    def test_root_lockfile_change_impacts_everything(self, tmp_path):
        repo_dir = _make_monorepo_git_repo(tmp_path)
        (repo_dir / "uv.lock").write_text("# bumped\n")

        result = CliRunner().invoke(impacted_packages_cli, ["--root-dir", str(repo_dir), "--format", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert [(p["name"], p["reason"]) for p in data["packages"]] == [
            ("pkg-alpha", "dep-files"),
            ("pkg-beta", "dep-files"),
        ]
```

- [ ] **Step 2: Run the integration tests**

Run: `uv run python -m pytest tests/test_monorepo_cli.py -m slow -v`
Expected: both PASS. If `pkg-alpha` shows extra impacted tests, the AST graph is over-selecting — debug before proceeding (do not weaken the assertion).

- [ ] **Step 3: Run the full non-slow suite for regressions**

Run: `uv run python -m pytest --cov=pytest_impacted --cov-branch tests -m 'not slow'`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_monorepo_cli.py
git commit -m "test: add end-to-end monorepo integration tests (#61)"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/usage.md` (new section after the existing "Monorepo Layout" subsection)
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the usage docs**

In `docs/usage.md`, immediately after the existing "### Monorepo Layout" subsection (which documents single-package-in-monorepo usage), add:

````markdown
### Multi-Package Monorepos: the `impacted-packages` CLI

When a monorepo contains **multiple** Python packages, use the `impacted-packages`
console script to analyze all of them in one invocation. It discovers every package,
computes which ones are affected by the current git changes (directly or through
inter-package dependencies), and emits a per-package list of impacted tests that a
CI system can fan out into parallel jobs.

```bash
impacted-packages --root-dir . --git-mode branch --base-branch origin/main --format json
```

**Package discovery:**

1. If the root `pyproject.toml` declares a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
   (`[tool.uv.workspace]`), its `members`/`exclude` globs are honored. The root itself
   is included when it has a `[project]` table.
2. Otherwise the tree is scanned recursively for `pyproject.toml` files (hidden
   directories, `venv`, `node_modules`, `build`, `dist` are skipped).

**Per-package configuration:** each package's `pyproject.toml` is consulted for
`[tool.pytest.ini_options]` `impacted_module` / `impacted_tests_dir`. When absent,
conventional layouts are inferred automatically: the module directory is derived from
the project name (checking `src/<name>/` then `<name>/`), and `tests/` is used when it
exists. Packages that cannot be resolved are skipped with a warning.

**Impact reasons:** each reported package carries a `reason`:

| Reason | Meaning | Tests selected |
|---|---|---|
| `direct` | Files changed inside the package | Precise AST-based impact analysis |
| `dependency` | A workspace package it depends on changed | All of the package's tests |
| `dep-files` | A dependency file changed at the monorepo root (e.g. `uv.lock`) | All of the package's tests |

Reasons combine with `+` (e.g. `direct+dependency`). Inter-package dependencies are
read from each package's `[project.dependencies]` (and `optional-dependencies`),
matched against workspace package names. Dependency selection is deliberately
coarse — all tests run for dependents — consistent with the plugin's philosophy of
preferring false positives over missed tests. Disable dependency-file triggering
with `--no-dep-files`.

**Output:** results go to stdout, diagnostics to stderr. `--format json` emits:

```json
{
  "packages": [
    {"name": "pkg-alpha", "path": "libs/pkg-alpha", "reason": "direct",
     "impacted_tests": ["libs/pkg-alpha/tests/test_core.py"]},
    {"name": "pkg-beta", "path": "libs/pkg-beta", "reason": "dependency",
     "impacted_tests": ["libs/pkg-beta/tests/test_service.py"]}
  ]
}
```

Test paths are always monorepo-root-relative. A GitHub Actions matrix fan-out:

```yaml
jobs:
  detect:
    runs-on: ubuntu-latest
    outputs:
      packages: ${{ steps.impact.outputs.packages }}
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - run: pip install pytest-impacted
      - id: impact
        run: |
          echo "packages=$(impacted-packages --git-mode branch --base-branch origin/main --format json | jq -c '.packages')" >> "$GITHUB_OUTPUT"
  test:
    needs: detect
    if: ${{ needs.detect.outputs.packages != '[]' }}
    strategy:
      matrix:
        package: ${{ fromJson(needs.detect.outputs.packages) }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cd ${{ matrix.package.path }} && pip install -e . && pytest ${{ matrix.package.impacted_tests }}
```

A runnable example monorepo lives in the repository under
[`examples/monorepo/`](https://github.com/promptromp/pytest-impacted/tree/main/examples/monorepo).
````

- [ ] **Step 2: Update the README**

In `README.md`, locate the section describing the `impacted-tests` CLI (search for `impacted-tests`) and add after it:

````markdown
### Monorepos with multiple packages

The `impacted-packages` CLI analyzes an entire multi-package monorepo in one run:
it discovers packages (uv-workspace globs or `pyproject.toml` scan), tracks
inter-package dependencies, and emits per-package impacted tests for CI fan-out:

```bash
impacted-packages --git-mode branch --base-branch origin/main --format json
```

See the [usage guide](https://promptromp.github.io/pytest-impacted/usage/) for
discovery rules, impact reasons, and a GitHub Actions matrix example.
````

- [ ] **Step 3: Update CLAUDE.md**

In `CLAUDE.md`:

1. In "Module Responsibilities", after the **cli.py** bullet, add:

```markdown
- **workspace.py**: Multi-package monorepo support (pure filesystem/TOML — no imports, no git). `discover_packages` finds packages via `[tool.uv.workspace]` globs or a recursive `pyproject.toml` scan; per-package config comes from `[tool.pytest.ini_options]` (`impacted_module`/`impacted_tests_dir`) with convention-based inference fallback (`src/<name>/` then `<name>/`, `tests/`). `build_package_graph` builds the inter-package dependency DiGraph from `[project.dependencies]` (edge B→A when A depends on B, via `packaging.requirements`). `compute_impacted_packages` maps changed files to packages (longest-prefix), expands transitively (reason `dependency`), and marks all packages on root-level dependency-file changes (reason `dep-files`)
```

2. Extend the **cli.py** bullet to mention both commands:

```markdown
- **cli.py**: Standalone CLI tools (Click-based) for CI integration: `impacted-tests` (single package) and `impacted-packages` (monorepo-wide; per-package analysis chdirs into each package and clears LRU caches on entry/exit via `_package_analysis_context` since `discover_submodules`/`get_impacted_tests` resolve paths against the CWD)
```

3. In "Test Structure", mention: `tests/test_workspace.py` (workspace unit tests), `tests/test_monorepo_cli.py` (CLI + slow end-to-end tests), and the checked-in fixture `examples/monorepo/` (excluded from collection via `testpaths = ["tests"]`).

- [ ] **Step 4: Build the docs site locally to validate**

Run: `uv run mkdocs build --strict 2>&1 | tail -5` (if mkdocs is not in the dev deps, fall back to a markdown sanity read of the edited files)
Expected: build succeeds (or fallback review finds no broken formatting).

- [ ] **Step 5: Run the full verification suite**

Run: `uv run python -m pytest --cov=pytest_impacted --cov-branch tests && uv run mypy pytest_impacted && ruff check && ruff format --check`
Expected: all pass, including slow tests.

- [ ] **Step 6: Commit**

```bash
git add docs/usage.md README.md CLAUDE.md
git commit -m "docs: document impacted-packages monorepo CLI (#61)"
```
