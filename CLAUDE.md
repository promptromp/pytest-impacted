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

The pipeline: Git identifies changed files → Files converted to Python modules → AST parser builds dependency graph → Graph analysis finds impacted test modules → Tests are filtered.

### Module Responsibilities

- **plugin.py**: pytest plugin entry point via `pytest11` entry point; handles CLI options, config validation (module name, base branch, tests dir), and test collection filtering
- **api.py**: Orchestration layer (`get_impacted_tests`, `matches_impacted_tests`); creates the default composite strategy
- **strategies.py**: Strategy pattern for impact analysis (see below)
- **git.py**: Git integration for finding changed files (unstaged changes and branch diffs)
- **graph.py**: Dependency graph construction and querying using NetworkX; uses `discover_submodules` for filesystem-based module discovery and `parse_file_imports` for AST parsing
- **parsing.py**: AST parsing using astroid to extract import relationships. Key functions: `parse_file_imports` (reads source files directly, uses `_ModuleProxy` for relative import resolution without importing), `is_module_path`, `is_test_module`, `normalize_path`
- **traversal.py**: Module discovery and path/module name conversion. Key functions: `discover_submodules` (LRU-cached filesystem scanning via `pkgutil.iter_modules`), `resolve_files_to_modules`, `resolve_modules_to_files` (requires `ns_module` parameter)
- **cli.py**: Standalone `impacted-tests` CLI tool (Click-based) for CI integration
- **display.py**: Console output formatting using pytest's `terminalreporter`

### Strategy Pattern

Impact analysis uses a strategy-based architecture defined in `strategies.py`:

- **`ImpactStrategy`** (ABC): Base class defining `find_impacted_tests()` interface
- **`ASTImpactStrategy`**: Default strategy using AST parsing and dependency graph traversal
- **`PytestImpactStrategy`**: Extends AST analysis with pytest-specific handling—when `conftest.py` files change, all tests in the same directory and subdirectories are considered impacted
- **`CompositeImpactStrategy`**: Combines multiple strategies, deduplicates and sorts results

The default strategy in `api.py` is `CompositeImpactStrategy([ASTImpactStrategy(), PytestImpactStrategy()])`.

Dependency tree building uses an LRU cache (`_cached_build_dep_tree` in `strategies.py`, maxsize=8) with `clear_dep_tree_cache()` for invalidation (also clears `discover_submodules` cache).

### Test Structure

Tests mirror the source structure. The `tests/strategies/` subdirectory contains per-strategy tests (`test_ast_impact.py`, `test_pytest_impact.py`, `test_composite_impact.py`, `test_caching.py`, `test_integration.py`). Tests use `unittest.mock` extensively and the `pytester` pytest plugin (enabled in `conftest.py`) for testing plugin behavior. Some tests are marked `@pytest.mark.slow`.

## Documentation

The project has three documentation surfaces that must stay in sync:

- **`README.md`** — project home page (also served as MkDocs home via `mkdocs.yml`)
- **`docs/usage.md`** — detailed usage guide (MkDocs site)
- **`CLAUDE.md`** — this file (architecture reference for Claude Code)

The documentation site uses [MkDocs Material](https://squidfun.github.io/mkdocs-material/) and is published to GitHub Pages at `https://promptromp.github.io/pytest-impacted`. Configuration is in `mkdocs.yml`.

## Special Instructions

- **Keep docs in sync**: When making significant changes to the codebase (new features, API changes, architectural changes, new CLI options, strategy changes), always update **all three** of `CLAUDE.md`, `README.md`, and `docs/usage.md` to reflect those changes in the same PR.
- **README.md and docs/usage.md serve different audiences**: `README.md` is a concise overview for GitHub/PyPI visitors; `docs/usage.md` is a comprehensive reference with deeper explanations, code examples, and MkDocs-specific features (admonitions, mermaid diagrams).

## Configuration Notes

- Ruff: line-length=120, target-version=py311, double quote style, T201 (print) allowed
- Pre-commit hooks: ruff, mypy, pytest with coverage (fail_fast: true)
- CI matrix: Python 3.11, 3.12, 3.13
- Python 3.11+ minimum required
