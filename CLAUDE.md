# CLAUDE.md

Guidance for Claude Code working in this repository. Keep it short and spend the
space on gotchas — anything discoverable by reading the code does not belong here.

## What this is

A pytest plugin that selectively runs tests impacted by code changes. Git identifies
changed files → files map to Python modules → AST parsing builds an import dependency
graph (NetworkX) → graph traversal finds impacted test modules → tests are filtered.
Separately, changes to dependency files (`uv.lock`, `requirements.txt`, …) mark all
tests impacted.

The philosophy is to **err on the side of caution**: false positives (running a test
that did not need to run) are always preferred over false negatives.

## Gotchas

**Modules are never imported at analysis time.** All discovery is filesystem scanning
and AST parsing. Importing a module to inspect it would execute module-level code —
monkey patching, DB connections, app factories. If you find yourself reaching for
`importlib`, you are solving the problem the wrong way.

**`parsing.py` imports node classes from `astroid.nodes`**, not `astroid` — required
since astroid v4.

**`discover_submodules(..., require_init=)` has two distinct modes.** `True` uses
`pkgutil.iter_modules` for real packages; `False` uses `Path.rglob` for test
directories, which frequently lack `__init__.py`. Picking the wrong one silently
finds nothing.

**src-layout is handled by splitting the path into a non-package prefix and an
importable root** (`find_non_package_prefix` in `traversal.py`). `src/my_package`
must resolve to the module name `my_package`, or AST-parsed imports will not match
discovered modules.

**`api.get_impacted_tests` copies the dependency graph before enrichment**
(`cached_build_dep_tree → .copy() → enrich_dep_tree → setup → find_impacted_tests →
teardown`). The copy is load-bearing: without it, extension enrichment pollutes the
LRU-cached base graph and the next run in the same process starts dirty. `teardown`
runs in a `finally`.

**`api.py` contains no strategy-specific logic.** It always passes `changed_files`
and `impacted_modules` (possibly empty) to the composite and lets each strategy
decide. This is why `DependencyFileImpactStrategy`, which operates on non-Python
files, needs no special-casing. Keep it that way.

**The dependency graph is built once** and passed to every strategy as a required
keyword-only `dep_tree`. Caches: `cached_build_dep_tree` (maxsize=8) and
`discover_submodules`; `clear_dep_tree_cache()` clears both.

**Every revision passed to the git CLI goes through `git.rev_args()`** — never hand a ref
straight to `repo.git.<cmd>(...)`. It validates each ref with `validate_rev` (rejecting
option-like values with a clear error) *and* prefixes `--end-of-options` so git cannot parse
an operand as an option even if the string check is ever bypassed. This is why the project
requires git >= 2.24, and why tests assert on the `--end-of-options` token in the argv.

**Logging: every module that logs declares `logger = logging.getLogger(__name__)`.**
Never call `logging.info(...)` and friends — those hit the root logger and are
flagged by LOG015.

**Ruff rule selection uses `extend-select`, not `select`.** Ruff 0.16 ships ~394
default rules; `extend-select` layers our categories on top. "Simplifying" it back to
`select` would silently disable every default rule not named in the list. Ruff also
formats Python code blocks inside Markdown, so `ruff format` covers `README.md` and
`docs/*.md`.

**Tooling versions come from `uv.lock` only.** Ruff/mypy/pytest run in pre-commit as
`local`/`system` hooks invoking `uv run …`, and CI's lint job runs
`uv run pre-commit run --all-files` with `SKIP=pytest`. So `.pre-commit-config.yaml`
is the single source of truth for what is enforced, and pre-commit and CI cannot
drift apart. Upgrade with `uv lock --upgrade` — there is no hook `rev` to bump, and
new checks go in the pre-commit config, never as bare workflow steps.

**The ruff version in `uv.lock` and the `ruff_python_parser` / `ruff_python_ast` git
tags in `rust/Cargo.toml` are kept at the same release.** Bump them together.

**Building the Rust crate with plain `cargo build` fails to link** — it is a pyo3
`extension-module` with no libpython to link against. Use `cargo check` to typecheck
and maturin (or `uv sync`) to build. Lint it from the repo root with
`--manifest-path rust/Cargo.toml`.

## Strategies

`strategies.py` defines `ImpactStrategy` (ABC) plus `ASTImpactStrategy`,
`PytestImpactStrategy` (a changed `conftest.py` impacts every test in its directory
and below — invisible to static import analysis), `DependencyFileImpactStrategy`
(patterns in `DEFAULT_DEPENDENCY_FILE_PATTERNS` / `..._GLOB_PATTERNS`; disable with
`--no-impacted-dep-files`), `InvalidationFileImpactStrategy` (user globs from
`--impacted-invalidate-all`, marking every test impacted; only added to the pipeline when
configured, and independent of `--no-impacted-dep-files`), and `CompositeImpactStrategy`,
which unions results. `get_default_strategies()` builds the default composition.

**All file globs go through `matches_any_glob`** (`PurePosixPath.match`, right-anchored,
`*` never spans `/`, and `**` is *not* recursive — it behaves like a single `*`), and the
"tests in this directory and below" conftest rule lives in `find_test_modules_under`. Do
not add a second matcher or a second directory walk.

Third-party strategies are discovered via the `pytest_impacted.strategies` entry
point group and composed in by `api.build_strategy_with_extensions()` — the
composition root, and the only module that knows about both built-ins and
extensions. `extensions.py` therefore imports nothing from `strategies.py`.
Built-ins run first, then extensions by `priority` — ordering rarely matters since
results are unioned.

**The extension API is documented in full in `docs/extensions.md`** — lifecycle
hooks, config options, the cache convention, error handling, testing patterns. Read
that rather than re-deriving it, and put new extension-author content there.

## Testing

`pytester` is enabled in `conftest.py` for plugin-level tests. Some tests are marked
`@pytest.mark.slow` and are excluded by the pre-commit run but not by plain `pytest`.

## Documentation

Four surfaces, different audiences — keep them in sync in the same PR as the change:

- `README.md` — concise overview for GitHub/PyPI (also the MkDocs home page)
- `docs/usage.md` — reference for people *running* pytest-impacted
- `docs/extensions.md` — reference for people *building strategies*; keep
  extension-author content here and do not let it leak back into `usage.md`
- `CLAUDE.md` — this file

## Commands

```bash
uv sync --all-extras --dev

# Matches what pre-commit runs (excludes slow tests)
uv run python -m pytest --cov=pytest_impacted --cov-branch tests -m 'not slow'

# Everything CI enforces
uv run pre-commit run --all-files

cargo fmt --manifest-path rust/Cargo.toml --check
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings

# Build the Rust extension from source
pip install maturin && cd rust && maturin develop --release
```

Python 3.11+; CI matrix covers 3.11–3.14 against both the pure-Python and `fast`
(Rust) backends.
