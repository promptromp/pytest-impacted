"""Graph analysis functionality."""

import logging
import os
import sys
from functools import cache
from pathlib import Path

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
    canonical_root,
    import_base,
    import_roots,
    locate_module,
    modules_for_files,
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


def resolve_impacted_tests(impacted_modules, dep_tree: nx.DiGraph) -> list[str]:
    """Resolve impacted tests based on impacted modules.

    The current logic is to do a DFS from the impacted module to find all nodes that depend on it.
    We then check if these nodes are test modules.
    We return the list of test modules that are impacted.

    For modules not found in the dependency tree (e.g. outside the analyzed package scope):
    - Test modules are included directly as impacted (they changed, so they should run).
    - Production modules cause ALL test modules to be marked as impacted,
      erring on the side of caution per project philosophy.

    """
    impacted_tests = []
    all_test_modules_in_tree = [node for node in dep_tree.nodes if is_test_module(node)]

    for module in impacted_modules:
        if module not in dep_tree.nodes:
            logger.warning(
                "Module %s is marked as impacted but was not found in dependency tree "
                "(possibly outside the analyzed package scope).",
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
                impacted_tests.extend(all_test_modules_in_tree)
            continue

        dependent_nodes = [node for node in nx.dfs_preorder_nodes(dep_tree, source=module) if is_test_module(node)]

        impacted_tests.extend(dependent_nodes)

    # Remove duplicates and sort the list for good measure.
    # (although the order of the tests should not matter)
    impacted_tests = sorted(set(impacted_tests))

    return impacted_tests


def resolve_files_to_nodes(filenames: list[str], dep_tree: nx.DiGraph, root_dir: str | Path | None = None) -> list[str]:
    """Resolve changed files (relative to *root_dir*, as git reports them) to the graph's nodes.

    Through the nodes' own ``path``, not a second discovery: every module returned is a
    node, however the graph was cached. A module the graph lacks would read as a
    production module outside it, and :func:`resolve_impacted_tests` would select every test.
    """
    return modules_for_files(filenames, {path: node for node, path in dep_tree.nodes(data="path") if path}, root_dir)


class _Linker:
    """Maps import candidates to graph nodes, looking up modules outside the walks on disk.

    A candidate that names no discovered module, alias or contested conftest is looked up
    under the import roots (:func:`~pytest_impacted.traversal.locate_module`), once. A file
    the walks already named links to its node, and the candidate becomes one of its
    aliases; any other file becomes an *external* node, named by the candidate.
    """

    def __init__(self, discovered: _Discovered, roots: list[Path], root: Path):
        self.modules = dict(discovered.modules)
        self.aliases = dict(discovered.aliases)
        self.external: set[str] = set()
        self._contested = discovered.contested
        self._roots, self._root = roots, root
        self._by_path = {path: name for name, path in self.modules.items()}
        self._located: dict[str, list[str]] = {}

    def targets(self, candidate: str) -> list[str]:
        """The nodes an import of *candidate* depends on (none: it names no module in the project)."""
        # First: a name looked up on disk may have found several files, one named after it.
        if (located := self._located.get(candidate)) is not None:
            return located
        known = self.aliases.get(candidate, candidate), *self._contested.get(candidate, ())
        if found := [name for name in known if name in self.modules]:
            return found
        paths = locate_module(candidate, self._roots, self._root)
        self._located[candidate] = [self._node_for(candidate, path) for path in paths]
        return self._located[candidate]

    def _node_for(self, candidate: str, path: str) -> str:
        if (node := self._by_path.get(path)) is not None:
            self.aliases.setdefault(candidate, node)
            return node
        # Two files under one name (one per import root): the second is named by its path.
        node = candidate if candidate not in self.modules else _last_resort_name(Path(path), self._root)
        self.modules[node] = path
        self.external.add(node)
        self._by_path[path] = node
        return node


def _last_resort_name(path: Path, root: Path) -> str:
    """A unique node name no import spells, from *path* relative to *root* (see ``LAST_RESORT_PREFIX``)."""
    relative = path.relative_to(root)
    parts = relative.parent.parts if relative.name == "__init__.py" else (*relative.parent.parts, relative.stem)
    return LAST_RESORT_PREFIX + ".".join(parts)


def link_changed_files(filenames: list[str], dep_tree: nx.DiGraph, root_dir: str | Path | None = None) -> list[str]:
    """Give each changed ``.py`` file inside the rootdir that *dep_tree* lacks a node; return their names.

    A module deleted since the graph was built, or created after it was cached, is no
    node, nor is an existing file no walk reaches and nothing imports. Each gets one on
    *dep_tree* — the run's copy, never the cached graph — with an edge to every module
    whose import of one of its names matched nothing (``graph["unresolved"]``). A deleted
    file nothing imports gets none: nothing is left for it to impact.
    """
    root = canonical_root(root_dir)
    known = {path for _, path in dep_tree.nodes(data="path") if path}
    unresolved = dep_tree.graph.get("unresolved", {})
    roots = sorted((Path(d) for d in dep_tree.graph.get("import_roots", [root])), key=lambda d: -len(d.parts))
    added = []
    for file in filenames:
        path = (root / file).resolve()
        if not file.endswith(".py") or str(path) in known or not path.is_relative_to(root):
            continue
        names = _importable_names(path, roots)
        importers = {importer for name in names for importer in unresolved.get(name, ())}
        if not importers and not os.path.exists(path):
            continue
        node = next((name for name in names if name not in dep_tree), None) or _last_resort_name(path, root)
        dep_tree.add_node(node, path=str(path), external=True)
        dep_tree.add_edges_from((node, importer) for importer in importers)
        known.add(str(path))
        added.append(node)
    return added


def _importable_names(path: Path, roots: list[Path]) -> list[str]:
    """The dotted names *path* is imported under from each of *roots* that contains it, in order."""
    names = []
    for base in roots:
        if not path.is_relative_to(base):
            continue
        relative = path.relative_to(base)
        parts = relative.parent.parts if relative.name == "__init__.py" else (*relative.parent.parts, relative.stem)
        if parts and all(part.isidentifier() for part in parts):
            names.append(".".join(parts))
    return names


def _pytest_plugin_edges(linker: _Linker, declarations) -> dict[str, list[str]]:
    """``{declaring module: [plugin modules]}`` for every ``pytest_plugins`` declaration in scope.

    pytest reads the declaration from conftests and test modules, and again from
    every plugin it loads that way, so plugins are followed transitively. Names
    outside the project — usually third-party plugins such as ``pytester`` — are skipped.
    """
    pending = [name for name in linker.modules if is_conftest_module(name) or is_test_module(name)]
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


def _may_name_a_module(candidate: str, linker: _Linker) -> bool:
    """Whether *candidate*, which names no module, could name one that is gone.

    Not a name defined in a module file (``pkg.mod.func`` for ``from pkg.mod import
    func``), and not the standard library's.
    """
    if candidate.partition(".")[0] in sys.stdlib_module_names:
        return False
    parent = candidate.rpartition(".")[0]
    parent_path = linker.modules.get(linker.aliases.get(parent, parent))
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
    flagged with the ``pytest_plugin`` attribute. Imports that name no module are
    kept in ``graph["unresolved"]``, for :func:`link_changed_files`.
    """
    root = canonical_root(root_dir)
    roots = import_roots([name for name in (package, tests_package) if name], root)
    linker = _Linker(_discover_project(package, tests_package, root), roots, root)

    logger.debug("Building dependency tree for %d submodules", len(linker.modules))

    imports, plugin_edges = _parse_project(linker)

    digraph = nx.DiGraph()
    unresolved: dict[str, set[str]] = {}
    for name, file_path in linker.modules.items():
        digraph.add_node(name, path=file_path)
        for candidate in imports[name]:
            # A name conftests above the package contest is an import of each of them too.
            targets = linker.targets(candidate)
            for target in targets:
                digraph.add_edge(name, target)
            if not targets and _may_name_a_module(candidate, linker):
                unresolved.setdefault(candidate, set()).add(name)
        for plugin in plugin_edges.get(name, ()):
            digraph.add_edge(name, plugin)
    # pytest registers plugins for the whole session; see PytestImpactStrategy.
    for plugin in {plugin for plugins in plugin_edges.values() for plugin in plugins}:
        digraph.nodes[plugin]["pytest_plugin"] = True
    for name in linker.external:
        digraph.nodes[name]["external"] = True

    # Other names each module imports under (see discover_project_modules), for names
    # that come from outside the source, such as ``-p`` plugins.
    digraph.graph["aliases"] = linker.aliases
    # For a changed file the graph lacks — deleted, or created since — see link_changed_files.
    digraph.graph["unresolved"] = {name: sorted(importers) for name, importers in unresolved.items()}
    digraph.graph["import_roots"] = [str(directory) for directory in roots]

    # The dependency graph is the reverse of the import graph, so invert it before returning.
    return digraph.reverse()
