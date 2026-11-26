"""What a dependency graph carries besides its nodes and edges: each key, what it holds, and who writes it.

``graph.build_dep_tree`` writes the graph-level keys and most node and edge keys, ``graph.link_changed_files`` adds
a run's own (on its copy); strategies and extensions read them. Each key is a plain ``str`` constant, stored as it
is: an extension's ``dep_tree.nodes[node]["path"]`` and ``dep_tree.nodes[node][Node.PATH]`` read the same attribute,
and the graph holds no object of this module (a graph is pickled and dumped unchanged).
"""

from collections.abc import Mapping
from typing import Any, Final


class Node:
    """A node's attributes (a node is a module, by its canonical dotted name)."""

    #: The module's source file, absolute: on every node, the only way from a module to its file (never rebuild one
    #: from the dotted name, which drops ``src/``).
    PATH: Final = "path"
    #: True for a module no walk found, joined by an import (or a changed file the run's graph lacked).
    EXTERNAL: Final = "external"
    #: Whether such a node is a test module, judged by its path from the rootdir (false for a deleted file).
    TEST: Final = "test"
    #: True for a ``pytest_plugins`` target: a change reaching it impacts every test.
    PYTEST_PLUGIN: Final = "pytest_plugin"

    ALL: Final = (PATH, EXTERNAL, TEST, PYTEST_PLUGIN)


class Edge:
    """An edge's flags; an edge points from a module to the module that imports it."""

    #: The import was found only under an assumed import root (a guess at ``sys.path``). Only reaches.
    ASSUMED_ROOT: Final = "assumed_root"
    #: The import goes through pytest's rootless test basedirs (which file it means depends on collection order).
    #: Only reaches.
    BASEDIR: Final = "basedir"
    #: A changed ``__init__.py`` to an importer of a missing name inside its package (a run's copy). Only reaches.
    RUNS_INIT: Final = "runs_init"

    ALL: Final = (ASSUMED_ROOT, BASEDIR, RUNS_INIT)


class Graph:
    """The graph's own attributes (``dep_tree.graph``)."""

    #: Another importable name -> the canonical name of the same file.
    ALIASES: Final = "aliases"
    #: An import candidate no walk defines -> the modules importing it.
    UNRESOLVED: Final = "unresolved"
    #: The directories names are looked up under on disk.
    IMPORT_ROOTS: Final = "import_roots"
    #: pytest's basedirs for the test modules and conftests (``prepend`` import mode).
    ROOTLESS_TEST_DIRS: Final = "rootless_test_dirs"
    #: Each assumed import root -> its :class:`AssumedRoot` record; an edge may then be flagged ``assumed_root``.
    ASSUMED_ROOTS: Final = "assumed_roots"
    #: True when some edge is flagged :attr:`Edge.BASEDIR`.
    BASEDIR_EDGES: Final = "basedir_edges"
    #: True when some edge is flagged :attr:`Edge.RUNS_INIT` (on a run's copy).
    RUNS_INIT: Final = "runs_init"

    ALL: Final = (ALIASES, UNRESOLVED, IMPORT_ROOTS, ROOTLESS_TEST_DIRS, ASSUMED_ROOTS, BASEDIR_EDGES, RUNS_INIT)


class AssumedRoot:
    """The record :attr:`Graph.ASSUMED_ROOTS` holds for each assumed import root."""

    #: The names leading from it to an analysed directory.
    GATEWAYS: Final = "gateways"
    #: Whether some walked module's import spells one (evidence the root is on ``sys.path``).
    ACTIVE: Final = "active"
    #: True when it is one of pytest's ``pythonpath`` entries too: every name is looked up there.
    CERTAIN: Final = "certain"


#: The edge flags that only reach — a dependent is linked, but application code is not said to import the module —
#: each with the graph flag set when some edge carries it. An edge flag that places is not listed.
REACH_ONLY: Final = {
    Edge.ASSUMED_ROOT: Graph.ASSUMED_ROOTS,
    Edge.BASEDIR: Graph.BASEDIR_EDGES,
    Edge.RUNS_INIT: Graph.RUNS_INIT,
}


def only_reaches(edge: Mapping[str, Any]) -> bool:
    """Whether an edge links a dependent without saying that application code imports the module: placing a module
    as application code must not follow it, or a test helper reached so would make its conftest rule opt-in."""
    return any(edge.get(flag) for flag in REACH_ONLY)


def may_hold_reach_only_edges(graph: Mapping[str, Any]) -> bool:
    """Whether some edge of a graph with these attributes may be flagged (few are: reading every edge is costly)."""
    return any(graph.get(sentinel) for sentinel in REACH_ONLY.values())
