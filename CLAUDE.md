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
add a new one silently. `--impacted-ignore-type-checking-imports` (off by default) is one rule, `type_only.py`
(mirrored in `rust/src/type_only.rs`): an import under `if TYPE_CHECKING:` is left out only when the module uses
every name it binds, and only in annotations or another such block; a name used at runtime, in a string the code
reads, or never used keeps it. All test code keeps it too (`def test_x(user: "User")`, `user` from a conftest,
depends on `User`'s module through that annotation alone), and so does every package `__init__.py` (it may provide
`X` to `from pkg import X` by a computed name while using `X` only in a hint: a lazy `__init__`, covered by the
file's role, naming no idiom; the cycles it keeps measured nil on two large typed libraries).
`graph._narrowed_files` is what the option narrows: `discover_application_files` less conftests, test modules
judged by path (catching tests inside the package), `__init__.py` files, and files outside the rootdir (a symlinked
sub-package's tests no longer read as tests by path). What the option keeps behaves exactly as without it,
placement included. Documented trade-offs: a loader declaring its names in another module; and a helper outside
the analysed directories that an `__init__` imports for a hint is application code, so a conftest importing it is
under the opt-in again (chosen over a reach-only `type_only` edge flag). Keep it a rule about how
names are used, never a list of idioms: skipping every type-only import lost tests through a lazy `__init__`
(exports under `TYPE_CHECKING`, loaded by `__getattr__`), and lists of lazy-loading signs kept missing spellings. A
false sighting of an annotation use is the unsafe side (it licenses the drop); a false runtime use only keeps an
edge. Prove a change by checking each dropped (module, test) pair against what the test file loads at collection —
"every removal traces to a type-only import" passed while tests were lost. The option removes type-only edges and
nothing else: assumed roots still activate on type-only imports (`parse_all_imports_split` returns both lists from
one parse). `TYPE_CHECKING` is matched by name, as type checkers do, or as the attribute of a module-level
`import`.

## Gotchas

**Modules are never imported at analysis time.** Discovery is filesystem scanning and AST parsing; importing runs
module-level code (monkey patching, DB connections, app factories). Reaching for `importlib` is the wrong way —
`importlib.util.find_spec` imports every parent package. The one exception is `importlib.util.resolve_name`
(strings only), with which `dynamic_imports` resolves a relative `import_module` name.

**The graph's keys are `graph_keys.py`'s** (`Node`, `Edge`, `Graph`): each node, edge and graph attribute is declared
there with what it holds; never a new string key outside it (`test_graph_keys` fails on an undeclared one).
`REACH_ONLY` maps each edge flag that only reaches to the graph flag set when an edge carries it; `only_reaches` and
`may_hold_reach_only_edges` read it. Plain `str` constants: the graph stores what it always did (no enum objects in
a pickled or dumped graph).

**The pure-Python backend is the standard library's `ast`**, not astroid (6–7x slower; its pin blocked pylint <
4.1). Every walk over statements, the type-only walk included, is iterative (`parsing._import_statements`, as
`collect_imports_from_stmts`): only the parse can give up (near 3,000 nested terms on 3.11, 10,000 on 3.12–3.13),
and then the file imports nothing, logged. Never a recursive visitor: `ast` nests each `elif` in the previous
`orelse`, so a generated 1,000-branch dispatch is 1,000 statements deep.

**Parsing goes through `backends.py`; `graph.py` names no parser.** A `Backend` answers per `Source`, in order, `None`
for a file it leaves to another (`imports`, `imports_by_name`): `PythonBackend` reads every file, `RustBackend` leaves
out what it cannot read or an older extension cannot do, and `WithFallback` is the "extension, then Python for what
it leaves" rule — never fewer imports than Python. `backend_for` is the one place a run's backend is chosen (the
extension with Python behind it; Python alone when the extension is absent, too old for the options, or
`--impacted-backend=python`), and part of the graph cache's key; a backend of the caller's gets Python behind it
(`resolve_backend`). The report header, written on every run, decides from `_rust`'s flags without loading the
layer (lazy imports, above); `test_the_header_names_the_backend_the_analysis_chooses` holds it to `backend_for`. Add
a parser as a backend, never as a branch in `graph.py`.

**`discover_submodules(..., require_init=)` has two distinct modes.** `True` walks an importable package:
`pkgutil.iter_modules`, a non-package prefix like `src/` dropped, plus the package's own `__init__.py` (`pkgutil`
lists children only; without it `from pkg import X` had no edge). `False` walks test directories, naming modules
by path (`os.walk`, following symlinked directories as pytest does, under the package walk's guards; a file reached
twice is one node, named not through the link). The wrong mode silently names modules differently (`app.tests.x`
vs `tests.x`) or skips non-identifier directories (`my-dir/`). `True` also walks directories *without*
`__init__.py` (`_namespace_portions`: PEP 420, which `pkgutil` skips) and ones shadowed by a same-named module
(`tests.py` beside `tests/`: pytest still collects there). It must stay as forgiving as `pkgutil`: an unreadable
directory has no modules (never raise: an INTERNALERROR), and a symlinked portion is followed only while inside the
project and not pointing back up the tree. Use `os.path.isfile`/`exists`, never `Path.exists()`/`is_file()` (on
3.11–3.13 they raise `PermissionError` in a listable but unsearchable directory); `traversal.real_path`, never
`Path.resolve()` (it raises on a symlink loop before 3.13, and git reports an untracked loop like any file);
`parsing.is_package_file`, never `endswith("__init__.py")` (`test__init__.py`).

**One file is one graph node, under one canonical name.** `_discover_project` (behind `discover_project_modules`)
is the only place the package walk, tests-dir walk and ancestor conftests merge; `build_dep_tree`,
`resolve_files_to_modules` and `resolve_modules_to_files` use it. The package walk's name is canonical; any other
name a file imports under is an *alias* (`tests.x` for `app/tests/x.py`; `company.app.x`, `src.company.app.x`,
rooted above the first regular package) — never one inside a regular package (it would invent `types` for
`pkg/ns/types.py`), nor through a regular package above the analysed directory (`src/__init__.py`): an alias picks
one file and hides a same-named one under another root, so that name is left to the on-disk lookup, which links
every file it may mean. Import candidates and `pytest_plugins` entries map through aliases before edges are added;
outside names (`-p`) through `graph.nodes_named`. Two nodes for one file double every count and hand consumers
each test twice.

**Modules no walk finds join the graph by import, and changed ones per run.** A candidate matching no module,
alias or contested name is looked up on disk (`os.path` only) by `graph._Linker` under `import_roots`, the roots
the project's names *imply*: the rootdir; every directory the walks' names are rooted at, even a regular package
(dropping one loses imports the walk assumes, keeping one only over-selects); each analysed dir's non-package
prefix; and the non-package directories in between. Standard-library names are looked up there too (a local
`profile/` shadows it), installed distributions (`pip install -t .`) not, and every part must match on-disk case
(a case-insensitive filesystem finds `schemas/election.py` for `from app.schemas import Election`, a class).

Every other directory above an analysed directory (none inside the analysed package) is an *assumed* root
(`split_import_roots`: `src/` with an `__init__.py` is on `sys.path` wherever code says `import app`), resolved
(`src -> real_src`) like the files under it. There a name spelling its way to an analysed directory is always
looked up; any other only once a walked module's import spells an analysed module from there (`_Linker.activate`:
evidence it is on `sys.path`, so `from celery import …` misses `mysite/celery.py`), and never a standard-library
name (`_Assumed.looks_up`: it would shadow the stdlib for everything). A file found only there is named from an
implied root (`backend.redis`, not `redis`) so its relative imports resolve; that name is still looked up, as it may
mean another file. Matches may be coincidence, so these edges are flagged `assumed_root` and, like `runs_init`,
only reach: `_changes_by_role` ignores them (when `graph["runs_init"]` or `graph["assumed_roots"]` is set), else a
test helper reached would become application code and its conftest rule opt-in — fewer tests. Inside the analysed
package too: an app module importing a test helper as `app.tests.h` must not lead placement through it, and a
deleted `app.gone` is test code, so a conftest reaching it selects its directory.

pytest's `pythonpath` entries inside or above the rootdir (`traversal.pythonpath_roots`, resolved once per build:
an iterator reads empty the second time) are surely on `sys.path`: *implied* roots, placing as the rootdir does.
One that is also an assumed root (`pythonpath = src`, `src/__init__.py` above `src/app`) stays assumed and
reach-only (implied, it made `src/shared.py` application code: fewer tests), but `certain` (`_Assumed.certain`):
every name, stdlib too, is looked up there. Never model entries as assumed roots with every module a gateway: they
go stale and bypass the opt-in. Entries are spelled as given, like the rootdir and walks: fix case for every path or
none (respelling only them broke a rootdir or `--impacted-module` typed in another case).

pytest's `prepend` mode puts each test module's or conftest's *basedir* (the first directory upward without an
`__init__.py`, or not an identifier; `traversal.rootless_test_dirs`) on `sys.path` for good, and `sys.modules`
keeps a name's first module. So any bare import may mean that name beneath *any* basedir: `_Linker.local_targets`
links all via an index (one lookup per name), plus a file no walk names beneath a basedir above the importer. Only
a root conftest's `pytest_plugins` entries see just the basedirs above it (imported before any other is on
`sys.path`; else every service's `fixtures.py` becomes a plugin); its imports see all, since one inside a fixture
runs at test time. These edges are flagged `basedir` and only reach (`graph["basedir_edges"]`): placing, a
coincidental `import helpers` in app code made a test helper application code. Never make basedirs import roots
(every unresolved import is then looked up under each), nor use only the importer's directory (it misses a helper
beside a conftest above, the basedir above a tests package, and another service's helper).

A file found on any root becomes an `external` node, parsed and followed (`_parse_project` runs to a fixpoint,
`pytest_plugins` included). Every candidate the *walks* do not define goes into `graph["unresolved"]`
(`_may_name_a_module`), found on disk or not: a hit under one root must not hide a deleted same-named file under
another. `link_changed_files` gives each changed `.py` in the rootdir that the run's graph lacks (deleted or never
walked) a node on the run's copy, linked to those importers (a deleted one only if imported, or for an
`__init__.py`, a missing name inside its package). So never judge graph membership by `discover_project_modules`.
With the graph at hand, use `is_test_node`, not `is_test_module`: an external node's `test` attribute comes from
its path from the rootdir (false when deleted). An `external` node is application code when application code
depends on it (`_changes_by_role`), keeping the `--impacted-conftest-imports` opt-in over a shared library reached
through the app; otherwise it is test code, followed.

**A changed `__init__.py` changes every module in its package** (`graph.package_members`, added to
`impacted_modules` by `api.py` *after* enrichment, so extension nodes count): importing `app.core.x` runs
`app/__init__.py` and `app/core/__init__.py` but names neither. A rule on the *changed file*, never
package-to-module edges, which would make every importer of `app.*` depend on everything `app/__init__.py` imports
(near-full runs). Members are found by path and by name (a symlinked-in or extension-generated module lives
elsewhere), deleted and added `__init__.py` files included. Only string nodes with a `path`: extensions may add any
hashable node, and read every impacted module's `path`.
A *missing* module's importer (`try: import app.core.fast`) must be *reached*, never changed (a changed conftest
selects its whole directory, bypassing `--impacted-conftest-imports`), and not through a graph edge from the
package (any edit `app/__init__.py` imports would then reach it: a facade). So `link_changed_files` links only the
*changed* `__init__.py` (node or not) to importers of missing names in its package, on the run's copy, by edges
flagged `runs_init` (`graph["runs_init"]` set) that `_changes_by_role` ignores: reach-only, never placing a package
as application code (fewer tests). `package_members` counts an `__init__.py` above the rootdir only through an
unbroken `__init__.py` chain down to it (prepend mode imports tests through it), making every node under the
rootdir a member; `link_changed_files` stops at the rootdir. A root `__init__.py` edit is thousands of changed
modules, so every traversal from `impacted_modules` must be one multi-source walk (`graph.reached_from`), never one per
module.

**src-layout is handled by splitting the path into a non-package prefix and an
importable root** (`find_non_package_prefix` in `traversal.py`). `src/my_package`
must resolve to the module name `my_package`, or AST-parsed imports will not match
discovered modules.

**`api.get_impacted_tests` copies the dependency graph before enrichment** (`cached_build_dep_tree → run_copy →
link_changed_files → resolve_files_to_nodes → enrich_dep_tree → package_members → setup → find_impacted_tests →
teardown`): otherwise extension enrichment pollutes the LRU-cached base graph for the next run in the
process. `run_copy`, not `DiGraph.copy()`, which shares graph-level values such as `graph["aliases"]`; `teardown`
runs in a `finally`. Changed files and impacted test modules resolve through the graph's node `path`s, not a second
discovery: a changed module the cached graph lacks would read as production code outside it, and
`resolve_impacted_tests` would select every test.

**`get_impacted_tests` contains no strategy-specific dispatch.** `api.py` is the composition root, but the run
always passes `changed_files` and `impacted_modules` (possibly empty) to the composite and lets each strategy
decide — which is why `DependencyFileImpactStrategy` (non-Python files) needs no special-casing. Keep it that way.

**The dependency graph is built once** and passed to every strategy as a required keyword-only `dep_tree`. Both
caches sit on *private* inner functions (`_cached_build_dep_tree`, maxsize=8; `_discover_submodules`) because the
public wrappers canonicalize `root_dir` first. `clear_dep_tree_cache()` clears both (via
`traversal.clear_discovery_cache()`); `discover_submodules.cache_clear` is a back-compat alias. Every
`build_dep_tree` drops cached directory listings (`forget_listings`), so a long-lived process sees new files.

**Every revision passed to the git CLI goes through `git.rev_args()`** — never hand a ref straight to
`repo.git.<cmd>(...)`. It validates each ref (`validate_rev`, rejecting option-like values) *and* prefixes
`--end-of-options`, so git cannot read an operand as an option even if the check is bypassed. Hence git >= 2.24,
and tests asserting on the `--end-of-options` token.

**A run without `--impacted` loads none of the analysis.** `plugin.py` (a `pytest11` entry point, imported by
every pytest run) reaches `api`, `git`, `file_map`, `strategies` and `traversal` only through `plugin._lazily` —
functions, never module-like objects, since pytest probes every attribute of a plugin module for fixtures — and
`pytest_impacted/__init__.py` exports lazily (PEP 562). Eager, a one-test run took 249 ms against 127 ms (GitPython
runs `git version` as it loads). `test_a_run_without_impacted_loads_none_of_the_analysis` pins a whole session.
The first load is in a hook, under the user's warning filters, so `_lazily` silences warnings. `GitMode` lives in
`modes.py` for the options; patch `pytest_impacted.git.GIT_AVAILABLE`, not the plugin's. An installed strategy
extension still loads `strategies.py` (it subclasses `ImpactStrategy`) when `pytest_addoption` loads it.

**`GIT_AVAILABLE` is not defensive clutter.** `from git import Repo` raises `ImportError` when the *git
executable* is missing — unguarded, every `--impacted` run and API user fails in containers without git;
`from __future__ import annotations` lets `git.py` import with `Repo` unbound (a subprocess test pins it). A missing git
at run time raises `GitUnavailableError`, never returns `None` ("nothing changed" skips every test): the plugin
**fails open** (runs everything), the CLI exits non-zero. Never collapse "unknown" into "no changes". The notice
prints exactly once: if git was missing at import, in the report header (the controller's, so xdist keeps it; the
terminal summary under `-q`/`--no-header`, both from `_header_warnings`), else by the analysis — never via
`warnings.warn` (see `display.notify` below), which would break the fail-open path. git
that runs and refuses (no repository, `dubious ownership`, an unknown revision) is `GitError`, git's words in one
line: a usage error, never failed open. `validate_config` checks first (`check_repository`), since otherwise git
only says "unknown option `cached`".

**Branch mode diffs from the merge base**, not the base tip: `_merge_bases` runs `git merge-base --all` (through
`rev_args`) and unions the diffs from every fork point (criss-cross merges have several; one can miss a file). Only
exit status 1 (no common ancestor: unrelated histories, or a shallow clone cut above the fork) falls back to the
tip, via `on_fallback`; any other failure propagates. `use_merge_base=False` (`--impacted-no-merge-base`) always
uses the tip.

**Anything the user must see goes through `display.notify` / `display.warn` with the session.** A `logger` record
is swallowed during collection, and `warnings.warn` becomes an INTERNALERROR under `filterwarnings = error`. A
pytest-xdist worker's terminal output is lost, so write from the controller (`pytest_report_header`,
`pytest_terminal_summary`).

**Every diff goes through `_name_status_diff` with fixed `--name-status -z --no-renames`.** `-z` stops C-quoting
of non-ASCII paths (`core.quotePath`), which match no file; `--no-renames` splits a rename into delete plus add, so
no record has two paths and `diff.renames` cannot matter. A `--` ends the revisions (`--end-of-options` does not
stop git guessing whether `docs` is a branch or a path); branch mode diffs to `HEAD`, never the branch name, which a
tag may share. Unstaged mode unions `git diff --cached`, `git diff` and `git ls-files --others`: a staged edit
reverted on disk shows in only one. Deletions count (`_NON_IMPACTFUL_STATUSES` drops only `X`/`B`), so
`changed_files` names missing paths, and files outside the root arrive absolute. A changed submodule (a recorded,
checked-out gitlink) is followed by the files changed inside it (`git._with_submodule_changes`); this never fails
the run nor yields less than the plain paths: anything unexpected is a notice (`on_fallback`) plus the plain paths,
and no old commit to diff from means every tracked file. Only changed directories are asked about (`_gitlinks`,
chunked; none without a `.gitmodules`) — never one git call per path, a whole-tree listing, or deleted paths as
pathspecs (23 s, "argument list too long"). Its commands drop a hook's `GIT_INDEX_FILE` (`_own_git_environment`)
and respect a submodule's `ignore` setting (`--ignore-submodules=none` fails on a broken submodule).

**`canonical_root()` is the single origin for every path.** Discovery, graph building and the conftest walk
resolve against `root_dir` (the plugin passes `config.rootpath`), never the CWD (from a subdirectory nothing
resolves). It absolutizes *and* resolves symlinks; both caches canonicalize before lookup, so a `None` default
cannot collapse two projects onto one entry. Never `Path.cwd()` in traversal, graph or strategy code —
`canonical_root`'s `None` default is its only place.

**Conftests above the analysed packages are graph nodes too.** Package discovery misses a root-level
`conftest.py`, so `_discover_project` adds them (`_discover_ancestor_conftests`; the public
`discover_ancestor_conftests` drops aliases and contested names and skips nothing), named as package discovery
would (`conftest`, `backend.conftest`, `app.conftest` for `src/app/conftest.py`) so relative imports resolve, and
around every name in use, aliases included (taking an alias re-points its imports). A contested name (`y.conftest`
for `y/conftest.py` and `x/y/conftest.py`) names one node, but an import of it is an edge to every ancestor
conftest that wanted it (`_Discovered.contested`): the file meant depends on `sys.path` and import mode, and any
single-winner rule loses tests in some layout. Only ancestor conftests contest names (tying every service's
`tests/conftest.py` together would select them all). One with no free name gets a leading dot (`.mysite.conftest`,
which no import spells) rather than being dropped with all its edges, and is parsed under `import_base(name)`, so
relative imports resolve. Conftests are recognised by file name, `conftest.py`, on both the changed-file and graph
paths. A node's source file is its `path` attribute: resolve with `_module_path`, never by rebuilding a path from
the dotted name (src-layout names drop `src/`). `is_test_module` is false for any `conftest`, even under `tests/`.

**A collected item is selected if `item.path` *or* `item.location` is an impacted file**
(`plugin._impacted_items`), location also matched by path suffix. They differ for inherited tests (location: the
base's module; path alone misses a base changed with no graph edge) and pytest-bdd scenarios (location inside
`pytest_bdd`; location alone misses them). Keep every half — each only adds tests. Items analysis cannot judge
always run, even when nothing is impacted: from non-Python files (`--doctest-glob`, YAML collectors),
`DoctestItem`s, Python test files outside both analysed directories (no node, as nothing imports a test), and
tests in a file the walks do not name as a test module (a `python_files` pattern, a non-identifier directory):
`plugin._unjudged_items` judges an item only when its file is one of `_known_tests` (discovery, no parsing), which
the controller hands pytest-xdist workers with the impacted files. The two Python kinds are each counted in the summary
(via `workeroutput`).

**Unselected items are deselected, unless `--impacted-unimpacted=skip`.** A run emptied by this plugin's
deselection turns pytest's exit 5 into 0 in `pytest_sessionfinish` (a docs-only commit must pass, as must a shard
handed only deselected tests); one emptied by `-k` or with nothing collected keeps 5. Hence two hooks: marking at
the default position, before `-k`/`-m` (`-m impacted` needs the marker), and deselection in a second, `trylast`
`pytest_collection_modifyitems` (`pytest_deselect_unimpacted`, via `specname`: pytest registers only
`pytest_`-prefixed names) — after `-k`, or a mistyped `-k` on a docs-only commit passes. It deselects only items
the first judged; one another plugin added in between runs. The exit fix-up is `tryfirst`, so most later plugins reading
`session.exitstatus` see the final status and may still set a failure (the hooks' `exitstatus` argument stays 5).

**Under pytest-xdist the controller analyses, once, and the workers only filter.** Separate analyses could
disagree (git sees a working tree that may change in between), and xdist aborts when workers collect different
tests. So `pytest_configure_node` runs `_analyse` on the controller before any worker starts and hands the impacted
files (rootdir-relative) over in `workerinput` — only if every worker would compute the same: built-in strategies
only (an extension may read worker-only state: `session.items`, `workerinput`), plain `popen` workers only (`--tx`
may start another interpreter, which parses differently), and a successful analysis (raising in
`pytest_configure_node` skips every `pytest_sessionfinish`: no JUnit XML, no combined coverage; it is warned of
instead). Otherwise each worker analyses alone and **skips** rather than deselects: differing skips abort nothing,
differing collections do. Keep this path as built (review rounds oscillated until it was). Workers report their
deselection via `config.workeroutput`, which `pytest_testnodedown` keeps for the exit status and summary; both are
`optionalhook`s, as xdist need not be installed.

**Changed-file paths are POSIX strings.** `normalize_git_paths` emits `as_posix()` because
every matcher is `PurePosixPath`-based; an OS-native Windows path would carry backslashes
into the file name and match nothing.

**`pytest_plugins` declarations are edges, and their targets are global.** `build_dep_tree` treats each entry in a
conftest, test module or (transitively) plugin as an import by the declarer (so an edge *from the plugin to its
declarer*) and flags targets with the `pytest_plugin` node attribute. `PytestImpactStrategy` marks *every* test
impacted when a change reaches one, or a `-p` / `PYTEST_PLUGINS` plugin (`_session_wide_changes`): pytest
registers plugins session-wide. Without a session the CLI reads `-p` where pytest does before a command line
(`ini.dash_p_plugins`: `addopts`, `PYTEST_ADDOPTS`), handed in as `PytestImpactStrategy(plugins=)`. Editing a mere *declarer* is deliberately not session-wide (it over-selected every
test module with `pytest_plugins = "pytester"`, and cannot see a removed declaration anyway).
`parse_pytest_plugins` uses stdlib `ast` outside the backend (so it cannot break parity), behind a text pre-filter,
warnings silenced so `-W error` cannot turn a `SyntaxWarning` into a lost declaration (`_quiet_parse` does the same
for imports). `read_source` and `is_conftest_module` are the shared reader and conftest check — don't
re-implement either.

**A star import loads the submodules `pkg.__all__` lists**, whether the `__init__` imports them or not:
`graph._star_names` adds `pkg.<name>` per name of the package's literal `__all__`, the package found as its import
is (import roots and basedirs). Both readers (`parsing.star_imports_in`, `parse_all_names`) are stdlib `ast` passes
outside the backends; `_parse_project` reads each file once for both and keeps only the results (holding sources
costs memory). A file the running Python cannot parse loses its star imports under either backend.

**`parse_file_imports` returns *candidates*, not resolved modules.** `from pkg import name` emits `pkg` and
`pkg.name`; choosing would mean importing `pkg`, so `build_dep_tree` maps candidates to modules and aliases and
looks the rest up on disk (`_Linker`). The over-emission is the design, not a bug.

**Imports by name are import candidates** (`dynamic_imports.py`): literal arguments of calls in the `LOADERS`
table, each read as the equivalent import statement (`graph._imports_by_name`), so placement, deleted targets and
basedirs behave as for it. Add an API as a row, never a branch: a broad rule ("any string spelling a project
module") measured +13% tests on pydantic, ~99% noise. Rows without a module (`patch`, `patch.dict`,
`patch.multiple`, `setattr`, `delattr`) match any receiver, so a same-named method elsewhere makes a plain edge
that, from application code, could place a test helper as application code — unseen in six measured projects: a
documented trade-off, not a reach-only flag. An object path keeps every prefix longer than its module, so a deleted
module is linked whichever part it was. By-name candidates come after `linker.activate`: only static imports are
evidence of an assumed root. The extension finds calls in parallel (`parse_all_dynamic_imports`, reading
`dynamic_imports.TABLE`: one table for both backends); the stdlib pass does when it is older or absent. Only
literals count.

**Every setting is one row of `options.OPTIONS`** (pytest flag, ini key, CLI spelling, help, kind, default). The
plugin's `addoption`/`addini` pairs, the report header, the CLI's click options and `CLI_SETTINGS` are generated
from it — add a row, never hand-edit one front end. Module and tests-dir checks are `validation.py`, shared by both
front ends so they cannot diverge; `api.get_impacted_tests` normalizes the module (`./app/` → `app`) for every
caller.

**The `impacted-tests` CLI reads the pytest configuration through `ini.py`, never a pytest `Config`**: building
one imports the initial conftests, running project code. `ini.py` mirrors the installed pytest's search and
conversions; `tests/test_ini.py` holds it to `pytester.parseconfig` for every format and precedence rule, and is
what notices when pytest changes. Follow each change behind a version gate (`PYTEST_8_1`, `PYTEST_9`,
`PYTEST_9_2`), never by dropping the old rule: pytest 8.0 and later are supported. Loaders come from pytest's own
two tables, by name then suffix (`_BY_NAME`, `_BY_SUFFIX`), one per pytest function, gates in the tables — never
name checks inside suffix loaders (that missed two); other gates sit in the function mirroring pytest's for the
same job (the search, `_tool_pytest`, `_pytest_table`, `load_pytest_config`). The test job runs only the locked
pytest; its 3.11 python job reruns `test_ini.py`, `test_cli_config.py` and `test_pythonpath_integration.py` under
older ones, so add a version there for a new gate. Before pytest 8.4, `pythonpath` belongs to the blockable
`python_path` plugin (`getini` raises without it), hence `plugin._pythonpath`. An option takes its file setting
only when `ctx.get_parameter_source` says the command line left it at its default (`cli._settings`). The root stays
`--root-dir` (default `.`), as for `pytest --rootdir`, never the file's directory: moving it reinterprets an
explicit `--module`.

**The Python and Rust backends must agree exactly.** `parsing.py` (stdlib `ast`) and `rust/src/lib.rs` (ruff)
both feed `build_dep_tree`; any divergence silently changes which tests run under the `fast` extra — change one,
change the other (CI runs both on 3.11–3.14). Parity tests: `test_parse_file_imports_matches_rust_backend` (read
with `utf-8-sig`, so a BOM parses; scan `match`/`case` bodies); `tests/test_type_only.py` (`type_only.py` ports
`type_only.rs` walk for walk: `elif TYPE_CHECKING:` nests in `ast`'s `orelse` but is a clause in ruff's
`elif_else_clauses`; no bare string counts, docstrings included; an f-string's text is no string and its
placeholder (`FormattedValue`, `Interpolation`) no nesting level; annotations are only a parameter's, a return's
and `AnnAssign`'s, so a type parameter's bound and a `type` alias's value run; type comments are no nodes);
`tests/test_dynamic_imports.py` against `rust/src/dynamic_imports.rs` (any non-ASCII character is an
identifier's, but lone surrogates and the U+FFFD ruff makes of them, so no Unicode version decides; the Rust port
of `importlib.util.resolve_name` must keep its `rsplit` arithmetic).
The extension returns `None`, leaving a file to Python (never less than that side), when ruff rejects what CPython
accepts (`f(a=1, a=2)`, 203 nested conditionals), nesting exceeds `DEEPEST`, or the file is not UTF-8
(`read_source` decodes a PEP 263 cookie), over `LARGEST_SOURCE` or unreadable — for imports by name and
(`parse_imports_each`, since 0.41) import statements alike. The older dict functions read every file, as an older
pytest-impacted cannot read one handed back (up to 6 MiB, `OLDER_LARGEST_SOURCE`, a larger file importing nothing
there). More, only where the running Python cannot parse the file (newer syntax). A table an older extension cannot read (ValueError,
TypeError) leaves every file to that pass. Only UTF-8 crosses into the extension (`_rust.takes`): PyO3 raises
UnicodeEncodeError on a surrogate escape (a non-UTF-8 file name on Linux), so such a file, path or name stays in
Python.
Batches run in an owned pool with 256 MiB stacks (`in_pool`, reserved, not committed): freeing a left-deep
`a + b + ...` recurses about 64 bytes a term, which overflowed rayon's 2 MiB threads and killed the host process;
the 4 MiB cap keeps the need at half the stack. Both type-only visitors stop at `DEEPEST`, every import then a
runtime one (`type_only.DEEPEST` counts as ruff does; pinned at 2,000/2,001 terms in `tests/test_deep_sources.py`).
Deep-source tests run several files per batch in a subprocess: a crash kills the process, and a one-file batch may
run on the caller's larger stack, hiding it. Check new rules over every file of a real clone too.
The extension is a separate distribution and the `fast` extra is unpinned (a development build is `0.1.0`, which a
pin would refuse), so an install may hold an older one: never change an existing function's signature — add one
(`parse_all_imports_split`), import it in `_rust.py` behind `ImportError` (`RUST_SPLITS_TYPE_ONLY`), and let
`uses_rust` leave parsing to Python when the extension cannot honour the options, warning in the header and CLI
(`outdated_for`) — never a TypeError, never an option dropped unseen. Call it in
`.github/scripts/smoke_rust_wheel.py` too, which runs every loadable wheel before publishing: a function missing
from a wheel would otherwise silently shrink the Rust path.

**Logging: every module that logs declares `logger = logging.getLogger(__name__)`.**
Never call `logging.info(...)` and friends — those hit the root logger and are
flagged by LOG015.

**Ruff rule selection uses `extend-select`, not `select`.** "Simplifying" it back to `select` would silently
disable every default rule not named in the list. Ruff also formats Python code blocks inside Markdown, so
`ruff format` covers `README.md` and `docs/*.md`.

**Tooling versions come from `uv.lock` only.** Ruff/mypy/pytest run in pre-commit as `local`/`system` hooks
invoking `uv run …`, and CI's lint job runs `uv run pre-commit run --all-files` with `SKIP=pytest`, so
`.pre-commit-config.yaml` alone defines what is enforced. Upgrade ruff/mypy/pytest with `uv lock --upgrade` (no
hook `rev`; the remaining `rev:` pins, pre-commit-hooks and uv-pre-commit, via `pre-commit autoupdate`); new checks
go in the pre-commit config, never as bare workflow steps.

**The ruff version in `uv.lock` and the `ruff_python_parser` / `ruff_python_ast` git
tags in `rust/Cargo.toml` are kept at the same release.** Bump them together.

**Building the Rust crate with plain `cargo build` fails to link** — it is a pyo3 `extension-module` with no
libpython to link against. Use `cargo check` to typecheck and maturin (or `uv sync`) to build; lint from the repo
root with `--manifest-path rust/Cargo.toml`. `uv sync` does not rebuild the extension when `rust/src` or the Cargo
files change: run `uv sync --all-extras --dev --reinstall-package pytest-impacted-rs`. Without `--all-extras` that
sync *removes* the extension, and the parity tests then skip.

**On macOS 27 and later, build the extension with Rust 1.98 or newer.** An older rustc strips a release build
into a file dyld refuses ("mis-aligned LINKEDIT string pool"). The build succeeds and `_rust.py` silently falls back
to the Python backend — only `backend=` in the report header shows it — while the parity tests fail on the import
rather than skip.

## Strategies

`strategies.py` defines `ImpactStrategy` (ABC) and: `ASTImpactStrategy`; `PytestImpactStrategy` (a conftest edited
or importing changed *test* code impacts every test in its directory and below — tests never import their conftest,
so import analysis cannot see it); `ConftestImportImpactStrategy` (the same for changed *application* code, per
`traversal.discover_application_files`: the `--impacted-module` walk less the `--impacted-tests-dir` walk,
conftests excluded — by discovery, never by path, which a symlinked subpackage resolves elsewhere; in the default
pipeline but `report_only` unless `--impacted-conftest-imports`, since with a root conftest importing the app
nearly every edit would be a full run — keep it so until narrowing makes it selective);
`DependencyFileImpactStrategy` (`DEFAULT_DEPENDENCY_FILE_PATTERNS` / `..._GLOB_PATTERNS` plus
`session.config.inipath`; off with `--no-impacted-dep-files`); `InvalidationFileImpactStrategy`
(`--impacted-invalidate-all` globs; added only when configured, independent of `--no-impacted-dep-files`); and
`CompositeImpactStrategy`, the union, built by `get_default_strategies()`. Duck-typed strategies need only
`find_impacted_tests`: the composite skips missing lifecycle hooks, and `get_impacted_tests` wraps a bare one in a
composite.

**`--impacted-map GLOB=PATH` is no strategy** (`file_map.py`): `get_impacted_tests` adds mapped paths to the
changed files before `link_changed_files`, so every rule and extension treats them as edits (as a strategy it
missed a file no walk reaches, `package_members`, the conftest opt-in). A path means the files git knows there
(`git.files_git_knows`: tracked, or untracked and not ignored) — never an `os.walk` (a virtualenv) nor graph nodes
(a file no walk reaches) — spelled exactly, checked by a fresh listing (macOS git lists a wrong-case pathspec as
untracked). Parent directories are resolved, the path itself not: a tracked symlink stands for itself unless it
points at a directory. A directory means every file git knows in it, not only Python files. A path git knows
nothing at is warned of in the report header every run (from the controller, so xdist shows it).

**All file globs go through `matches_any_glob`** (`PurePosixPath.match`, right-anchored, `*` never spans `/`; a
`**` segment matches any number of segments, none included, which `pathlib` cannot do before 3.13 — tried only
after `match`, so it only widens: never route a glob past `match`, whose matches, version quirks included, must
all stay), and every "this directory and below" match (the conftest rule's `find_test_modules_under`, a changed
package's members) goes through `graph.nodes_under`. Do not add a second matcher or directory walk.

Third-party strategies are discovered via the `pytest_impacted.strategies` entry point group and composed in by
`api.build_strategy_with_extensions()` — the composition root, and the only module that knows both built-ins and
extensions; `extensions.py` therefore imports nothing from `strategies.py`. Built-ins run first, then extensions by
`priority` — ordering rarely matters since results are unioned.

**The extension API is documented in full in `docs/extensions.md`** — lifecycle
hooks, config options, the cache convention, error handling, testing patterns. Read
that rather than re-deriving it, and put new extension-author content there.

## Testing

`pytester` is enabled in `tests/conftest.py`. End-to-end tests build throwaway repos with the `make_git_project`
factory fixture (which also isolates the in-process `runpytest` from your git config) and `git_helpers.edit_file`;
an autouse fixture clears the analysis caches around every test. Plain helpers live in `tests/git_helpers.py` —
never import from a conftest. In-process projects must not name their tests dir `tests` (it clashes with this
repo's package in `sys.modules`); use `suite`.

**Every test must pass inside a pytest-xdist worker** (CI's impacted job reruns them under `-n auto`). A test
project's conftest detects a worker by `config.workerinput`, never `PYTEST_XDIST_WORKER`, which a nested run
inherits from the suite's own worker. No test report may hold a non-UTF-8 string (a surrogate escape, in a log
record or a message): execnet cannot send it, and the whole xdist run fails with an INTERNALERROR.

**The suite scrubs git's repo-locating variables** (`GIT_DIR`, `GIT_INDEX_FILE`, … from
`git rev-parse --local-env-vars`, with a fallback copy) in `pytest_configure` and restores them in
`pytest_unconfigure`: git exports them to hooks, and inherited when the pre-commit hook runs the suite they point
every throwaway repository at the real one (from a linked worktree this overwrote the index and set
`core.bare=true` on the shared `.git`). It must stay `pytest_configure`, not an autouse fixture — session-scoped
repo fixtures are set up before any function-scoped one.

**The suite dogfoods the plugin.** `[tool.pytest.ini_options]` sets `impacted_module` and `impacted_tests_dir`, so
the pre-commit hook runs `pytest -n auto --impacted --impacted-git-mode=unstaged` — except that a change to
`pytest_impacted/` or `rust/` (`--impacted-invalidate-all`) runs every test, since the selecting code is under
test. CI runs every test on every cell (`-n auto`); its impacted job reruns the selection serially and under xdist.
The config also sets `--strict-markers`, `--strict-config`, `xfail_strict` and `filterwarnings = error` (not pytest
9's `strict = true`, which the older-pytest step would refuse). A test must not read the repository's own
configuration through the working directory: run it from `tmp_path`, as `in_tmp_dir` does.

## Documentation

Five surfaces, different audiences — keep them in sync in the same PR as the change:

- `README.md` — concise overview for GitHub/PyPI (also the MkDocs home page)
- `docs/usage.md` — reference for people *running* pytest-impacted
- `docs/extensions.md` — reference for people *building strategies*; keep
  extension-author content here and do not let it leak back into `usage.md`
- `rust/README.md` — pytest-impacted-rs's PyPI page: what the extension is, how to install it, its speed figure
- `CLAUDE.md` — this file

## Commands

```bash
uv sync --all-extras --dev

# Every test, on every core (what CI runs on each cell)
uv run python -m pytest -n auto

# Only the tests your uncommitted change impacts (what the pre-commit hook runs)
uv run python -m pytest -n auto --impacted --impacted-git-mode=unstaged

# Coverage: `coverage run`, not `--cov` — the plugin is imported before pytest-cov starts, which misses its
# module-level lines; [tool.coverage.run] follows the xdist workers and subprocesses
uv run coverage run -m pytest -n auto && uv run coverage combine && uv run coverage report

# Everything CI enforces
uv run pre-commit run --all-files

cargo fmt --manifest-path rust/Cargo.toml --check
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings

# Build the Rust extension from source
pip install maturin && cd rust && maturin develop --release
```

Python 3.11+; CI matrix covers 3.11–3.14 against both the pure-Python and `fast`
(Rust) backends.
