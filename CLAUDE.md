# CLAUDE.md

Guidance for Claude Code working in this repository. Keep it short and spend the
space on gotchas — anything discoverable by reading the code does not belong here.

## What this is

A pytest plugin that selectively runs tests impacted by code changes. Git identifies
changed files → files map to Python modules → AST parsing builds an import dependency
graph (NetworkX) → graph traversal finds impacted test modules → tests are filtered.
Separately, changes to dependency and test-config files (`uv.lock`, `requirements*.txt`,
`pytest.ini`, …) mark all tests impacted.

The philosophy is to **err on the side of caution**: false positives (running a test
that did not need to run) are preferred over false negatives. Exceptions are deliberate,
documented and opt-back-in-able — chiefly `--impacted-conftest-imports` (below); never
add a new one silently.

## Gotchas

**Modules are never imported at analysis time.** All discovery is filesystem scanning
and AST parsing. Importing a module to inspect it would execute module-level code —
monkey patching, DB connections, app factories. If you find yourself reaching for
`importlib`, you are solving the problem the wrong way — `importlib.util.find_spec`
in particular, which imports every parent package to answer.

**`parsing.py` imports node classes from `astroid.nodes`**, not `astroid` — required
since astroid v4.

**`discover_submodules(..., require_init=)` has two distinct modes.** `True` walks an
importable package — `pkgutil.iter_modules`, with a non-package prefix like `src/`
dropped from names, plus the package's own `__init__.py` under its importable name
(`pkgutil` lists children only; without it, `from pkg import X` had no edge, nor did
anything the `__init__` re-exports); `False` uses `Path.rglob` for test directories,
which frequently lack `__init__.py`, naming modules by path. Picking the wrong one
silently finds nothing.
Despite the name, `True` also walks sub-directories *without* `__init__.py`
(`_namespace_portions`): since PEP 420 they import as namespace packages, and `pkgutil`
skips them. A directory shadowed by a same-named module (`tests.py` beside `tests/`) is
walked anyway: pytest still collects from it, and skipping it hid whole test directories.
That walk must stay as forgiving as `pkgutil`: an unreadable directory has no
modules (never raise — it would be an INTERNALERROR), and a symlinked portion is followed
only while it stays inside the project and does not point back up the tree.

**One file is one graph node, under one canonical name.** `discover_project_modules` is the
only place the package walk, the tests-dir walk and the ancestor conftests are merged —
`build_dep_tree` and both `resolve_*` functions go through it. The package walk's name is canonical (for a file the
walk reaches twice through a symlinked directory, the name not through the link); every
other name the file imports under is an *alias*: `tests.x` for `app/tests/x.py`, and a name
rooted at any directory above the module's first regular package (`company.app.x`,
`src.company.app.x` for `src/company/app/x.py`) — never inside a regular package, which would
invent `types` for `pkg/ns/types.py`. Import candidates and `pytest_plugins` entries are
mapped through the aliases before edges are added; names from outside the source (`-p`)
through `dep_tree.graph["aliases"]`. Two nodes for one file would double every count and
hand each consumer the same test twice.

**src-layout is handled by splitting the path into a non-package prefix and an
importable root** (`find_non_package_prefix` in `traversal.py`). `src/my_package`
must resolve to the module name `my_package`, or AST-parsed imports will not match
discovered modules.

**`api.get_impacted_tests` copies the dependency graph before enrichment**
(`cached_build_dep_tree → .copy() → resolve_files_to_nodes → enrich_dep_tree → setup →
find_impacted_tests → teardown`). Changed files resolve through the graph's own node
`path`s, not a second discovery: a changed module the (cached) graph lacks would read as a
production module outside it, and `resolve_impacted_tests` would select every test. The
copy is load-bearing: without it, extension enrichment pollutes the LRU-cached base graph
and the next run in the same process starts dirty. `teardown`
runs in a `finally`.

**`get_impacted_tests` contains no strategy-specific dispatch.** `api.py` assembles
the pipeline (it is the composition root), but the run itself always passes
`changed_files` and `impacted_modules` (possibly empty) to the composite and lets each
strategy decide. This is why `DependencyFileImpactStrategy`, which operates on
non-Python files, needs no special-casing. Keep it that way.

**The dependency graph is built once** and passed to every strategy as a required
keyword-only `dep_tree`. Both caches live on *private* inner functions —
`_cached_build_dep_tree` (maxsize=8) and `_discover_submodules` — because the public
wrappers must canonicalize `root_dir` before the lookup. `clear_dep_tree_cache()`
clears both (via `traversal.clear_discovery_cache()`); `discover_submodules.cache_clear`
is a back-compat alias onto the inner cache.

**Every revision passed to the git CLI goes through `git.rev_args()`** — never hand a ref
straight to `repo.git.<cmd>(...)`. It validates each ref with `validate_rev` (rejecting
option-like values with a clear error) *and* prefixes `--end-of-options` so git cannot
parse an operand as an option even if the string check is ever bypassed. This is why the
project requires git >= 2.24, and why tests assert on the `--end-of-options` token.

**`GIT_AVAILABLE` is not defensive clutter.** `from git import Repo` raises
`ImportError` when the *git executable* is missing, not just when GitPython is absent —
and `plugin.py` is a `pytest11` entry point imported on every pytest run, so an
unguarded import breaks pytest itself in containers without git. `from __future__ import
annotations` at the top of `git.py` is what lets the module import with `Repo` unbound.
A subprocess test pins it. At run time a missing git raises `GitUnavailableError` rather
than returning `None`, because `None` means "nothing changed" and skips every test: the
plugin catches it and **fails open** (runs everything), the CLI exits non-zero. Never
collapse "unknown" into "no changes". The notice is printed exactly once: by
`pytest_report_header` when git was missing at import (the controller writes the header,
so it survives pytest-xdist), otherwise by the collection hook. Never via `warnings.warn`,
which `filterwarnings = error` turns into an INTERNALERROR on the fail-open path.

**Branch mode diffs from the merge base**, not the base tip: `_merge_bases` runs
`git merge-base --all` (through `rev_args`) and the result is the union of the diffs from
each fork point — criss-cross merges have several, and one alone can miss a file. Only
git's exit status 1 (no common ancestor: unrelated histories, or a shallow clone cut above
the fork) falls back to the tip, and it says so through `on_fallback`. Any other failure,
such as an unknown ref, propagates. `use_merge_base=False` (`--impacted-no-merge-base`)
always uses the tip.

**Anything the user must see goes through `display.notify` / `display.warn` with the
session.** A `logger` record is swallowed during collection, and `warnings.warn` becomes an
INTERNALERROR under `filterwarnings = error`. Under pytest-xdist even terminal output is
lost (collection runs on the workers), so only `pytest_report_header` reliably reaches the
user there.

**Every diff goes through `_name_status_diff` with fixed `--name-status -z --no-renames`.**
`-z` stops git C-quoting non-ASCII paths (`core.quotePath`), which would never match a
file on disk; `--no-renames` turns a rename into a delete plus an add, so no record
carries two paths and results do not depend on the developer's `diff.renames`. Unstaged
mode is the union of three views — `git diff --cached`, `git diff`, and
`git ls-files --others` — because a staged edit reverted on disk appears in only one of
them. Deletions count (`_NON_IMPACTFUL_STATUSES` drops only `X`/`B`), so `changed_files`
routinely names paths that no longer exist, and files outside the project root arrive as
absolute paths.

**`canonical_root()` is the single origin for every path.** Discovery, graph building and
the conftest walk all resolve against `root_dir` (the plugin passes `config.rootpath`),
never the process CWD — running pytest from a subdirectory used to resolve nothing. It
absolutizes *and* resolves symlinks, and both caches canonicalize before their lookup so a
`None` default cannot collapse two projects onto one entry. Never reach for `Path.cwd()`
in traversal, graph or strategy code — `canonical_root`'s `None` default is the only place
it may appear.

**Conftests above the analysed packages are graph nodes too.** Package discovery never
sees a root-level `conftest.py`, so `discover_project_modules` adds them via
`discover_ancestor_conftests`, named the way package discovery names modules (`conftest`,
`backend.conftest`, and `app.conftest` for `src/app/conftest.py`) so their relative imports
resolve. They are named around every name already in use, aliases included — taking a
package file's alias would re-point its imports — and a conftest with no free name gets a
leading dot (`.mysite.conftest`, which no import spells) rather than being dropped: a
dropped conftest loses every edge from it. A conftest is recognised by its file
name, `conftest.py`, on both the changed-file and the graph path. Every node carries its
source file in the `path` attribute. Resolve a node to a file with `_module_path`, never by
rebuilding a path from the dotted name — that silently fails for src-layout, where the
name drops `src/`. `is_test_module` is false for any `conftest`, even under `tests/`: it
holds fixtures, never tests.

**A collected item is selected if `item.path` *or* `item.location` is an impacted file**
(`plugin._impacted_items`). They differ for an inherited test (location: the base class's
module) and a pytest-bdd scenario (location: inside `pytest_bdd`). Matching location alone
skipped scenarios; path alone skipped inherited tests whose base changed with no graph edge
(`from checks import Checks` in a rootless dir). The location is also still matched by
path suffix, as 0.31.0 did, so the result is a strict superset of the old selection. Keep
every half — each only adds tests. Items import analysis cannot judge always run: those
from non-Python files (`--doctest-glob`, YAML collectors) and `DoctestItem`s — also when
nothing at all is impacted.

**Changed-file paths are POSIX strings.** `normalize_git_paths` emits `as_posix()` because
every matcher is `PurePosixPath`-based; an OS-native Windows path would carry backslashes
into the file name and match nothing.

**`pytest_plugins` declarations are edges, and their targets are global.** `build_dep_tree`
treats each `pytest_plugins` entry in a conftest, test module or (transitively) plugin as
an import of the declaring module — so, like every import, the returned graph has an edge
*from the plugin to its declarer* — and flags the targets with the `pytest_plugin` node
attribute. `PytestImpactStrategy` marks *every* test impacted when a change reaches a
flagged node or a `-p` / `PYTEST_PLUGINS` plugin (`_session_wide_changes`): pytest registers
plugins session-wide, not for the declaring module's directory. Editing a module that
merely *declares* plugins is deliberately not session-wide: it over-selected every edit to
a test module using `pytest_plugins = "pytester"`, and still could not see a removed
declaration. `parse_pytest_plugins` uses stdlib `ast`, outside the backend on purpose (it
cannot break Rust/Python parity), behind a text pre-filter, with warnings silenced so
`-W error` cannot turn a `SyntaxWarning` into a lost declaration — `_quiet_parse` does the
same for the astroid import parser. Do not catch `RecursionError` in `parse_file_imports`:
ruff would still return the imports, and a silent `[]` there breaks backend parity.
`read_source` and `is_conftest_module` are the shared reader and conftest-name check —
don't re-implement either.

**`parse_file_imports` returns *candidates*, not resolved modules.** `from pkg import name`
emits both `pkg` and `pkg.name`; deciding between them would mean importing `pkg`, so
`build_dep_tree` filters candidates against the discovered modules (and their aliases) instead. The apparent
over-emission is the design, not a bug.

**The Python and Rust backends must agree exactly.** `parsing.py` (astroid) and
`rust/src/lib.rs` (ruff parser) both feed `build_dep_tree`, so any divergence silently
changes which tests run under the `fast` extra. Known parity points: read with `utf-8-sig`
so a BOM is not a syntax error, and scan `match`/`case` bodies.
`test_parse_file_imports_matches_rust_backend` pins it and CI runs both backends across
3.11–3.14 — change one backend, change the other.

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
drift apart. Upgrade ruff/mypy/pytest with `uv lock --upgrade` — they have no hook `rev`
(the remaining `rev:` pins, pre-commit-hooks and uv-pre-commit, are bumped with
`pre-commit autoupdate`) — and new checks go in the pre-commit config, never as bare
workflow steps.

**The ruff version in `uv.lock` and the `ruff_python_parser` / `ruff_python_ast` git
tags in `rust/Cargo.toml` are kept at the same release.** Bump them together.

**Building the Rust crate with plain `cargo build` fails to link** — it is a pyo3
`extension-module` with no libpython to link against. Use `cargo check` to typecheck
and maturin (or `uv sync`) to build. Lint it from the repo root with
`--manifest-path rust/Cargo.toml`.

## Strategies

`strategies.py` defines `ImpactStrategy` (ABC) plus `ASTImpactStrategy`,
`PytestImpactStrategy` (a conftest that was edited or imports changed *test* code — a
fixture module, another conftest — impacts every test in its directory and below; tests
never import their conftest, so this is invisible to test-side import analysis),
`ConftestImportImpactStrategy` (the same for a conftest importing changed *application*
code — `traversal.discover_application_files`: the `--impacted-module` walk's files less
the `--impacted-tests-dir` walk's, conftests excluded; by discovery, never by path, which a
symlinked subpackage resolves elsewhere. Always in the default pipeline, but `report_only` — naming those
conftests — unless `--impacted-conftest-imports`: a root conftest importing the app turned
0.31.0's every edit into a full run, so keep it selecting only on request until narrowing
can make it selective), `DependencyFileImpactStrategy`
(patterns in `DEFAULT_DEPENDENCY_FILE_PATTERNS` / `..._GLOB_PATTERNS`, plus the config
file pytest actually loaded, `session.config.inipath`; disable with
`--no-impacted-dep-files`), `InvalidationFileImpactStrategy` (user globs from
`--impacted-invalidate-all`, marking every test impacted; only added to the pipeline when
configured, and independent of `--no-impacted-dep-files`), and `CompositeImpactStrategy`,
which unions results. `get_default_strategies()` builds the default composition.
Duck-typed strategies need only `find_impacted_tests`: the composite skips the lifecycle
hooks they lack, and `get_impacted_tests` wraps a bare one in a composite for the same
reason.

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

`pytester` is enabled in `tests/conftest.py` (`pytest_plugins = "pytester"`) for
plugin-level tests. End-to-end tests build throwaway repos with the `make_git_project`
factory fixture (it also isolates the in-process `runpytest` from your git config) and
`git_helpers.edit_file`; an autouse fixture clears the analysis caches around every test.
Plain helpers live in `tests/git_helpers.py` — never import from a conftest. In-process
projects must not name their tests dir `tests` (it would clash with this repo's package in
`sys.modules`); use `suite`.

**The suite scrubs git's repo-locating variables** (`GIT_DIR`, `GIT_INDEX_FILE`, … — read
from `git rev-parse --local-env-vars`, with a fallback copy) in `pytest_configure`, and
restores them in `pytest_unconfigure`. Git exports them to hooks, and the pre-commit hook
runs this suite mid-commit; inherited, every throwaway test repository resolves to the
real one. From a linked worktree that overwrote the index and set `core.bare=true` on the
shared `.git`. It must stay `pytest_configure`, not an autouse fixture — session-scoped
repo fixtures are set up before any function-scoped one.

The pre-commit pytest hook filters with `-m 'not slow'`, but no test currently carries
that marker and `slow` is registered nowhere (there is no `[tool.pytest.ini_options]`) —
register it before using it, or the mark warns.

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
