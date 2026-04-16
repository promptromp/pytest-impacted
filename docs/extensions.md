# Extensions

`pytest-impacted` is built around a strategy-based architecture (see [Impact Analysis Strategies](usage.md#impact-analysis-strategies) in the Usage Guide). Anywhere the built-in strategies aren't enough — runtime DI bindings, codegen outputs, plugin discovery, custom heuristics — you can extend the pipeline with your own strategy.

There are two ways to do that:

| Approach | When to use | How it's wired |
|---|---|---|
| **Programmatic** | One-off in-process customization, or driving impact analysis from your own tooling | Pass `strategy=` to `get_impacted_tests()` |
| **Packaged extension** | Reusable, distributable, auto-discovered alongside built-in strategies | Register via the `pytest_impacted.strategies` entry point |

Both approaches share the same `ImpactStrategy` base class and the same lifecycle hooks, dependency graph, and utility helpers documented below.

## Custom strategies (programmatic)

The simplest way to extend impact analysis is to subclass `ImpactStrategy` and pass an instance to the `get_impacted_tests()` API:

```python
from pathlib import Path
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

This is the right entry point for one-off integrations or for driving impact analysis from your own test runner. For reusable, auto-discovered strategies that ship as their own package, see the packaged extension system below.

## Packaged extensions

Third-party packages can register custom strategies as installable plugins. Once installed, they are automatically discovered and composed into the analysis pipeline alongside the built-in strategies.

An extension is a standard Python package that registers a strategy class via the `pytest_impacted.strategies` entry point group.

### Minimal extension (no configuration)

```python
# my_extension/strategy.py
from pytest_impacted import ImpactStrategy, resolve_impacted_tests

class MyStrategy(ImpactStrategy):
    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, *, dep_tree, **kwargs):
        # dep_tree is the pre-built dependency graph (nx.DiGraph), shared across strategies
        return resolve_impacted_tests(impacted_modules, dep_tree)
```

```toml
# pyproject.toml for the extension package
[project]
name = "pytest-impacted-my-extension"
dependencies = ["pytest-impacted>=0.19"]

[project.entry-points."pytest_impacted.strategies"]
my_extension = "my_extension.strategy:MyStrategy"
```

The entry point name (`my_extension`) is the user-facing identifier used for enabling/disabling the extension.

### Extension with configuration

Extensions can declare configuration options that are automatically registered as CLI flags and ini settings:

```python
from pytest_impacted import ImpactStrategy, ConfigOption

class CoverageStrategy(ImpactStrategy):
    config_options = [
        ConfigOption(name="coverage_file", help="Path to .coverage file", default=".coverage"),
        ConfigOption(name="threshold", help="Minimum coverage %% to consider", type=int, default=80),
    ]
    priority = 50  # Lower = runs earlier (default is 100)

    def __init__(self, coverage_file: str = ".coverage", threshold: int = 80):
        self.coverage_file = coverage_file
        self.threshold = threshold

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, *, dep_tree, **kwargs):
        # dep_tree is the pre-built dependency graph; use self.coverage_file and self.threshold
        ...
```

Config options are automatically namespaced to avoid collisions:

- **CLI flag**: `--impacted-ext-{extension_name}-{option_name}` (hyphens)
- **ini setting**: `impacted_ext_{extension_name}_{option_name}` (underscores)

For the example above:

```bash
pytest --impacted --impacted-ext-coverage-threshold 90
```

```toml
[tool.pytest.ini_options]
impacted_ext_coverage_threshold = "90"
impacted_ext_coverage_coverage_file = ".coverage.ci"
```

!!! tip
    The `ConfigOption` dataclass supports `str`, `bool`, `int`, and `float` types. Values from config files are automatically coerced to the declared type.

### Duck-typed extensions (zero dependency)

Extensions don't need to inherit from `ImpactStrategy`. Any class with a `find_impacted_tests` method works:

```python
# No import from pytest_impacted at all!
class MyLightweightStrategy:
    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, *, dep_tree, **kwargs):
        # dep_tree is an nx.DiGraph supplied by the pipeline
        return [...]
```

This is validated at runtime using the `StrategyProtocol` (a `typing.Protocol`).

## Using extensions

Once installed, extensions are automatically discovered and added to the strategy pipeline. No additional configuration is needed beyond installing the package.

### Disabling extensions

To disable a specific extension, use `--impacted-disable-ext` (repeatable):

```bash
pytest --impacted --impacted-disable-ext my_extension
```

Or in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
impacted_disable_ext = ["my_extension"]
```

### Viewing loaded extensions

Extensions are listed in the pytest report header:

```
pytest-impacted: ..., extensions=my_extension,coverage
```

### Extension priority

Extensions can declare a `priority` class variable to control execution order. Lower values run earlier. The default priority is `100`. Built-in strategies always run first, followed by extensions sorted by priority.

```python
class EarlyStrategy(ImpactStrategy):
    priority = 10  # Runs before other extensions
    ...

class LateStrategy(ImpactStrategy):
    priority = 200  # Runs after other extensions
    ...
```

!!! note
    Since `CompositeImpactStrategy` unions all results, execution order rarely matters for correctness. Priority is mainly useful if an extension needs to set up shared state or log information before others run.

## Dependency graph access

All strategies receive the pre-built dependency graph as a required keyword-only argument `dep_tree: nx.DiGraph`. The graph is built once by the orchestration layer and passed through `CompositeImpactStrategy` to all sub-strategies, so the expensive graph construction is shared across the pipeline.

The `resolve_impacted_tests` utility is exported from the package root for extensions that want standard graph traversal:

```python
from pytest_impacted import ImpactStrategy, resolve_impacted_tests

class MyStrategy(ImpactStrategy):
    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, *, dep_tree, **kwargs):
        # Standard traversal: find test modules that transitively depend on changed modules
        base_tests = resolve_impacted_tests(impacted_modules, dep_tree)
        # Add custom logic on top...
        return base_tests
```

The dependency graph uses inverted edge direction: edges point from imported module to its dependents (e.g. `core -> api -> test_api`). This means `nx.dfs_preorder_nodes(dep_tree, source="core")` finds all modules that transitively depend on `core`.

## Extension utilities

Beyond `resolve_impacted_tests`, two additional helpers are exported from the package root for extensions that need to do their own file or import analysis:

- **`discover_submodules(package, require_init=True)`** — walks a Python package and returns a `{module_name: file_path}` dict. Uses the same filesystem-based discovery pytest-impacted uses internally (handles src-layout, namespace packages, and LRU-caches results). Pass `require_init=False` for test directories that may not have `__init__.py` files. This is the right primitive for any extension that needs to scan the full source tree.

- **`parse_file_imports(file_path, module_name, is_package=False)`** — AST-parses a Python file and returns a `list[str]` of the modules it imports. Uses pytest-impacted's own astroid-based parser, so extensions that call it will interpret imports the same way the core does (including relative imports, star imports, and conditional imports inside `if TYPE_CHECKING` blocks). No module execution — imports are extracted from the AST without running code.

Example: a strategy that enumerates all source files and scans them for a custom pattern:

```python
from pytest_impacted import ImpactStrategy, discover_submodules, parse_file_imports

class MyScanningStrategy(ImpactStrategy):
    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, *, dep_tree, **kwargs):
        # Walk every source file in the package
        modules = discover_submodules(ns_module)
        for module_name, file_path in modules.items():
            imports = parse_file_imports(file_path, module_name)
            # ... do something with imports ...
        return []
```

!!! tip
    `discover_submodules` is LRU-cached by `(package, require_init)` so calling it multiple times within a single pytest run is cheap. The cache is cleared by `clear_dep_tree_cache()` alongside the dependency graph cache.

## Lifecycle hooks

`ImpactStrategy` exposes three optional lifecycle methods that run once per pytest invocation: `enrich_dep_tree`, `setup`, and `teardown`. They give extensions proper places to hang one-time work and — critically — to inject synthetic dependency edges that the built-in AST traversal will then follow automatically.

### `setup` and `teardown` — one-time work per run

```python
from pytest_impacted import ImpactStrategy, discover_submodules, parse_file_imports

class IndexingStrategy(ImpactStrategy):
    def setup(self, *, ns_module, tests_package=None, root_dir=None, session=None, dep_tree):
        # One-time O(source-tree) work happens here, not in find_impacted_tests
        self._index = {}
        for module_name, file_path in discover_submodules(ns_module).items():
            self._index[module_name] = parse_file_imports(file_path, module_name)

    def teardown(self):
        # Release per-run state. Fires even if find_impacted_tests raises.
        self._index = None

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, *, dep_tree, **kwargs):
        # Cheap lookups against self._index — no scanning.
        ...
        return []
```

**When they fire.** `pytest_impacted.api.get_impacted_tests` invokes `strategy.setup(...)` immediately before `strategy.find_impacted_tests(...)`, and `strategy.teardown()` in a `finally` block immediately after. Teardown always runs, even if `find_impacted_tests` raises — so strategies can allocate resources in `setup` with confidence they will be released.

**Signature.** `setup` takes only keyword arguments: `ns_module`, `tests_package`, `root_dir`, `session`, `dep_tree`. These are the same context kwargs as `find_impacted_tests` minus `changed_files` / `impacted_modules` (which are not known at setup time). Both hooks have no-op default implementations on `ImpactStrategy`, so existing strategies adopt the new lifecycle without any changes.

**Composition and ordering.** `CompositeImpactStrategy` propagates `setup` to its children in list order and `teardown` in reverse order (LIFO, matching the convention used by context managers and `ExitStack`). If any sub-strategy's `setup` or `teardown` raises, the composite logs a warning on the `pytest_impacted.strategies` logger and continues with the remaining sub-strategies — one misbehaving extension cannot prevent the others from running.

!!! tip
    If you find yourself writing a lazy-init guard at the top of `find_impacted_tests` (`if self._index is None: ...`), that's the signal to move the work into `setup`. The hook also makes timing and profiling cleaner — you can measure setup cost independently from per-call work.

### `enrich_dep_tree` — inject synthetic edges

Some dependency relationships are invisible to static import analysis: runtime DI bindings, codegen outputs, plugin discovery, config-driven wiring. The `enrich_dep_tree` hook lets an extension add those relationships as explicit edges in the shared dependency graph **before** any strategy runs its impact analysis. The built-in AST strategy then traverses those synthetic edges exactly as if they had been real imports.

Most real extensions need to look at the actual source code to decide which edges to add. `enrich_dep_tree` receives the same context kwargs as `setup` (`ns_module`, `tests_package`, `root_dir`, `session`) so you can walk the tree with [`discover_submodules`](#extension-utilities) and [`parse_file_imports`](#extension-utilities) from inside the hook — a scan-then-enrich pattern that keeps all the logic in one place.

```python
import re
from pathlib import Path

import networkx as nx
from pytest_impacted import ImpactStrategy, discover_submodules, parse_file_imports

# Finds @binding("key") decorators used by microcosm-style DI frameworks.
_BINDING_RE = re.compile(r'@binding\(["\']([^"\']+)["\']\)')


class DIBindingStrategy(ImpactStrategy):
    """Bridge runtime DI bindings into the static dependency graph.

    Walks the source tree once per run, finds ``@binding("key")``
    producers and ``graph.key`` consumers, and adds a synthetic edge
    from every producer module to every consumer module. The built-in
    AST strategy then picks up those edges automatically.
    """

    def enrich_dep_tree(
        self,
        dep_tree: nx.DiGraph,
        *,
        ns_module: str,
        tests_package: str | None = None,
        root_dir: Path | None = None,
        session=None,
    ) -> None:
        # 1. Enumerate every source file the core knows about.
        modules = dict(discover_submodules(ns_module))
        if tests_package:
            modules.update(discover_submodules(tests_package, require_init=False))

        # 2. Scan each file for producers and consumers.
        producers: dict[str, str] = {}  # binding_key -> producer module
        consumers: dict[str, set[str]] = {}  # binding_key -> {consumer modules}
        for module_name, file_path in modules.items():
            try:
                source = Path(file_path).read_text(encoding="utf-8")
            except OSError:
                continue
            for match in _BINDING_RE.finditer(source):
                producers[match.group(1)] = module_name
            for match in re.finditer(r"\bgraph\.(\w+)\b", source):
                consumers.setdefault(match.group(1), set()).add(module_name)

            # Reuse the core import parser to stay consistent with AST strategy.
            parse_file_imports(file_path, module_name)

        # 3. Add producer → consumer edges. The graph uses inverted
        #    direction, so "producer points at impacted consumer"
        #    matches how the AST strategy reads its own import edges.
        for key, producer in producers.items():
            for consumer in consumers.get(key, ()):
                if producer != consumer:
                    dep_tree.add_edge(producer, consumer)

    def find_impacted_tests(self, *args, **kwargs):
        # Often unnecessary — the AST strategy already traverses the
        # edges you added above. Return [] to contribute nothing extra.
        return []
```

**When it fires.** `enrich_dep_tree` runs once per pytest invocation, on a **per-run copy** of the LRU-cached base graph, **before** any strategy's `setup` is called. The ordering is: build cached graph → copy → `enrich_dep_tree(all strategies)` → `setup(all strategies)` → `find_impacted_tests(all strategies)` → `teardown(all strategies)`.

**Per-run copy matters.** `pytest_impacted.strategies.cached_build_dep_tree` is LRU-cached by `(ns_module, tests_package)`. Without the copy, enrichment from one run would accumulate into every subsequent run within the same process (e.g. pytester-driven test suites). The orchestrator calls `.copy()` on the cached graph before handing it to `enrich_dep_tree`, so the graph you mutate is yours for this run only.

**Propagation and ordering.** `CompositeImpactStrategy` calls `enrich_dep_tree` on its children in list order, forwarding all context kwargs unchanged. Because the graph is mutated in place, edges added by one child are immediately visible to every later child's `enrich_dep_tree` call. Exceptions are logged at WARNING on `pytest_impacted.strategies` and swallowed — the fault-tolerance contract applies here too.

!!! tip
    Prefer `enrich_dep_tree` over doing your own DFS inside `find_impacted_tests` when the relationships you're modeling can be expressed as edges. You get the built-in traversal, deduplication, and transitive closure for free, and the edges are visible to every other strategy in the pipeline — not just yours.

### Persisting state across runs

Extensions that build an expensive index — for example, scanning every `.py` file for `@binding` decorators, symbol tables, or any other codebase-wide fingerprint — can easily amortize that cost across pytest runs. pytest-impacted does not ship a built-in cache service for extensions, but there is a recommended **filesystem convention** so every extension does not need to reinvent it.

**Recommended layout.** Store per-extension state under `.pytest-impacted-cache/<extension-name>/` in the project root, next to pytest's own `.pytest_cache/`. This keeps extension state easy to discover, easy to clear (`rm -rf .pytest-impacted-cache/`), and out of the way of unrelated tooling. Extensions should add this directory to `.gitignore`.

**Expose the path as a `ConfigOption`.** Users may want to override the location — to move it onto faster storage, share it across CI workers, or point at an absolute path that survives `tmp`-style workdirs. Declare it as a config option so it is auto-registered as a CLI flag (`--impacted-ext-<name>-cache-dir`) and ini value.

```python
from pathlib import Path

from pytest_impacted import ConfigOption, ImpactStrategy


class MyExtension(ImpactStrategy):
    config_options = [
        ConfigOption(
            name="cache_dir",
            help="Directory for persisted extension state (default: .pytest-impacted-cache/my_ext)",
            type=str,
            default=".pytest-impacted-cache/my_ext",
        ),
    ]

    def __init__(self, cache_dir: str = ".pytest-impacted-cache/my_ext"):
        self._cache_dir = Path(cache_dir)
```

**Invalidate on mtime.** The simplest invalidation strategy that is also correct-by-default: hash the mtimes of every file the extension scans, compare against a stored manifest, rebuild when anything is newer. This catches code edits, git checkouts, and merges without any special integration with git.

```python
import json
from pathlib import Path


def _mtime_fingerprint(paths: list[Path]) -> str:
    """Stable hash of (path, mtime_ns) pairs for cache invalidation."""
    import hashlib

    h = hashlib.sha256()
    for p in sorted(paths):
        try:
            mtime = p.stat().st_mtime_ns
        except FileNotFoundError:
            continue
        h.update(str(p).encode())
        h.update(str(mtime).encode())
    return h.hexdigest()


def load_or_build(cache_dir: Path, scanned_files: list[Path], build_index):
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "manifest.json"
    index_path = cache_dir / "index.json"

    fingerprint = _mtime_fingerprint(scanned_files)
    if manifest_path.exists() and index_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("fingerprint") == fingerprint:
            return json.loads(index_path.read_text())

    index = build_index()
    index_path.write_text(json.dumps(index))
    manifest_path.write_text(json.dumps({"fingerprint": fingerprint}))
    return index
```

Call this from your `setup` or `enrich_dep_tree` hook so the expensive `build_index()` runs only when something on disk has actually changed.

!!! note
    This is a **convention, not an API**. pytest-impacted does not validate or manage `.pytest-impacted-cache/` — extensions own their own state. A future release may introduce an integrated `Cache` service that handles invalidation automatically; until then, the filesystem convention above is the recommended pattern and keeps extensions consistent with each other.

!!! warning
    Do not commit `.pytest-impacted-cache/` to version control. Add it to `.gitignore` in the extension's project template, and document the recommendation in your extension's README so users know what it is when they see it appear.

## Error handling

The extension system is designed to be fault-tolerant:

- **Import errors**: If an extension package fails to import, it is skipped with a warning log. Other extensions and built-in strategies continue to work.
- **Instantiation errors**: If a strategy's `__init__` raises an exception, the extension is skipped.
- **Invalid classes**: If an entry point resolves to a class without `find_impacted_tests`, it is skipped with a warning.

Extensions never prevent the core pytest-impacted functionality from working.

## Testing extensions

Extensions are just Python classes, so they can be unit-tested in isolation. Two patterns work well:

### Unit-testing `find_impacted_tests` directly

Construct a small `networkx.DiGraph` by hand and invoke the strategy's method directly. This is fast, hermetic, and doesn't require a real project layout.

```python
import networkx as nx
from my_extension.strategy import MyStrategy


def test_my_strategy_returns_impacted_tests():
    # Build a minimal dep graph: edges point from imported module to its dependents
    dep_tree = nx.DiGraph()
    dep_tree.add_edge("mypkg.core", "mypkg.api")
    dep_tree.add_edge("mypkg.api", "tests.test_api")

    strategy = MyStrategy()
    impacted = strategy.find_impacted_tests(
        changed_files=["mypkg/core.py"],
        impacted_modules=["mypkg.core"],
        ns_module="mypkg",
        dep_tree=dep_tree,
    )

    assert "tests.test_api" in impacted
```

!!! tip
    The graph's inverted edge direction (imported module → dependents) is what makes `resolve_impacted_tests(["mypkg.core"], dep_tree)` return everything that transitively depends on `mypkg.core`. A handful of `add_edge()` calls is usually enough to cover your strategy's branches.

### Integration testing with `pytester`

For end-to-end coverage — including entry-point discovery and CLI flag registration — use pytest's built-in `pytester` fixture. Patch `importlib.metadata.entry_points` to inject your strategy without having to `pip install` it.

```python
from unittest.mock import MagicMock, patch
from pytest_impacted.extensions import clear_extension_cache
from my_extension.strategy import MyStrategy


@patch("pytest_impacted.extensions.importlib.metadata.entry_points")
def test_extension_discovered_by_plugin(mock_eps, pytester):
    ep = MagicMock()
    ep.name = "my_extension"
    ep.load.return_value = MyStrategy
    mock_eps.return_value = [ep]
    clear_extension_cache()

    pytester.makepyfile(test_smoke="def test_ok(): pass")
    result = pytester.runpytest("-v", "--impacted", "--impacted-module=pytest_impacted")
    result.stdout.fnmatch_lines(["*extensions=my_extension*"])
```

See `tests/test_extensions.py` and `tests/test_extension_integration.py` in the pytest-impacted repo for the canonical patterns used by the built-in test suite.
