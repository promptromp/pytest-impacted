# Design: `impacted-packages` CLI — monorepo-wide impacted test analysis

**Date:** 2026-07-23
**Issue:** [#61 — CLI: support producing impacted tests for all monorepo packages](https://github.com/promptromp/pytest-impacted/issues/61)
**Status:** Approved

## Problem

pytest-impacted's existing monorepo support is scoped to a single package: `find_repo`
searches parent directories for the git root and `normalize_git_paths` rebases paths, but
each invocation targets one `--module`, and changes in sibling directories are ignored. In a
multi-package monorepo, CI must invoke `impacted-tests` once per package, which is slow and
cumbersome. Issue #61 asks for a single CLI invocation that discovers all packages, performs
impact analysis across them, and emits per-package impacted test lists so CI can fan out
(e.g. Buildkite/GitHub Actions dynamic pipelines).

## Scope decisions (made during brainstorming)

1. **MVP depth — metadata-level cross-package impact.** Per-package AST analysis stays as-is.
   Cross-package impact is derived from inter-package dependency *metadata*
   (`[project.dependencies]`), not a global AST graph: if package B changed and package A
   depends on B, **all** of A's tests are impacted. Coarse but correct, matching the
   project's err-toward-false-positives philosophy. A full cross-package AST graph is a
   possible phase 2.
2. **Discovery — uv workspace first, filesystem scan fallback.**
3. **Surface — new `impacted-packages` console script**, text output by default,
   `--format json` for CI.
4. **Per-package config — read `[tool.pytest.ini_options]` when present, else infer
   conventionally; skip (with warning) packages where inference fails.**
5. **Out of scope:** per-package extension/strategy configuration (flagged as out of scope in
   the issue itself). Extensions installed in the running environment apply globally with the
   same `--disable-ext` semantics as the existing CLI.

## Architecture

Pipeline: discover packages → map changed files to packages → expand via package dependency
graph → per-package impacted-test analysis (existing `get_impacted_tests`) → grouped output.

### New module: `pytest_impacted/workspace.py`

Package discovery and the inter-package dependency graph. Pure and side-effect free: stdlib
`tomllib` for parsing, no git access, no module imports (consistent with the project's
no-import-at-analysis-time principle).

- **`PackageInfo`** (frozen dataclass):
  - `name: str` — PEP 503-normalized `[project.name]`
  - `path: Path` — package directory (contains `pyproject.toml`), relative to monorepo root
  - `module: str` — impacted-module path relative to the package dir (e.g. `src/pkg_a`)
  - `tests_dir: str | None` — tests directory relative to the package dir
  - `workspace_deps: frozenset[str]` — normalized names of other workspace packages this
    package depends on

- **`discover_packages(root: Path) -> list[PackageInfo]`**:
  - If the root `pyproject.toml` contains `[tool.uv.workspace]`: expand `members` globs minus
    `exclude` globs; each matching directory containing a `pyproject.toml` is a package. The
    root itself is included when it has a `[project]` table.
  - Otherwise: recursive scan for `pyproject.toml` files, pruning hidden directories,
    `.venv`, `venv`, `node_modules`, `build`, `dist`, and `site-packages`.
  - Directories whose `pyproject.toml` lacks a `[project].name` are skipped with a warning.

- **Per-package config resolution** (inside discovery):
  - `[tool.pytest.ini_options]` `impacted_module` and `impacted_tests_dir` are used verbatim
    when present.
  - Otherwise inferred: module directory from the normalized project name (underscored),
    checked at `src/<name>/` then `<name>/` (must contain `__init__.py`); tests dir is
    `tests/` when that directory exists, else `None`.
  - Packages whose module cannot be resolved are skipped with a stderr warning naming the
    package and the reason.

- **`build_package_graph(packages) -> nx.DiGraph`**: nodes are package names; edge B → A when
  A's `[project.dependencies]` or `[project.optional-dependencies]` contains a requirement
  whose normalized name equals workspace package B. Requirement strings are parsed with
  `packaging.requirements.Requirement` (tolerant of extras/markers/specifiers); unparseable
  requirement strings are ignored. `packaging` becomes an explicit runtime dependency (it is
  currently only transitively available via pytest).

### Orchestration: `impacted-packages` command

New Click command in `cli.py`, registered as an `impacted-packages` console script in
`pyproject.toml`. Flags mirror the existing CLI — `--git-mode`, `--base-branch`,
`--root-dir` (the monorepo root, default `.`), `--no-dep-files`, `--disable-ext`,
`--verbose` — plus `--format [text|json]` (default `text`). No `--module`/`--tests-dir`
(those are per-package, resolved by discovery).

Flow:

1. `discover_packages(root_dir)`.
2. `find_impacted_files_in_repo(root_dir, ...)` once, at the monorepo root, to get the
   changed-file list (root-relative).
3. **Direct impact:** map each changed file to the package whose `path` is its longest
   matching prefix. Files not under any package (e.g. root-level files) map to no package.
4. **Dependency-file impact:** if a changed file at the *monorepo root* matches the
   dependency-file patterns used by `DependencyFileImpactStrategy` (`uv.lock`,
   `pyproject.toml`, `requirements*.txt`, …), **all** packages are impacted with reason
   `dep-files`. Suppressed by `--no-dep-files`. (Dependency files *inside* a package are
   handled by the per-package run in step 6, exactly as today.)
5. **Transitive impact:** descendants of directly-changed packages in the package dependency
   graph get reason `dependency`.
6. **Per-package analysis:**
   - *Direct* packages: call the existing `get_impacted_tests()` with
     `root_dir=<package path>`, `ns_module=<module>`, `tests_dir=<tests_dir>`, and a strategy
     from `build_strategy_with_extensions()` (same flags as the existing CLI). No changes to
     `api.py` — the existing single-package path normalization already handles running from a
     package subdirectory.
   - *Dependency*/*dep-files* packages (no direct file changes): all their tests are
     impacted — enumerate test modules via `discover_submodules` on the package's tests dir
     (and test modules under the namespace module when no tests dir), then resolve to file
     paths. If a package is both direct and transitive, it is analyzed as direct but
     escalated to all-tests (union), reported with reason `direct+dependency`.
7. Emit output; test paths are rebased to be relative to the monorepo root so CI can use them
   without knowing package-relative conventions.

Failure isolation: an exception during one package's analysis logs a warning with the package
name and continues with the remaining packages (mirrors the extension-hook error philosophy);
the failed package appears in output with an `error` field (JSON) / warning line (text).

### Output

Stdout carries only results; all diagnostics go to stderr (existing CLI convention). Only
impacted packages appear. Exit code 0 regardless of whether anything is impacted (CI branches
on content, not exit code); non-zero only on operational errors (bad root, no packages found).

Text format:

```
== pkg-a (libs/pkg-a) [direct]
libs/pkg-a/tests/test_x.py
== pkg-b (libs/pkg-b) [dependency]
libs/pkg-b/tests/test_y.py
```

JSON format (`--format json`):

```json
{
  "packages": [
    {
      "name": "pkg-a",
      "path": "libs/pkg-a",
      "reason": "direct",
      "impacted_tests": ["libs/pkg-a/tests/test_x.py"]
    }
  ]
}
```

`reason` is one of `direct`, `dependency`, `dep-files`, `direct+dependency`.

## Testing

- **Unit — `tests/test_workspace.py`:** discovery in uv-workspace mode (members/exclude
  globs), scan mode (pruning), config read vs. inference (src-layout and flat), skip-warning
  paths, package-graph construction including extras/specifiers and unparseable requirements.
  Fixtures are `tmp_path`-built `pyproject.toml` trees; no git needed.
- **Unit — orchestration:** file→package longest-prefix mapping, transitive expansion,
  root-dep-file → all-packages, reason merging, failure isolation. Git and `get_impacted_tests`
  mocked (consistent with the existing heavy use of `unittest.mock`).
- **Integration — marked `slow`:** a two-package monorepo (pkg-b depends on pkg-a) built in a
  temporary git repo, patterned on existing `tests/test_git.py` fixtures. Assert: change in
  pkg-a selects pkg-a's impacted tests (direct) and all of pkg-b's tests (dependency); root
  `uv.lock` change selects everything; both output formats via `click.testing.CliRunner`.

## Documentation (same PR)

- `docs/usage.md`: new "Multi-package monorepos" section under the existing monorepo docs —
  discovery rules, config/inference, reasons, CI fan-out example (JSON → matrix).
- `README.md`: brief mention of `impacted-packages` alongside `impacted-tests`.
- `CLAUDE.md`: `workspace.py` module responsibilities + CLI addition.

## Phase 2 candidates (explicitly not in this MVP)

- Full cross-package AST dependency graph for precise transitive test selection.
- Per-package strategy/extension configuration.
- Parallel per-package analysis (`concurrent.futures`) if runtime on large monorepos warrants it.
