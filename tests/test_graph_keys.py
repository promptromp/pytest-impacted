"""The graph's keys (``pytest_impacted.graph_keys``): every one a graph carries is declared there."""

import pytest

from pytest_impacted import graph
from pytest_impacted.graph_keys import Edge, Graph, Node, may_hold_reach_only_edges, only_reaches

from .git_helpers import write_files


@pytest.mark.parametrize("flag", list(Edge))
def test_each_edge_flag_only_reaches(flag):
    assert only_reaches({flag: True})
    assert not only_reaches({flag: False})


def test_a_plain_edge_places():
    assert not only_reaches({})


@pytest.mark.parametrize("flag", [Graph.RUNS_INIT, Graph.ASSUMED_ROOTS, Graph.BASEDIR_EDGES])
def test_the_graph_says_when_an_edge_may_only_reach(flag):
    assert may_hold_reach_only_edges({flag: {"x": False} if flag is Graph.ASSUMED_ROOTS else True})
    assert not may_hold_reach_only_edges({Graph.ALIASES: {}})


def test_a_key_is_its_string():
    """An extension reads ``nodes[node]["path"]``: the same attribute."""
    assert {Node.PATH: 1}["path"] == 1 and Edge.RUNS_INIT == "runs_init" and Graph.ALIASES == "aliases"


def test_a_graph_carries_no_key_graph_keys_does_not_declare(tmp_path):
    """Over a project with every kind of node and edge: walked and external modules, a pytest plugin, a rootless test
    dir's basedir, an import only an assumed root explains."""
    write_files(
        tmp_path,
        {
            "src/__init__.py": "",
            "src/app/__init__.py": "",
            "src/app/core.py": "import helpers\nimport app.extra\n",
            "src/app/extra.py": "",
            "lib/shared.py": "",
            "suite/helpers.py": "import shared\n",
            "suite/conftest.py": 'pytest_plugins = ["suite.fixtures"]\n',
            "suite/fixtures.py": "",
            "suite/test_core.py": "from app import core\n",
        },
    )

    dep_tree = graph.build_dep_tree("src/app", tests_package="suite", root_dir=tmp_path, pythonpath=["lib"])

    assert {key for _, data in dep_tree.nodes(data=True) for key in data} <= set(Node)
    assert {key for _, _, data in dep_tree.edges(data=True) for key in data} <= set(Edge)
    assert set(dep_tree.graph) <= set(Graph)
    assert any(data.get(Node.EXTERNAL) for _, data in dep_tree.nodes(data=True))  # not vacuous
    assert any(data.get(Node.PYTEST_PLUGIN) for _, data in dep_tree.nodes(data=True))
