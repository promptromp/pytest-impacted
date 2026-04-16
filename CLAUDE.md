# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pytest-impacted is a pytest plugin that selectively runs tests impacted by code changes via git introspection, AST parsing, and dependency graph analysis. It analyzes Python import dependencies using astroid, builds dependency graphs with NetworkX, and uses GitPython to identify changed files. The philosophy is to err on the side of caution—favoring false positives over missed impacted tests.

**Key design principle**: All module discovery and import analysis is done via filesystem scanning and AST parsing—modules are never imported at analysis time. This avoids side effects from module-level code (e.g. monkey patching, database connections, application factory calls).

## Development Commands

This project uses `uv` for dependency and virtualenv management.

```bash
# Install/sync development environment
uv sync --all-extras --dev

# Install with Rust acceleration (pre-built wheels)
pip install pytest-impacted[fast]

# Or build Rust extension from source (requires Rust toolchain + maturin)
pip install maturin
cd rust && maturin develop --release

# Add a new dependency
uv add <package_name>

# Run all tests
uv run python -m pytest

# Run tests with coverage
uv run python -m pytest --cov=pytest_impacted --cov-branch tests

# Run tests excluding slow tests (matches pre-commit behavior)
uv run python -m pytest --cov=pytest_impacted --cov-branch tests -m 'not slow'

# Run a single test file or function
uv run python -m pytest tests/test_api.py
uv run python -m pytest tests/test_api.py::test_function_name

# Linting and formatting (ruff: line-length=120, target=py311, double quotes)
ruff check --fix
ruff format

# Type checking
uv run mypy pytest_impacted

# Run all pre-commit hooks (fail_fast: true)
pre-commit run --all-files
```

## Core Architecture

The pipeline: Git identifies changed files → Files converted to Python modules → AST parser builds dependency graph → Graph analysis finds impacted test modules → Tests are filtered. In parallel, dependency file changes (e.g. `uv.lock`, `requirements.txt`) trigger all tests via `DependencyFileImpactStrategy`.

### Module Responsibilities

- **plugin.py**: pytest plugin entry point via `pytest11` entry point; handles CLI options, config validation (module name, base branch, tests dir), test collection filtering, and extension option registration
- **__init__.py**: Public API exports for extension developers (`ImpactStrategy`, `ConfigOption`, `StrategyProtocol`)
- **api.py**: Orchestration layer (`get_impacted_tests`, `matches_impacted_tests`); accepts an optional pre-built `strategy` parameter, falling back to built-in defaults when none is provided
- **extensions.py**: Extension/plugin system for third-party strategies. Provides entry-point-based discovery (`pytest_impacted.strategies` group), `ConfigOption` for declarative config, `StrategyProtocol` for duck-typed strategies, and `build_strategy_with_extensions()` to compose built-in + extension strategies
- **strategies.py**: Strategy pattern for impact analysis (see below)
- **git.py**: Git integration for finding changed files (unstaged changes and branch diffs). Key functions: `find_repo` (wraps `Repo()` with `search_parent_directories=True` for monorepo support), `normalize_git_paths` (converts git-root-relative paths to working-dir-relative paths), `find_impacted_files_in_repo` (main entry point)
- **graph.py**: Dependency graph construction and querying using NetworkX; uses `discover_submodules` for filesystem-based module discovery and `parse_file_imports` for AST parsing. When the Rust extension is available, `build_dep_tree` uses parallel batch parsing via `rust_parse_all_imports` instead of sequential astroid parsing
- **_rust.py**: Safe import wrapper for the optional Rust extension (`pytest_impacted_rs`). Exports `RUST_AVAILABLE` flag and `rust_parse_file_imports` / `rust_parse_all_imports` functions
- **parsing.py**: AST parsing using astroid to extract import relationships. Node classes are imported from `astroid.nodes` (required since astroid v4). Key functions: `parse_file_imports` (reads source files directly, uses `_ModuleProxy` for relative import resolution without importing), `is_module_path`, `is_test_module`, `normalize_path`
- **traversal.py**: Module discovery and path/module name conversion. Key functions: `find_non_package_prefix` (detects src-layout by splitting path into non-package prefix and importable root), `_discover_pkgutil_impl` (recursive pkgutil-based discovery that handles non-package prefixes for correct module naming), `discover_submodules` (LRU-cached, with `require_init` parameter: `True` uses `pkgutil.iter_modules` for source packages, `False` uses `Path.rglob` for test directories that may lack `__init__.py`; handles src-layout automatically), `path_to_package_name` (pure path manipulation, no imports), `resolve_files_to_modules`, `resolve_modules_to_files` (requires `ns_module` parameter)
- **cli.py**: Standalone `impacted-tests` CLI tool (Click-based) for CI integration
- **display.py**: Console output formatting using pytest's `terminalreporter`

### Strategy Pattern

Impact analysis uses a strategy-based architecture defined in `strategies.py`:

- **`ImpactStrategy`** (ABC): Base class defining `find_impacted_tests()` interface. Has optional `config_options: ClassVar[list[ConfigOption]]` and `priority: ClassVar[int]` for extension metadata
- **`ASTImpactStrategy`**: Default strategy using AST parsing and dependency graph traversal
- **`PytestImpactStrategy`**: Extends AST analysis with pytest-specific handling—when `conftest.py` files change, all tests in the same directory and subdirectories are considered impacted
- **`DependencyFileImpactStrategy`**: Detects changes in dependency/config files (`uv.lock`, `requirements.txt`, `pyproject.toml`, `Pipfile.lock`, `poetry.lock`, `setup.py`, `setup.cfg`, `requirements/*.txt`) and marks all test modules as impacted. Accepts custom patterns via constructor. Enabled by default; disable with `--no-impacted-dep-files`
- **`CompositeImpactStrategy`**: Combines multiple strategies, deduplicates and sorts results. Builds the dependency graph once via `cached_build_dep_tree` and passes it as `dep_tree` to all sub-strategies

The default strategy composition is built by `get_default_strategies()` in `strategies.py` and wrapped in `CompositeImpactStrategy` by `api.py`. The orchestrator (`api.py`) has no strategy-specific logic—it always passes `changed_files` and `impacted_modules` (which may be empty) to the composite, and each strategy decides what to do. This means strategies like `DependencyFileImpactStrategy` that operate on non-Python files work naturally without special-casing in the orchestrator.

All strategies receive a required keyword-only `dep_tree: nx.DiGraph` parameter containing the pre-built dependency graph. The graph is built once by the orchestration layer (`api.py`) and passed through `CompositeImpactStrategy` to all sub-strategies, avoiding redundant construction. The `resolve_impacted_tests` utility from `graph.py` is exported via `__init__.py` for use by extension developers.

Dependency tree building uses an LRU cache (`cached_build_dep_tree` in `strategies.py`, maxsize=8) with `clear_dep_tree_cache()` for invalidation (also clears `discover_submodules` cache).

### Extension System

Third-party packages can register custom strategies via Python entry points in the `pytest_impacted.strategies` group. The extension system is in `extensions.py` and provides:

- **`ConfigOption`**: Frozen dataclass for declaring extension config options (name, help, type, default, required)
- **`ExtensionMetadata`**: Metadata about a discovered extension (name, strategy_class, config_options, priority)
- **`StrategyProtocol`**: `runtime_checkable` Protocol for duck-typed strategies (no ABC inheritance needed)
- **`discover_extension_metadata()`**: LRU-cached discovery via `importlib.metadata.entry_points()`. Validates classes, extracts `config_options` and `priority` ClassVars
- **`load_extensions()`**: Instantiates discovered strategies with config, using `inspect.signature` to pass matching constructor params
- **`build_strategy_with_extensions()`**: Main builder—combines `get_default_strategies()` + extensions, sorts by priority, wraps in `CompositeImpactStrategy`

Extension config options are auto-registered as CLI flags (`--impacted-ext-{name}-{option}`) and ini values (`impacted_ext_{name}_{option}`). Extensions can be disabled with `--impacted-disable-ext {name}` (repeatable). The `__init__.py` exports `ImpactStrategy`, `ConfigOption`, `StrategyProtocol`, `resolve_impacted_tests`, `discover_submodules`, and `parse_file_imports` as the public API for extension developers — `discover_submodules` enumerates Python modules from a package (for walking the source tree) and `parse_file_imports` reuses pytest-impacted's own AST-based import extraction so extensions don't diverge from the core on what counts as an import. Strategies receive the pre-built dependency graph as a required keyword-only `dep_tree` parameter on `find_impacted_tests()` and can use `resolve_impacted_tests(modules, dep_tree)` for standard graph traversal. Built-in strategies always run first, followed by extensions sorted by the `priority` ClassVar (default `100`, lower runs earlier) — ordering rarely affects correctness since `CompositeImpactStrategy` unions all results.

**Lifecycle hooks**: `ImpactStrategy` exposes optional `enrich_dep_tree(dep_tree, *, ns_module, tests_package, root_dir, session)`, `setup(*, ns_module, tests_package, root_dir, session, dep_tree)`, and `teardown()` methods with no-op defaults. `api.get_impacted_tests` drives the pipeline as `cached_build_dep_tree → .copy() → strategy.enrich_dep_tree(tree, context) → strategy.setup(...) → strategy.find_impacted_tests(...) → strategy.teardown()` (teardown in a `finally` block so it always fires). The copy step is load-bearing: it prevents enrichment from polluting the LRU-cached base graph, so the same module in the same process (e.g. repeated pytester runs) always starts from a clean base. `enrich_dep_tree` receives the same context kwargs as `setup` so scan-based enrichers can walk the source tree with `discover_submodules` + `parse_file_imports` from inside the hook — a scan-then-enrich pattern that produces synthetic edges the built-in AST strategy then traverses automatically. `CompositeImpactStrategy` propagates `enrich_dep_tree` and `setup` in list order, `teardown` in reverse (LIFO); exceptions in any sub-strategy's hook are logged at WARNING level on `pytest_impacted.strategies` and swallowed so one misbehaving extension cannot prevent the others from running. Extensions doing O(source-tree) scans (e.g. pytest-impacted-microcosm's binding index) should move that work from lazy-init inside `find_impacted_tests` into `enrich_dep_tree` (where they can both scan AND inject the resulting edges in one hook), or into `setup` if they only need per-strategy state and not graph mutation.

**Cross-run caching convention**: pytest-impacted does not expose a built-in cache service for extensions, but documents a filesystem convention in `docs/extensions.md` — store per-extension state under `.pytest-impacted-cache/<extension-name>/` next to `.pytest_cache/`, invalidate with an mtime hash of the scanned files, expose the location as a `ConfigOption` so users can override it. `.pytest-impacted-cache/` is in the repo's `.gitignore` so the convention is reinforced by default. This is a convention, not an API — a future integrated `Cache` service may supersede it.

### Test Structure

Tests mirror the source structure. The `tests/strategies/` subdirectory contains per-strategy tests (`test_ast_impact.py`, `test_pytest_impact.py`, `test_composite_impact.py`, `test_dependency_file_impact.py`, `test_caching.py`, `test_integration.py`). Extension system tests are in `tests/test_extensions.py` (unit tests) and `tests/test_extension_integration.py` (pytester integration). Tests use `unittest.mock` extensively and the `pytester` pytest plugin (enabled in `conftest.py`) for testing plugin behavior. Some tests are marked `@pytest.mark.slow`.

## Documentation

The project has four documentation surfaces that must stay in sync:

- **`README.md`** — project home page (also served as MkDocs home via `mkdocs.yml`)
- **`docs/usage.md`** — detailed usage guide for users running impact analysis (MkDocs site)
- **`docs/extensions.md`** — dedicated guide for extension authors (MkDocs site): programmatic strategies, packaged-extension entry points, lifecycle hooks, extension API helpers, cache conventions, error handling, testing patterns
- **`CLAUDE.md`** — this file (architecture reference for Claude Code)

The documentation site uses [MkDocs Material](https://squidfun.github.io/mkdocs-material/) and is published to GitHub Pages at `https://promptromp.github.io/pytest-impacted`. Configuration is in `mkdocs.yml`.

## Special Instructions

- **Keep docs in sync**: When making significant changes to the codebase (new features, API changes, architectural changes, new CLI options, strategy changes), always update `CLAUDE.md`, `README.md`, and the relevant docs page (`docs/usage.md` for end-user workflow, `docs/extensions.md` for extension/strategy author APIs) to reflect those changes in the same PR.
- **Different audiences**: `README.md` is a concise overview for GitHub/PyPI visitors; `docs/usage.md` is the user-facing reference for running pytest-impacted; `docs/extensions.md` targets developers building third-party strategies/extensions. Keep extension-author content in `docs/extensions.md` and do not let it grow back into `docs/usage.md`.

## Configuration Notes

- Ruff: line-length=120, target-version=py311, double quote style, T201 (print) allowed
- Pre-commit hooks: ruff, mypy, pytest with coverage (fail_fast: true)
- CI matrix: Python 3.11, 3.12, 3.13, 3.14
- Python 3.11+ minimum required
