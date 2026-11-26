"""The graph's keys (``pytest_impacted.graph_keys``): every one a graph carries is declared there."""

import pytest

from pytest_impacted import graph
from pytest_impacted.graph_keys import REACH_ONLY, Edge, Graph, Node, may_hold_reach_only_edges, only_reaches
from pytest_impacted.strategies import run_copy

from .git_helpers import write_files


@pytest.mark.parametrize("flag", Edge.ALL)
def test_each_edge_flag_only_reaches(flag):
    """Today every edge flag only reaches; one that places would be left out of REACH_ONLY."""
    assert flag in REACH_ONLY
    assert only_reaches({flag: True})
    assert not only_reaches({flag: False})


def test_a_plain_edge_places():
    assert not only_reaches({})


@pytest.mark.parametrize("flag", REACH_ONLY.values())
def test_the_graph_says_when_an_edge_may_only_reach(flag):
    assert may_hold_reach_only_edges({flag: {"x": False} if flag is Graph.ASSUMED_ROOTS else True})
    assert not may_hold_reach_only_edges({Graph.ALIASES: {}})


def test_a_key_is_a_plain_string():
    """Stored as it is: an extension reads ``nodes[node]["path"]``, and a dumped graph holds no object of ours."""
    assert all(type(key) is str for key in (*Node.ALL, *Edge.ALL, *Graph.ALL))


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

    built = graph.build_dep_tree("src/app", tests_package="suite", root_dir=tmp_path, pythonpath=["lib"])
    # And a run's own keys: a changed __init__ whose package an importer misses a name of, and a deleted module.
    dep_tree = run_copy(built)
    (tmp_path / "src/app/core.py").write_text("import helpers\nimport app.extra\nimport app.gone\n")
    graph.link_changed_files(["src/app/__init__.py", "src/app/gone.py"], dep_tree, root_dir=tmp_path)

    assert {key for _, data in dep_tree.nodes(data=True) for key in data} <= set(Node.ALL)
    assert {key for _, _, data in dep_tree.edges(data=True) for key in data} == set(Edge.ALL)  # each one, and no other
    assert set(dep_tree.graph) == set(Graph.ALL)
    assert any(data.get(Node.EXTERNAL) for _, data in dep_tree.nodes(data=True))  # not vacuous
    assert any(data.get(Node.PYTEST_PLUGIN) for _, data in dep_tree.nodes(data=True))
