# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pytest-impacted is a pytest plugin that selectively runs tests impacted by code changes via git introspection, AST parsing, and dependency graph analysis. The plugin analyzes Python import dependencies using astroid, builds dependency graphs with NetworkX, and uses GitPython to identify changed files.

## Core Architecture

The plugin follows a modular architecture with clear separation of concerns:

- **plugin.py**: pytest plugin entry point, handles CLI options and test collection filtering
- **api.py**: Main API functions (`get_impacted_tests`, `matches_impacted_tests`) that orchestrate the analysis
- **git.py**: Git integration for finding changed files (supports unstaged changes and branch diffs)
- **graph.py**: Dependency graph construction and analysis using NetworkX
- **parsing.py**: AST parsing using astroid to extract import relationships
- **traversal.py**: Module discovery and path/module name conversion utilities
- **cli.py**: Standalone CLI tool for generating impacted test lists
- **display.py**: Rich-based console output formatting

The workflow: Git identifies changed files → Files converted to Python modules → AST parser builds dependency graph → Graph analysis finds impacted test modules → Tests are filtered accordingly.

## Development Commands

### Testing
```bash
# Run all tests
uv run python -m pytest

# Run tests with coverage
uv run python -m pytest --cov=pytest_impacted --cov-branch tests

# Run tests excluding slow tests (used in pre-commit)
uv run python -m pytest --cov=pytest_impacted --cov-branch tests -m 'not slow'

# Run a single test file
uv run python -m pytest tests/test_api.py

# Run a specific test function
uv run python -m pytest tests/test_api.py::test_function_name
```

### Linting and Formatting
```bash
# Run ruff linting with auto-fix
ruff check --fix

# Run ruff formatting
ruff format

# Check both lint and format (CI mode)
ruff check && ruff format --check
```

### Type Checking
```bash
# Run mypy type checking
uv run mypy pytest_impacted
```

### Pre-commit Hooks
```bash
# Run all pre-commit hooks manually
pre-commit run --all-files

# Install pre-commit hooks
pre-commit install
```

## Package Management

This project uses `uv` for dependency management:

```bash
# Install development environment
uv sync --all-extras --dev

# Install package in editable mode (legacy approach)
pip install -e .
```

## Plugin Usage Examples

```bash
# Run tests impacted by unstaged changes
pytest --impacted --impacted-git-mode=unstaged --impacted-module=my_package

# Run tests impacted by branch changes vs main
pytest --impacted --impacted-git-mode=branch --impacted-base-branch=main --impacted-module=my_package

# Include external tests directory
pytest --impacted --impacted-git-mode=unstaged --impacted-module=my_package --impacted-tests-dir=tests/

# Generate impacted test list (for CI)
impacted-tests --module=my_package --git-mode=branch --base-branch=main > impacted_tests.txt
pytest @impacted_tests.txt
```

## Key Dependencies

- **astroid**: AST parsing and static analysis
- **networkx**: Dependency graph construction and analysis
- **gitpython**: Git repository introspection
- **pytest**: Test framework integration
- **click**: CLI interface for standalone tool
- **rich**: Console output formatting

## Configuration Notes

- Ruff configuration in pyproject.toml sets line length to 120 characters
- MyPy configured with namespace packages support
- Pre-commit hooks include ruff, mypy, and pytest with coverage
- CI runs on Python 3.11, 3.12, and 3.13
- Project requires Python 3.11+ minimum
