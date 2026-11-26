"""What a dependency graph carries besides its nodes and edges: each key, what it holds, and who writes it.

``graph.build_dep_tree`` writes the graph-level keys and most node and edge keys, ``graph.link_changed_files`` adds
a run's own (on its copy); strategies and extensions read them. Each key is a ``StrEnum`` member, so a ``str`` too:
an extension's ``dep_tree.nodes[node]["path"]`` and ``dep_tree.nodes[node][Node.PATH]`` read the same attribute.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class Node(StrEnum):
    """A node's attributes (a node is a module, by its canonical dotted name)."""

    #: The module's source file, absolute: on every node, the only way from a module to its file (never rebuild one
    #: from the dotted name, which drops ``src/``).
    PATH = "path"
    #: True for a module no walk found, joined by an import (or a changed file the run's graph lacked).
    EXTERNAL = "external"
    #: Whether such a node is a test module, judged by its path from the rootdir (false for a deleted file).
    TEST = "test"
    #: True for a ``pytest_plugins`` target: a change reaching it impacts every test.
    PYTEST_PLUGIN = "pytest_plugin"


class Edge(StrEnum):
    """An edge's flags; an edge points from a module to the module that imports it. Each flag here only *reaches*:
    the dependent is linked, but application code is not said to import the module (see :func:`only_reaches`)."""

    #: The import was found only under an assumed import root (a guess at ``sys.path``).
    ASSUMED_ROOT = "assumed_root"
    #: The import goes through pytest's rootless test basedirs (which file it means depends on collection order).
    BASEDIR = "basedir"
    #: A changed ``__init__.py`` to an importer of a missing name inside its package.
    RUNS_INIT = "runs_init"


class Graph(StrEnum):
    """The graph's own attributes (``dep_tree.graph``)."""

    #: Another importable name -> the canonical name of the same file.
    ALIASES = "aliases"
    #: An import candidate no walk defines -> the modules importing it.
    UNRESOLVED = "unresolved"
    #: The directories names are looked up under on disk.
    IMPORT_ROOTS = "import_roots"
    #: pytest's basedirs for the test modules and conftests (``prepend`` import mode).
    ROOTLESS_TEST_DIRS = "rootless_test_dirs"
    #: Each assumed import root -> whether it is certain (a ``pythonpath`` entry); some edge then may be a guess.
    ASSUMED_ROOTS = "assumed_roots"
    #: True when some edge is flagged :attr:`Edge.BASEDIR`.
    BASEDIR_EDGES = "basedir_edges"
    #: True when some edge is flagged :attr:`Edge.RUNS_INIT` (on a run's copy).
    RUNS_INIT = "runs_init"


def only_reaches(edge: Mapping[str, Any]) -> bool:
    """Whether an edge links a dependent without saying that application code imports the module: placing a module
    as application code must not follow it, or a test helper reached so would make its conftest rule opt-in."""
    return any(edge.get(flag) for flag in Edge)


def may_hold_reach_only_edges(graph: Mapping[str, Any]) -> bool:
    """Whether some edge of a graph with these attributes may be flagged (few are: reading every edge is costly)."""
    return any(graph.get(flag) for flag in (Graph.RUNS_INIT, Graph.ASSUMED_ROOTS, Graph.BASEDIR_EDGES))
