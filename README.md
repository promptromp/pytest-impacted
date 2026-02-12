# pytest-impacted

[![CI](https://github.com/promptromp/pytest-impacted/actions/workflows/ci.yml/badge.svg)](https://github.com/promptromp/pytest-impacted/actions/workflows/ci.yml)
[![GitHub License](https://img.shields.io/github/license/promptromp/pytest-impacted)](https://github.com/promptromp/pytest-impacted/blob/main/LICENSE)
[![PyPI - Version](https://img.shields.io/pypi/v/pytest-impacted)](https://pypi.org/project/pytest-impacted/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pytest-impacted)](https://pypi.org/project/pytest-impacted/)

**Run only the tests that matter.** A pytest plugin that uses git diff, AST parsing, and dependency graph analysis to selectively run tests impacted by your code changes.

```bash
pytest --impacted --impacted-module=my_package     # unstaged changes
pytest --impacted --impacted-module=my_package \
       --impacted-git-mode=branch \
       --impacted-base-branch=main                 # branch changes vs main
```

---

### Key Features

| | Feature | Details |
|---|---|---|
| :zap: | **Fast feedback** | Only runs tests affected by your changes — skip the rest |
| :deciduous_tree: | **Dependency-aware** | Follows import chains transitively, not just direct file changes |
| :gear: | **No imports at analysis time** | Filesystem discovery + AST parsing — no module-level side effects |
| :test_tube: | **pytest-native** | Works as a standard pytest plugin with familiar CLI options |
| :wrench: | **conftest.py aware** | Changes to `conftest.py` automatically impact all tests in scope |
| :building_construction: | **CI-friendly** | Standalone `impacted-tests` CLI for two-stage CI pipelines |
| :shield: | **Helpful errors** | Validates config early with clear messages and suggestions |

> [!CAUTION]
> This project is currently in beta. Please report bugs via the [Issues](https://github.com/promptromp/pytest-impacted/issues) tab.

---

## Installation

```bash
pip install pytest-impacted
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add pytest-impacted
```

Requires **Python 3.11+**.

---

## Quick Start

**1. Run tests impacted by uncommitted changes:**

```bash
pytest --impacted --impacted-module=my_package
```

**2. Run tests impacted by branch changes (vs `main`):**

```bash
pytest --impacted \
       --impacted-module=my_package \
       --impacted-git-mode=branch \
       --impacted-base-branch=main
```

**3. Include tests outside the package directory:**

```bash
pytest --impacted \
       --impacted-module=my_package \
       --impacted-tests-dir=tests
```

That's it. Unaffected tests are automatically skipped.

---

## How It Works

```
Git diff → Changed files → Module resolution → AST import parsing → Dependency graph → Impacted tests
```

1. **Git introspection** identifies which files changed (unstaged edits or branch diff)
2. **Filesystem discovery** maps file paths to Python module names — without importing anything
3. **AST parsing** (via [astroid](https://pylint.pycqa.org/projects/astroid/en/latest/)) extracts import relationships from source files
4. **Dependency graph** (via [NetworkX](https://networkx.org/)) traces transitive dependencies from changed modules to test modules
5. **Test filtering** skips tests whose modules are not in the impact set

The philosophy is to **err on the side of caution**: we favor false positives (running a test that didn't need to run) over false negatives (missing a test that should have run).

### Strategy-Based Architecture

Impact analysis is pluggable via a strategy pattern. The default pipeline combines two strategies:

| Strategy | What it does |
|----------|-------------|
| **ASTImpactStrategy** | Traces transitive import dependencies through the dependency graph |
| **PytestImpactStrategy** | Extends AST analysis with pytest-specific knowledge — when a `conftest.py` file changes, **all tests in its directory and subdirectories** are marked as impacted |

Both strategies are combined via `CompositeImpactStrategy`, which deduplicates and merges their results. This is important because `conftest.py` files are implicitly loaded by pytest at runtime and are not visible through normal import analysis.

You can also supply a custom strategy via the `get_impacted_tests()` API:

```python
from pytest_impacted.api import get_impacted_tests
from pytest_impacted.strategies import ImpactStrategy

class MyCustomStrategy(ImpactStrategy):
    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        # your logic here
        ...

impacted = get_impacted_tests(
    impacted_git_mode="branch",
    impacted_base_branch="main",
    root_dir=Path("."),
    ns_module="my_package",
    strategy=MyCustomStrategy(),
)
```

---

## Usage

### Git Modes

| Mode | Flag | What it compares |
|------|------|-----------------|
| **unstaged** (default) | `--impacted-git-mode=unstaged` | Working directory changes + untracked files |
| **branch** | `--impacted-git-mode=branch` | All commits on current branch vs base branch |

The `--impacted-base-branch` flag accepts any valid git ref, including expressions like `HEAD~4`.

### External Tests Directory

When your tests live outside the namespace package (a common layout), use `--impacted-tests-dir` so the dependency graph includes them:

```bash
pytest --impacted \
       --impacted-module=my_package \
       --impacted-tests-dir=tests
```

### CI Integration

For CI pipelines where git access and test execution happen in separate stages, use the `impacted-tests` CLI to generate the test file list:

```bash
# Stage 1: identify impacted tests
impacted-tests --module=my_package --git-mode=branch --base-branch=main > impacted_tests.txt

# Stage 2: run only those tests
pytest $(cat impacted_tests.txt)
```

### Configuration via `pyproject.toml`

All CLI options can be set as defaults in your `pyproject.toml` (or `pytest.ini`):

```toml
[tool.pytest.ini_options]
impacted = true
impacted_module = "my_package"
impacted_git_mode = "branch"
impacted_base_branch = "main"
impacted_tests_dir = "tests"
```

CLI flags override these defaults.

### All Options

| Option | Default | Description |
|--------|---------|-------------|
| `--impacted` | `false` | Enable the plugin |
| `--impacted-module` | *(required)* | Top-level Python package to analyze |
| `--impacted-git-mode` | `unstaged` | Git comparison mode: `unstaged` or `branch` |
| `--impacted-base-branch` | *(required for branch mode)* | Base branch/ref for branch-mode comparison |
| `--impacted-tests-dir` | `None` | Directory containing tests outside the package |

---

## Alternatives

| Project | Notes |
|---------|-------|
| [pytest-testmon](https://testmon.org/) | Most popular option. Uses coverage-based granular change tracking. More precise but heavier; may conflict with other plugins. |
| [pytest-picked](https://github.com/anapaulagomes/pytest-picked) | Runs tests from directly modified files only — no transitive dependency analysis. |
| [pytest-affected](https://pypi.org/project/pytest-affected/0.1.6/) | Appears unmaintained, no source repository. |

---

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Setup
uv sync --all-extras --dev

# Run tests
uv run python -m pytest

# Run tests with coverage
uv run python -m pytest --cov=pytest_impacted --cov-branch tests

# Lint + format + type check
pre-commit run --all-files
```

---

## License

[MIT](LICENSE)
