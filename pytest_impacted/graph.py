"""Graph analysis functionality."""

import logging
import os
from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path, PurePosixPath

import networkx as nx

from pytest_impacted._rust import RUST_AVAILABLE, rust_parse_all_imports
from pytest_impacted.parsing import (
    is_conftest_module,
    is_test_module,
    parse_file_imports,
    parse_pytest_plugins,
)
from pytest_impacted.traversal import (
    LAST_RESORT_PREFIX,
    _discover_project,
    _Discovered,
    _is_regular_package,
    canonical_root,
    import_base,
    locate_module,
    module_parts,
    modules_for_files,
    split_import_roots,
)


logger = logging.getLogger(__name__)


def _parse_all_module_imports(submodules: dict[str, str]) -> dict[str, list[str]]:
    """Parse imports for all discovered submodules.

    A conftest with no free name is keyed by its last resort (see
    :func:`~pytest_impacted.traversal.discover_ancestor_conftests`), which is no base
    for its relative imports, so it is parsed under its
    :func:`~pytest_impacted.traversal.import_base`. That name may belong to another
    module, so it is parsed apart.
    """
    bases = {import_base(name): name for name in submodules if name.startswith(LAST_RESORT_PREFIX)}
    if not bases:
        return _parse_imports(submodules)
    result = _parse_imports(
        {name: path for name, path in submodules.items() if not name.startswith(LAST_RESORT_PREFIX)}
    )
    parsed = _parse_imports({base: submodules[name] for base, name in bases.items()})
    return result | {bases[base]: imports for base, imports in parsed.items()}


def _parse_imports(submodules: dict[str, str]) -> dict[str, list[str]]:
    """Parse the imports of each ``{name: path}``, resolving relative imports from *name*.

    Uses the Rust extension (parallel batch via rayon) when available,
    falling back to sequential astroid parsing.
    """
    if RUST_AVAILABLE:
        modules_info = [(path, name, path.endswith("__init__.py")) for name, path in submodules.items()]
        return rust_parse_all_imports(modules_info)

    result: dict[str, list[str]] = {}
    for name, file_path in submodules.items():
        logger.debug("Processing submodule: %s", name)
        is_pkg = file_path.endswith("__init__.py")
        result[name] = parse_file_imports(file_path, name, is_package=is_pkg)
    return result


def is_test_node(dep_tree: nx.DiGraph, node: str) -> bool:
    """Whether graph node *node* is a test module to run.

    Its ``test`` attribute says so when set: a module no walk names is named from wherever
    it was found, so it is judged by its path under the rootdir instead, and a deleted file
    linked in for the run is nothing to run. Otherwise its name decides
    (:func:`is_test_module`) — or, for an ``external`` node without the attribute (one an
    extension added), its name or its file's.
    """
    attributes = dep_tree.nodes[node]
    if "test" in attributes:
        return bool(attributes["test"])
    path = attributes.get("path")
    by_file = bool(attributes.get("external") and isinstance(path, str | os.PathLike)) and is_test_module(
        _importable_stem(Path(path))
    )
    return (isinstance(node, str) and is_test_module(node)) or by_file


def _is_test_file(path: Path, root: Path) -> bool:
    """Whether the file at *path* is a test module, judged by its name from the rootdir, as a walk would."""
    parts = module_parts(path.relative_to(root))
    return bool(parts) and os.path.exists(path) and is_test_module(".".join((*parts[:-1], _importable_stem(path))))


def _importable_stem(path: Path) -> str:
    """The module name pytest's importlib mode gives *path*: dots in the stem become underscores."""
    return (path.parent.name if path.name == "__init__.py" else path.stem).replace(".", "_")


def resolve_impacted_tests(impacted_modules, dep_tree: nx.DiGraph) -> list[str]:
    """Resolve impacted tests based on impacted modules.

    Every node that depends on an impacted module, directly or transitively
    (:func:`reached_from`, the impacted modules themselves included), that is a test module
    (:func:`is_test_node`).

    For modules not found in the dependency tree (e.g. a name an extension passes that is no node):
    - Test modules are included directly as impacted (they changed, so they should run).
    - Production modules cause ALL test modules to be marked as impacted,
      erring on the side of caution per project philosophy.

    """
    impacted_modules = list(impacted_modules)
    impacted_tests = []
    every_test = cache(lambda: [node for node in dep_tree.nodes if is_test_node(dep_tree, node)])

    for module in impacted_modules:
        if module in dep_tree.nodes:
            continue
        logger.warning(
            "Module %s is marked as impacted but was not found in dependency tree "
            "(a name that is no node of the graph).",
            module,
        )
        if is_test_module(module):
            # Test module changed but not in tree — include it directly.
            impacted_tests.append(module)
        else:
            # Production module changed but not in tree — conservatively
            # mark all known test modules as impacted.
            logger.warning(
                "Production module %s not in dependency tree; conservatively marking all test modules as impacted.",
                module,
            )
            impacted_tests.extend(every_test())

    impacted_tests.extend(node for node in reached_from(impacted_modules, dep_tree) if is_test_node(dep_tree, node))

    # Remove duplicates and sort the list for good measure.
    # (although the order of the tests should not matter)
    return sorted(set(impacted_tests))


def reached_from(modules: Iterable[str], dep_tree: nx.DiGraph) -> set[str]:
    """Every node that depends, directly or transitively, on *modules* (those that are nodes included).

    One multi-source traversal, not one per module: a package whose ``__init__.py`` changed
    is thousands of changed modules sharing their dependents (:func:`package_members`).
    """
    sources = [module for module in modules if module in dep_tree]
    return set().union(*nx.bfs_layers(dep_tree, sources))


def package_members(filenames: Iterable[str], dep_tree: nx.DiGraph, root_dir: str | Path | None = None) -> list[str]:
    """The nodes inside the package of each changed ``__init__.py`` — edited, added or deleted.

    Importing a module runs every package ``__init__.py`` above it first — ``from app.core.x
    import f`` runs ``app/__init__.py`` and ``app/core/__init__.py`` — but names neither, so no
    import edge links them: a changed ``__init__.py`` changes every module in its package. A
    member is a module node whose file is in the package's directory or below, or whose name or an
    alias is inside the package's or a package node's there: a module symlinked into it lives
    elsewhere, as may one an extension generates. (A module importing a missing name inside the
    package is no member but a dependent: see :func:`link_changed_files`.)

    *filenames* are as git reports them: POSIX paths relative to the rootdir, or absolute for a
    file outside it — which is how an ``__init__.py`` above the rootdir arrives. It counts only
    through an unbroken chain of packages down to the rootdir, as pytest's prepend mode imports a
    test module (every node under the rootdir is then a member); otherwise imports start inside
    the rootdir.
    """
    root = canonical_root(root_dir)
    # The directory, resolved, not the file: an ``__init__.py`` may itself be a symlink.
    directories = {
        directory
        for file in filenames
        if PurePosixPath(file).name == "__init__.py"
        if _runs_for_the_project(directory := (root / file).parent.resolve(), root)
    }
    if not directories:
        return []
    # Module nodes with a file only: an extension may add any hashable node, and read the ``path``
    # of every impacted module (all of 0.33.0's had one).
    paths = {
        node: _canonical(os.fspath(path), root)
        for node, path in dep_tree.nodes(data="path")
        if isinstance(node, str) and isinstance(path, str | os.PathLike)
    }
    members = set(nodes_under(directories, paths))
    roots = [Path(d) for d in dep_tree.graph.get("import_roots", [root])]
    packages = {name for directory in directories for name in _importable_names(directory / "__init__.py", roots)}
    packages |= {node for node in members if paths[node].endswith(f"{os.sep}__init__.py")}

    def inside_a_package(name: str) -> bool:
        return not packages.isdisjoint(_enclosing_packages(name))

    members |= {node for node in paths if inside_a_package(node)}
    # ``app/core/linked -> ../other``: ``app.other.m`` is imported as ``app.core.linked.m`` too.
    members |= {
        node for alias, node in dep_tree.graph.get("aliases", {}).items() if node in paths and inside_a_package(alias)
    }
    return sorted(members)


def _enclosing_packages(name: str) -> list[str]:
    """The packages importing module *name* runs first: ``app``, ``app.core`` for ``app.core.x``."""
    parts = name.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts))]


def _runs_for_the_project(directory: Path, root: Path) -> bool:
    """Whether importing the project's modules can run the ``__init__.py`` in *directory*."""
    if directory.is_relative_to(root):
        return True
    below = [root, *root.parents[: len(root.parents) - len(directory.parents) - 1]]
    return root.is_relative_to(directory) and all(map(_is_regular_package, below))


def _canonical(path: str, root: Path) -> str:
    """*path* normalised (``..``), and resolved when that leaves it outside the resolved rootdir.

    Discovery records resolved paths; an extension may not (pytest's rootdir itself can be a
    symlink). Resolving thousands costs a syscall per part each, so the others stay as spelled —
    right for membership, too: ``app/core/linked/gen.py`` imports through ``app.core``, wherever
    ``linked`` points.
    """
    normal = os.path.normpath(path)
    return normal if normal.startswith(os.path.join(root, "")) else os.path.realpath(path)


def nodes_under(directories: Iterable[Path], paths: dict[str, str]) -> list[str]:
    """The nodes among *paths* (``{node: file}``, normalised as the caller needs) whose file is in
    one of *directories* (resolved) or below — the one "this directory and below" matcher.

    Plain string prefixes ending in the separator (``app/core/`` holds no ``app/core_utils.py``):
    pathlib is far slower on thousands of nodes.
    """
    # normcase: as ``Path.relative_to`` does on Windows, which is case-insensitive (a no-op elsewhere).
    # A path that is one of the directories counts too, as with ``relative_to``.
    exact = {os.path.normcase(os.path.normpath(directory)) for directory in directories}
    prefixes = tuple(os.path.join(directory, "") for directory in exact)
    return [
        node
        for node, path in paths.items()
        if (normal := os.path.normcase(path)).startswith(prefixes) or normal in exact
    ]


def resolve_files_to_nodes(filenames: list[str], dep_tree: nx.DiGraph, root_dir: str | Path | None = None) -> list[str]:
    """Resolve changed files (relative to *root_dir*, as git reports them) to the graph's nodes.

    Through the nodes' own ``path``, not a second discovery: every module returned is a
    node, however the graph was cached. A module the graph lacks would read as a
    production module outside it, and :func:`resolve_impacted_tests` would select every test.
    """
    return modules_for_files(filenames, _nodes_by_path(dep_tree), root_dir)


def _nodes_by_path(dep_tree: nx.DiGraph) -> dict[str, str]:
    """``{file: node}`` for the nodes that have one.

    A ``path`` that is a string or path-like only: an extension may set anything else.
    """
    return {
        os.fspath(path): node
        for node, path in dep_tree.nodes(data="path")
        if path and isinstance(path, str | os.PathLike)
    }


def nodes_named(names: Iterable[str], dep_tree: nx.DiGraph) -> set[str]:
    """The nodes that module *names* from outside the source mean — ``-p`` plugins, say.

    A node's own name or an alias of one, and every node whose file the name is found at
    under the graph's import roots, as an import of it is looked up (:class:`_Linker`):
    ``-p app.plugin`` is ``src/app/plugin.py`` with ``src/`` on ``sys.path``, though nothing
    in the source spells it so. Which file a name means depends on ``sys.path``, so each
    counts; a name found nowhere means no node.
    """
    names = list(names)
    roots = [Path(directory) for directory in dep_tree.graph.get("import_roots", [])]
    if not names:
        return set()
    aliases = dep_tree.graph.get("aliases", {})
    found = {node for name in names if (node := aliases.get(name, name)) in dep_tree}
    if roots:  # the rootdir comes first
        by_path = _nodes_by_path(dep_tree)
        located = (path for name in names for path in locate_module(name, roots, roots[0]))
        found.update(by_path[path] for path in located if path in by_path)
    return found


class _Linker:
    """Maps import candidates to graph nodes, looking up modules outside the walks on disk.

    A candidate that names no discovered module, alias or contested conftest is looked up
    under the import roots (:func:`~pytest_impacted.traversal.locate_module`), once. A file
    the walks already named links to its node, and the candidate becomes one of its
    aliases; any other file becomes an *external* node, named by the candidate.

    *roots* are those the project's names imply, *assumed* the others
    (:func:`~pytest_impacted.traversal.split_import_roots`): a file found under an assumed
    root alone is linked all the same, and told apart by :meth:`only_assumed`.
    """

    def __init__(self, discovered: _Discovered, roots: list[Path], root: Path, assumed: Iterable[Path] = ()):
        self.modules = dict(discovered.modules)
        self.aliases = dict(discovered.aliases)
        self.external: set[str] = set()
        self._contested = discovered.contested
        self._roots, self._assumed, self._root = roots, list(assumed), root
        self._by_path = {path: name for name, path in self.modules.items()}
        self._located: dict[str, list[str]] = {}
        self._only_assumed: dict[str, set[str]] = {}

    def targets(self, candidate: str) -> list[str]:
        """The nodes an import of *candidate* depends on (none: it names no module in the project)."""
        # First: a name looked up on disk may have found several files, one named after it.
        if (located := self._located.get(candidate)) is not None:
            return located
        known = self.aliases.get(candidate, candidate), *self._contested.get(candidate, ())
        if found := [name for name in known if name in self.modules]:
            return found
        implied = locate_module(candidate, self._roots, self._root)
        assumed = [path for path in locate_module(candidate, self._assumed, self._root) if path not in implied]
        nodes = [self._node_for(candidate, path) for path in (*implied, *assumed)]
        self._located[candidate] = nodes
        if assumed:
            self._only_assumed[candidate] = set(nodes[len(implied) :]) - set(nodes[: len(implied)])
        return nodes

    def only_assumed(self, candidate: str) -> set[str]:
        """Those of *candidate*'s :meth:`targets` found under an assumed import root alone."""
        return self._only_assumed.get(candidate, set())

    def _taken(self, name: str) -> bool:
        return name in self.modules or name in self.aliases

    def _node_for(self, candidate: str, path: str) -> str:
        if (node := self._by_path.get(path)) is not None:
            if candidate not in self.modules:  # not when it named a file another root found
                self.aliases.setdefault(candidate, node)
            return node
        # Two files under one name (one per import root): the second is named by its path.
        node = _free_name([candidate], _last_resort_name(Path(path), self._root), self._taken)
        self.modules[node] = path
        self.external.add(node)
        self._by_path[path] = node
        return node


def _free_name(preferred: Iterable[str], last_resort: str, taken: Callable[[str], bool]) -> str:
    """The first of *preferred* not *taken* (by a node or an alias), else *last_resort*, made unique.

    A package and a module file of the same name share a last-resort name.
    """
    name = next((name for name in preferred if not taken(name)), last_resort)
    while taken(name):
        name += "_"
    return name


def _last_resort_name(path: Path, root: Path) -> str:
    """A unique node name no import spells, from *path* relative to *root* (see ``LAST_RESORT_PREFIX``)."""
    return LAST_RESORT_PREFIX + ".".join(module_parts(path.relative_to(root)))


def link_changed_files(filenames: list[str], dep_tree: nx.DiGraph, root_dir: str | Path | None = None) -> list[str]:
    """Link each changed ``.py`` file inside the rootdir into *dep_tree*; return the nodes added.

    A module deleted since the graph was built, or created after it was cached, is no
    node, nor is an existing file no walk reaches and nothing imports. Each gets one on
    *dep_tree* — the run's copy, never the cached graph — with an edge to every module
    whose import of one of its names matched nothing (``graph["unresolved"]``; flagged
    ``assumed_root`` when only a name under an assumed import root matched). A deleted
    file nothing imports gets none — unless it is an ``__init__.py`` whose package has importers
    of a missing name (below): nothing else is left for it to impact.

    A changed ``__init__.py``, a node or not, also gets an edge to every module importing a
    missing name inside its package (``try: import app.core.fast``): that import runs it before
    failing, but there is no module to be a member of the package (:func:`package_members`). The
    graph has no such edge, only the run's copy, from the ``__init__`` that changed: an edit to a
    module the ``__init__`` imports must not reach every importer of a name inside it. The edge
    is flagged ``runs_init``: it reaches the importer, but does not place the package.
    """
    root = canonical_root(root_dir)
    by_path = _nodes_by_path(dep_tree)
    aliases = dep_tree.graph.setdefault("aliases", {})
    taken = set(dep_tree) | set(aliases)
    unresolved = dep_tree.graph.get("unresolved", {})
    roots = sorted((Path(d) for d in dep_tree.graph.get("import_roots", [root])), key=lambda d: -len(d.parts))
    assumed = {Path(d) for d in dep_tree.graph.get("assumed_roots", ())}
    inside = cache(lambda: _importers_inside(unresolved))
    added = []
    for file in filenames:
        path = (root / file).resolve()
        if not file.endswith(".py") or not path.is_relative_to(root):
            continue
        names = _importable_names(path, roots)
        node = by_path.get(str(path))
        runners: set[str] = set()
        if path.name == "__init__.py":
            # Its node's names too: ``libs/core/__init__.py`` is ``app.core`` through ``app/core -> ../libs/core``.
            package = {
                *names,
                *([node, *(alias for alias, target in aliases.items() if target == node)] if node else []),
            }
            runners = {runner for name in package for runner in inside().get(name, ())}
        if node is None:
            importers = {importer for name in names for importer in unresolved.get(name, ())}
            if not importers and not runners and not os.path.exists(path):
                continue
            implied = _importable_names(path, [base for base in roots if base not in assumed])
            guessed = importers - {importer for name in implied for importer in unresolved.get(name, ())}
            node = _free_name(names, _last_resort_name(path, root), taken.__contains__)
            # Its other names reach it too, e.g. a ``-p`` plugin spelled from the rootdir.
            aliases.update({name: node for name in names if name != node and name not in taken})
            taken.update(names)
            taken.add(node)
            dep_tree.add_node(node, path=str(path), external=True, test=_is_test_file(path, root))
            dep_tree.add_edges_from((node, importer) for importer in importers - guessed)
            dep_tree.add_edges_from(((node, importer) for importer in guessed), assumed_root=True)
            by_path[str(path)] = node
            added.append(node)
        # Only to reach them (``runs_init``), never to place the package as application code or test
        # code (see strategies._changes_by_role): that could make a conftest rule opt-in, fewer tests.
        if runs := [runner for runner in runners if runner != node and not dep_tree.has_edge(node, runner)]:
            dep_tree.add_edges_from(((node, runner) for runner in runs), runs_init=True)
            dep_tree.graph["runs_init"] = True
    return added


def _importers_inside(unresolved: dict[str, list[str]]) -> dict[str, set[str]]:
    """``{package name: modules importing a name inside it that no walk defines}``, from ``graph["unresolved"]``.

    A name found outside the walks is a member of its package too, so linking its importers only
    repeats what the member reaches — harmless, since the link does not place the package.
    """
    inside: dict[str, set[str]] = {}
    for name, importers in unresolved.items():
        for package in _enclosing_packages(name):
            inside.setdefault(package, set()).update(importers)
    return inside


def _importable_names(path: Path, roots: list[Path]) -> list[str]:
    """The dotted names *path* is imported under from each of *roots* that contains it, in order."""
    names = []
    for base in roots:
        if not path.is_relative_to(base):
            continue
        parts = module_parts(path.relative_to(base))
        if parts and all(part.isidentifier() for part in parts):
            names.append(".".join(parts))
    return names


def _pytest_plugin_edges(linker: _Linker, declarations) -> dict[str, list[str]]:
    """``{declaring module: [plugin modules]}`` for every ``pytest_plugins`` declaration in scope.

    pytest reads the declaration from conftests and test modules, and again from
    every plugin it loads that way, so plugins are followed transitively. Names
    outside the project — usually third-party plugins such as ``pytester`` — are skipped.
    """
    # pytest reads them from the conftests and test modules it collects: those the walks find.
    pending = [
        name
        for name in linker.modules
        if (is_conftest_module(name) or is_test_module(name)) and name not in linker.external
    ]
    edges: dict[str, list[str]] = {}
    while pending:
        name = pending.pop()
        if name in edges:
            continue
        edges[name] = []
        for declared in declarations(linker.modules[name]):
            plugins = linker.targets(declared)
            if not plugins:
                logger.debug(
                    "pytest_plugins entry %r in %s is not a module in the project; not followed", declared, name
                )
            edges[name] += plugins
            pending += plugins
    return {name: plugins for name, plugins in edges.items() if plugins}


def _parse_project(linker: _Linker) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """The import candidates of every module, and the ``pytest_plugins`` edges.

    Imports and declarations that name a module outside the walks add it (see
    :class:`_Linker`), and its own imports and declarations are followed in turn.
    """
    declarations = cache(parse_pytest_plugins)
    imports: dict[str, list[str]] = {}
    pending = dict(linker.modules)
    while True:
        imports |= _parse_all_module_imports(pending)
        for name in pending:
            for candidate in imports[name]:
                linker.targets(candidate)
        plugin_edges = _pytest_plugin_edges(linker, declarations)
        pending = {name: path for name, path in linker.modules.items() if name not in imports}
        if not pending:
            return imports, plugin_edges


def _may_name_a_module(candidate: str, walked: _Discovered) -> bool:
    """Whether *candidate* could name a module no walk names — one that is gone, say.

    Judged by the walks alone: a file the lookup found under some import root does not
    settle which file an import means, since that depends on ``sys.path``. Not a name the
    walks define, nor one inside a module file they found (``pkg.mod.func`` for ``from
    pkg.mod import func``). A standard-library name counts: a deleted local ``platform/``
    or ``secrets.py`` shadowed it.
    """
    named = walked.aliases.get(candidate, candidate), *walked.contested.get(candidate, ())
    if any(name in walked.modules for name in named):
        return False
    parent = candidate.rpartition(".")[0]
    parent_path = walked.modules.get(walked.aliases.get(parent, parent))
    return parent_path is None or parent_path.endswith("__init__.py")


def build_dep_tree(package: str, tests_package: str | None = None, root_dir: str | Path | None = None) -> nx.DiGraph:
    """Build a dependency tree using filesystem discovery (no imports).

    Scans the package directory to find modules, reads their source files,
    and parses imports via AST — without executing any module-level code.
    Package paths are resolved against *root_dir* (default: the current directory).

    Conftests above the packages are included too (see
    :func:`~pytest_impacted.traversal.discover_project_modules`), and so is any
    module outside the walks that one of them imports, found on disk and flagged
    ``external`` (see :class:`_Linker`). Every node carries its absolute file in the
    ``path`` attribute. Modules named in a ``pytest_plugins`` declaration count as
    imports (see :func:`~pytest_impacted.parsing.parse_pytest_plugins`) and are
    flagged with the ``pytest_plugin`` attribute. Every import the walks do not
    define — found on disk or not — is kept in ``graph["unresolved"]`` with its
    importers, for :func:`link_changed_files`. An import found only under an assumed
    import root (``graph["assumed_roots"]``, see
    :func:`~pytest_impacted.traversal.split_import_roots`) is an edge flagged ``assumed_root``.
    """
    root = canonical_root(root_dir)
    discovered = _discover_project(package, tests_package, root)
    analysed = [name for name in (package, tests_package) if name]
    roots, assumed_roots = split_import_roots(analysed, discovered.modules, discovered.aliases, root)
    linker = _Linker(discovered, roots, root, assumed_roots)

    logger.debug("Building dependency tree for %d submodules", len(linker.modules))

    imports, plugin_edges = _parse_project(linker)

    digraph = nx.DiGraph()
    unresolved: dict[str, set[str]] = {}
    found: dict[bool, set[tuple[str, str]]] = {True: set(), False: set()}  # by "under an assumed root alone"
    for name, file_path in linker.modules.items():
        digraph.add_node(name, path=file_path)
        for candidate in imports[name]:
            # A name conftests above the package contest is an import of each of them too.
            targets = linker.targets(candidate)
            only_assumed = linker.only_assumed(candidate)
            for target in targets:
                digraph.add_edge(name, target)
                found[target in only_assumed].add((name, target))
            if _may_name_a_module(candidate, discovered):
                unresolved.setdefault(candidate, set()).add(name)
        for plugin in plugin_edges.get(name, ()):
            digraph.add_edge(name, plugin)
    # pytest registers plugins for the whole session; see PytestImpactStrategy.
    for plugin in {plugin for plugins in plugin_edges.values() for plugin in plugins}:
        digraph.nodes[plugin]["pytest_plugin"] = True
    for name in linker.external:
        digraph.nodes[name].update(external=True, test=_is_test_file(Path(linker.modules[name]), root))
    # Whether such a directory is on sys.path is a guess, and the match may be a coincidence of
    # names: the edge reaches the importer, but never places the file as application code
    # (see strategies._changes_by_role), which could make a conftest rule opt-in — fewer tests.
    for edge in found[True] - found[False]:
        digraph.edges[edge]["assumed_root"] = True

    # Other names each module imports under (see discover_project_modules), for names
    # that come from outside the source, such as ``-p`` plugins.
    digraph.graph["aliases"] = linker.aliases
    # For a changed file the graph lacks — deleted, or created since — see link_changed_files.
    digraph.graph["unresolved"] = {name: sorted(importers) for name, importers in unresolved.items()}
    digraph.graph["import_roots"] = [str(directory) for directory in (*roots, *assumed_roots)]
    digraph.graph["assumed_roots"] = [str(directory) for directory in assumed_roots]

    # The dependency graph is the reverse of the import graph, so invert it before returning.
    return digraph.reverse()
