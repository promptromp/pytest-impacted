"""Unit tests for the graph module."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import networkx as nx
import pytest

from pytest_impacted import graph
from pytest_impacted.strategies import cached_build_dep_tree, clear_dep_tree_cache, run_copy
from pytest_impacted.traversal import _Discovered, path_to_package_name, resolve_files_to_modules

from .git_helpers import write_files


@pytest.fixture
def sample_dep_tree():
    """Create a sample dependency tree for testing."""
    digraph = nx.DiGraph()
    # Add some test and non-test modules
    # Note: edges go from regular modules to test modules in the dependency graph
    digraph.add_edges_from(
        [
            ("module_a", "test_module1"),
            ("module_b", "test_module1"),
            ("module_b", "test_module2"),
            ("module_c", "test_module2"),
            ("module_d", "module_a"),
            ("module_d", "module_b"),
            ("module_e", "module_c"),
        ]
    )
    return digraph


@pytest.mark.parametrize(
    "modified_modules,expected_impacted",
    [
        # Test single module modification
        (["module_d"], {"test_module1", "test_module2"}),
        # Test multiple module modifications
        (["module_b", "module_c"], {"test_module1", "test_module2"}),
        # Test no impact
        (["module_e"], {"test_module2"}),
        # Test dangling production module — conservatively marks all tests as impacted
        (["dangling_module"], {"test_module1", "test_module2"}),
    ],
)
def test_resolve_impacted_tests(sample_dep_tree, modified_modules, expected_impacted):
    """Test resolving impacted tests from modified modules.

    Args:
        sample_dep_tree: Fixture providing a sample dependency tree
        modified_modules: List of modules that were modified
        expected_impacted: Set of test modules expected to be impacted
    """
    impacted = graph.resolve_impacted_tests(modified_modules, sample_dep_tree)
    assert set(impacted) == expected_impacted


def test_resolve_impacted_tests_dangling_test_module(sample_dep_tree):
    """Test that a dangling test module is directly included as impacted."""
    impacted = graph.resolve_impacted_tests(["test_new_feature"], sample_dep_tree)
    assert "test_new_feature" in impacted


def test_resolve_impacted_tests_dangling_production_module(sample_dep_tree):
    """Test that a dangling production module causes all test modules to be impacted."""
    impacted = graph.resolve_impacted_tests(["unknown_prod_module"], sample_dep_tree)
    # Should include all test modules from the tree
    assert "test_module1" in impacted
    assert "test_module2" in impacted


def test_build_dep_tree():
    """Test building dependency tree from a package."""
    # Mock discovered submodules: name -> absolute file path
    mock_submodules = {
        "module_a": "/fake/module_a.py",
        "module_b": "/fake/module_b.py",
        "module_c": "/fake/module_c.py",
    }

    with (
        patch("pytest_impacted.graph.RUST_AVAILABLE", False),
        patch("pytest_impacted.graph._discover_project", return_value=_Discovered(mock_submodules, {}, {})),
        patch("pytest_impacted.graph.parse_file_imports") as mock_parse_imports,
    ):
        # Set up mock imports for each module
        mock_parse_imports.side_effect = [
            ["module_b"],  # module_a imports
            ["module_c"],  # module_b imports
            [],  # module_c imports
        ]

        dep_tree = graph.build_dep_tree("mock_package")

        # Verify the graph structure
        assert set(dep_tree.nodes()) == {"module_a", "module_b", "module_c"}
        assert dep_tree.has_edge("module_b", "module_a")  # Note: edges are inverted
        assert dep_tree.has_edge("module_c", "module_b")


def test_an_init_that_no_test_imports_from_impacts_nothing(tmp_path):
    """``import pkg.core`` runs ``pkg/__init__.py`` too, but is not linked to it (a documented limit)."""
    for rel, source in {"pkg/__init__.py": "", "pkg/core.py": "", "tests/test_core.py": "import pkg.core\n"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("pkg", tests_package="tests", root_dir=tmp_path)

    assert graph.resolve_impacted_tests(["pkg"], dep_tree) == []


def test_an_init_changed_beside_a_module_adds_no_tests_of_its_own(tmp_path):
    files = {
        "pkg/__init__.py": "",
        "pkg/core.py": "",
        "pkg/utils.py": "",
        "tests/test_core.py": "import pkg.core\n",
        "tests/test_utils.py": "import pkg.utils\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("pkg", tests_package="tests", root_dir=tmp_path)

    assert graph.resolve_impacted_tests(["pkg", "pkg.core"], dep_tree) == ["tests.test_core"]


def test_a_module_importing_from_its_package_root_links_its_tests_to_the_init(tmp_path):
    """``from . import VERSION`` in a top-level module is an import of ``app/__init__.py``."""
    files = {
        "app/__init__.py": "VERSION = 1\n",
        "app/core.py": "from . import VERSION\n",
        "tests/test_core.py": "from app.core import thing\n",
        "tests/test_other.py": "",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert graph.resolve_impacted_tests(["app"], dep_tree) == ["tests.test_core"]


def test_build_dep_tree_includes_root_conftest_with_paths(tmp_path):
    """A root conftest joins the graph, and every node records its source file."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").touch()
    (tmp_path / "app/db.py").write_text("")
    (tmp_path / "conftest.py").write_text("from app.db import connect\n")

    dep_tree = graph.build_dep_tree("app", root_dir=tmp_path)

    assert dep_tree.has_edge("app.db", "conftest")  # the graph is inverted: dependency -> dependent
    assert dep_tree.nodes["conftest"]["path"] == str((tmp_path / "conftest.py").resolve())
    assert dep_tree.nodes["app.db"]["path"] == str((tmp_path / "app/db.py").resolve())


def test_build_dep_tree_does_not_duplicate_a_conftest_inside_the_package(tmp_path):
    """Walking up from a tests dir inside the package passes a conftest pkgutil already named."""
    for rel in ("src/app/__init__.py", "src/app/conftest.py", "src/app/tests/test_a.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")

    dep_tree = graph.build_dep_tree("src/app", tests_package="src/app/tests", root_dir=tmp_path)

    assert "app.conftest" in dep_tree
    paths = [path for _, path in dep_tree.nodes(data="path")]
    assert len(paths) == len(set(paths)), sorted(dep_tree.nodes)


def test_ancestor_conftest_relative_imports_resolve_in_src_layout(tmp_path):
    """Named ``app.conftest`` (not ``src.app.conftest``), so ``.core.db`` matches the discovered module."""
    files = {
        "src/app/__init__.py": "",
        "src/app/conftest.py": "from .core.db import connect\n",
        "src/app/core/__init__.py": "",
        "src/app/core/db.py": "def connect(): ...\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("src/app/core", root_dir=tmp_path)

    assert dep_tree.has_edge("app.core.db", "app.conftest")


def test_ancestor_conftest_whose_short_name_clashes_keeps_its_full_name(tmp_path):
    """``tests/app/conftest.py`` would shorten to ``app.conftest``, already the package's own conftest.

    Dropping it on the clash would lose every edge from it, so it keeps its full path name.
    """
    files = {
        "src/app/__init__.py": "",
        "src/app/conftest.py": "",
        "src/app/core/__init__.py": "",
        "src/app/core/db.py": "",
        "tests/app/__init__.py": "",
        "tests/app/conftest.py": "import app.core.db\n",
        "tests/app/unit/test_x.py": "",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("src/app", tests_package="tests/app/unit", root_dir=tmp_path)

    assert dep_tree.nodes["app.conftest"]["path"] == str((tmp_path / "src/app/conftest.py").resolve())
    assert dep_tree.has_edge("app.core.db", "tests.app.conftest")


def test_pytest_plugins_declaration_is_an_edge(tmp_path):
    """A plugin module is imported by pytest on the conftest's behalf; tests never import it."""
    files = {
        "app/__init__.py": "",
        "app/db.py": "",
        "tests/fixtures.py": "from app.db import connect\n",
        "conftest.py": 'pytest_plugins = ["tests.fixtures", "pytester"]\n',
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("tests.fixtures", "conftest")
    assert nx.has_path(dep_tree, "app.db", "conftest")


def test_pytest_plugin_targets_are_flagged(tmp_path):
    """Flagged so the strategy can treat them as session-wide."""
    files = {
        "app/__init__.py": "",
        "tests/fixtures.py": "",
        "tests/test_a.py": 'pytest_plugins = "tests.fixtures"\n',
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.nodes["tests.fixtures"].get("pytest_plugin") is True
    assert not dep_tree.nodes["tests.test_a"].get("pytest_plugin")


def test_namespace_subpackage_modules_are_linked_by_absolute_and_relative_imports(tmp_path):
    """``processors/`` has no ``__init__.py``; its modules still import and are imported."""
    files = {
        "app/__init__.py": "",
        "app/processors/ocr.py": "from . import util\n",
        "app/processors/util.py": "",
        "tests/test_ocr.py": "from app.processors.ocr import run\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("app.processors.util", "app.processors.ocr")
    assert dep_tree.has_edge("app.processors.ocr", "tests.test_ocr")
    assert graph.resolve_impacted_tests(["app.processors.util"], dep_tree) == ["tests.test_ocr"]


def test_a_test_importing_from_the_package_root_sees_what_its_init_imports(tmp_path):
    """``from app import Thing`` runs ``app/__init__.py``, which imports ``app.core``: both edges count."""
    files = {
        "app/__init__.py": "from app.core import Thing\n",
        "app/core.py": "class Thing: ...\n",
        "tests/test_app.py": "from app import Thing\n",
        "tests/test_other.py": "",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("app.core", "app")
    assert dep_tree.has_edge("app", "tests.test_app")
    assert graph.resolve_impacted_tests(["app"], dep_tree) == ["tests.test_app"]
    assert graph.resolve_impacted_tests(["app.core"], dep_tree) == ["tests.test_app"]


def test_every_node_resolves_back_to_itself(tmp_path):
    """The graph and the changed-file resolver name every file alike: a node an edit cannot
    resolve to is a change that impacts nothing. Discovery knows every walked node, never an
    ``external`` one, which only the graph finds."""
    files = {
        "conftest.py": "",
        "backend/conftest.py": "from backend.app.db import connect\n",
        "backend/app/__init__.py": "",
        "backend/app/db.py": "import libs.shared\n",
        "libs/shared.py": "",
        "backend/app/ns/x.py": "",
        "backend/app/checks/test_in.py": "",
        "suite/conftest.py": "from backend.conftest import *\n",
        "suite/test_a.py": "",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)
    root = tmp_path.resolve()

    dep_tree = graph.build_dep_tree("backend/app", tests_package="suite", root_dir=tmp_path)

    assert dep_tree.nodes["libs.shared"]["external"]
    for node, path in dep_tree.nodes(data="path"):
        changed = str(Path(path).relative_to(root))
        assert graph.resolve_files_to_nodes([changed], dep_tree, root_dir=tmp_path) == [node], changed
        by_discovery = resolve_files_to_modules([changed], "backend/app", "suite", root_dir=tmp_path)
        assert by_discovery == ([] if dep_tree.nodes[node].get("external") else [node]), changed


def test_changed_files_resolve_to_the_graph_nodes_with_that_path(tmp_path):
    """Including a conftest above the package; a file created after the graph was cached is no node
    of it (``link_changed_files`` adds one to a run's copy)."""
    for rel in ("backend/conftest.py", "backend/app/__init__.py", "backend/app/db.py", "suite/test_a.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    dep_tree = cached_build_dep_tree("backend/app", "suite", root_dir=tmp_path)
    (tmp_path / "suite/test_new.py").touch()

    changed = ["backend/conftest.py", "backend/app/db.py", "suite/test_new.py", "gone.py", "README.md"]

    assert graph.resolve_files_to_nodes(changed, dep_tree, root_dir=tmp_path) == ["backend.conftest", "app.db"]


def test_a_conftest_named_like_one_in_the_package_keeps_its_edges(tmp_path):
    """Django's ``mysite/conftest.py`` beside ``mysite/mysite/conftest.py``: no importable name is
    free, so it is named with a leading dot, which no import spells, and keeps its own imports."""
    files = {
        "mysite/conftest.py": "from mysite.models import Poll\n",
        "mysite/mysite/__init__.py": "",
        "mysite/mysite/conftest.py": "from mysite.views import index\n",
        "mysite/mysite/models.py": "",
        "mysite/mysite/views.py": "",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("mysite/mysite", root_dir=tmp_path)

    assert dep_tree.nodes[".mysite.conftest"]["path"] == str((tmp_path / "mysite/conftest.py").resolve())
    assert dep_tree.has_edge("mysite.models", ".mysite.conftest")
    # Parsed apart, though both are parsed as ``mysite.conftest``: neither takes the other's imports.
    assert dep_tree.has_edge("mysite.views", "mysite.conftest")
    assert not dep_tree.has_edge("mysite.views", ".mysite.conftest")
    assert not dep_tree.has_edge("mysite.models", "mysite.conftest")
    assert graph.resolve_files_to_nodes(["mysite/conftest.py"], dep_tree, root_dir=tmp_path) == [".mysite.conftest"]


def test_a_conftest_with_no_free_name_keeps_its_relative_imports(tmp_path):
    """``x/conftest.py``'s only name, ``x.conftest``, is an alias of ``x/x/conftest.py``: it gets the last
    resort, but is still parsed as ``x.conftest``, so ``.x.tests.helpers`` resolves to the helper."""
    files = {
        "x/conftest.py": "from .x.tests.helpers import make\n",
        "x/x/conftest.py": "",
        "x/x/mod.py": "",
        "x/x/tests/helpers.py": "",
        "x/x/tests/test_a.py": "",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("x/x", tests_package="x/x/tests", root_dir=tmp_path)

    assert dep_tree.nodes[".x.conftest"]["path"] == str((tmp_path / "x/conftest.py").resolve())
    assert dep_tree.has_edge("x.x.tests.helpers", ".x.conftest")


def test_a_module_in_a_regular_package_reaches_its_conftest_by_relative_import(tmp_path):
    """``from ..conftest import helper`` in ``x/y/pkg/mod.py`` can only mean ``x/y/conftest.py``."""
    files = {
        "x/y/__init__.py": "",
        "x/y/conftest.py": "def helper(): ...\n",
        "x/y/pkg/__init__.py": "",
        "x/y/pkg/mod.py": "from ..conftest import helper\n",
        "y/conftest.py": "",
        "y/tests/test_a.py": "from y.pkg import mod\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("x/y/pkg", tests_package="y/tests", root_dir=tmp_path)

    conftest = graph.resolve_files_to_nodes(["x/y/conftest.py"], dep_tree, root_dir=tmp_path)
    assert graph.resolve_impacted_tests(conftest, dep_tree) == ["tests.test_a"]


CONTESTED = {
    "y/conftest.py": "from lib.util import thing\n",
    "y/lib/__init__.py": "",
    "y/lib/util.py": "thing = 1\n",
    "x/y/__init__.py": "",
    "x/y/conftest.py": "from lib.other import other\n",
    "y/lib/other.py": "other = 1\n",
    "x/y/tests/test_a.py": "from y.conftest import thing\n",
}


@pytest.mark.parametrize("symlinked", [False, True], ids=["plain", "x_y_is_a_symlink"])
@pytest.mark.parametrize("changed", ["lib.util", "lib.other"])
def test_an_import_of_a_contested_conftest_name_depends_on_every_conftest_it_can_mean(tmp_path, changed, symlinked):
    """``y.conftest`` is ``y/conftest.py`` with the root on ``sys.path`` and ``x/y/conftest.py`` with ``x/``:
    analysis cannot know which, so ``test_a`` depends on both, whichever took the name — named by the
    path it is reached by, also through a symlinked directory."""
    for rel, source in CONTESTED.items():
        if symlinked and rel.startswith("x/y/"):
            rel = "w/" + rel.removeprefix("x/y/")
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)
    if symlinked:
        (tmp_path / "x").mkdir()
        (tmp_path / "x/y").symlink_to("../w", target_is_directory=True)

    dep_tree = graph.build_dep_tree("y/lib", tests_package="x/y/tests", root_dir=tmp_path)

    assert graph.resolve_impacted_tests([changed], dep_tree) == ["tests.test_a"]


def test_tests_dir_conftests_sharing_a_name_do_not_select_each_others_tests(tmp_path):
    """Every service's ``tests/conftest.py`` could be imported as ``tests.conftest``; that must not tie
    one service's tests to another's conftest — they are not the contested conftests above the package."""
    for service in ("a", "b"):
        files = {
            f"services/{service}/tests/conftest.py": "def helper(): ...\n",
            f"services/{service}/tests/test_x.py": "from tests.conftest import helper\n",
        }
        for rel, source in files.items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("services", tests_package="services", root_dir=tmp_path)
    conftest = graph.resolve_files_to_nodes(["services/a/tests/conftest.py"], dep_tree, root_dir=tmp_path)

    assert not [test for test in graph.resolve_impacted_tests(conftest, dep_tree) if ".b." in test]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_an_unsearchable_directory_with_a_conftest_does_not_crash_the_graph(tmp_path):
    """Listable but not searchable (mode 644): discovery lists its conftest; nothing may raise on it."""
    for rel in ("app/__init__.py", "tests/test_a.py", "tests/data/conftest.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    locked = tmp_path / "tests/data"
    locked.chmod(0o644)
    try:
        dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)
    finally:
        locked.chmod(0o755)

    assert "tests.test_a" in dep_tree


def test_a_file_reached_under_two_names_is_one_node(tmp_path):
    """A tests dir inside the package is walked by both discoveries; its files must not be doubled."""
    files = {
        "app/__init__.py": "",
        "app/core.py": "",
        "app/tests/helpers.py": "import app.core\n",
        "app/tests/test_a.py": "from tests.helpers import make\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    dep_tree = graph.build_dep_tree("app", tests_package="app/tests", root_dir=tmp_path)

    paths = [dep_tree.nodes[node]["path"] for node in dep_tree.nodes]
    assert len(paths) == len(set(paths))
    # ``tests.helpers`` is an alias of ``app.tests.helpers``: the import still becomes an edge.
    assert "app.tests.test_a" in graph.resolve_impacted_tests(["app.core"], dep_tree)


def test_a_module_outside_the_analysed_dirs_that_they_import_is_a_node(tmp_path):
    """``testing/`` is neither the package nor the tests dir, but a conftest imports it: an edit
    there must reach the conftest, and what ``testing`` itself imports must reach it too."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core.py": "",
            "testing/factories.py": "from app.core import Thing\nfrom testing.base import Base\n",
            "testing/base.py": "",
            "tests/conftest.py": "from testing.factories import make\n",
            "tests/test_a.py": "import testing.factories\n",
        },
    )

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.nodes["testing.factories"]["path"] == str((tmp_path / "testing/factories.py").resolve())
    assert dep_tree.has_edge("testing.factories", "tests.conftest")
    assert dep_tree.has_edge("testing.factories", "tests.test_a")
    assert dep_tree.has_edge("app.core", "testing.factories")
    assert dep_tree.has_edge("testing.base", "testing.factories")


def test_a_module_beside_the_package_in_src_layout_is_a_node(tmp_path):
    """``src/`` is on ``sys.path`` for ``app``, so ``shared.x`` names ``src/shared/x.py``."""
    write_files(
        tmp_path,
        {
            "src/app/__init__.py": "",
            "src/app/core.py": "from shared.x import helper\n",
            "src/shared/x.py": "",
            "tests/test_core.py": "import app.core\n",
            "tests/test_other.py": "",
        },
    )

    dep_tree = graph.build_dep_tree("src/app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("shared.x", "app.core")
    assert graph.resolve_impacted_tests(["shared.x"], dep_tree) == ["tests.test_core"]


def test_a_local_package_named_like_a_standard_library_module_is_followed(tmp_path):
    """With the rootdir first on ``sys.path``, ``profile.util`` loads the project's ``profile/``:
    only modules Python loaded before the project's code ran are safe from it, and which those
    are depends on the process, so a local file is always followed."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core.py": "import json\nfrom profile.util import y\n\ndef f():\n    from profile import gone\n",
            "profile/__init__.py": "",
            "profile/util.py": "",
            "tests/test_core.py": "import app.core\n",
        },
    )

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("profile.util", "app.core")
    assert dep_tree.graph["unresolved"]["profile.gone"] == ["app.core"]


def test_a_pytest_plugin_outside_the_analysed_dirs_is_a_flagged_node(tmp_path):
    """A fixture plugin kept in ``testing/`` is loaded session-wide like any other plugin."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "testing/fixtures.py": "",
            "tests/conftest.py": 'pytest_plugins = ["testing.fixtures"]\n',
            "tests/test_a.py": "",
        },
    )

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("testing.fixtures", "tests.conftest")
    assert dep_tree.nodes["testing.fixtures"].get("pytest_plugin") is True


def test_imports_that_name_no_module_are_recorded_for_their_importers(tmp_path):
    """So a deleted module can be linked to what still imports it; a name inside a module file
    (``from app.core import thing``) cannot be a module. A standard-library name can: a deleted
    local module of that name shadowed it."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core.py": "import json\nfrom app import gone\n\ndef lazy():\n    import app.old\n",
            "tests/test_a.py": "from app.core import thing\n",
        },
    )

    unresolved = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path).graph["unresolved"]

    assert unresolved["app.gone"] == ["app.core"]
    assert unresolved["app.old"] == ["app.core"]
    assert unresolved["json"] == ["app.core"]
    assert "app.core.thing" not in unresolved
    assert "app.core" not in unresolved  # a module the walks name


@pytest.mark.parametrize(
    ("package", "prefix"), [pytest.param("app", "", id="flat"), pytest.param("src/app", "src/", id="src_layout")]
)
def test_a_deleted_module_is_linked_to_what_still_imports_it(tmp_path, package, prefix):
    write_files(
        tmp_path,
        {
            f"{prefix}app/__init__.py": "",
            f"{prefix}app/core.py": "def lazy():\n    from app import helpers\n",
            "tests/test_core.py": "import app.core\n",
            "tests/test_other.py": "",
        },
    )
    dep_tree = graph.build_dep_tree(package, tests_package="tests", root_dir=tmp_path).copy()

    linked = graph.link_changed_files([f"{prefix}app/helpers.py"], dep_tree, root_dir=tmp_path)

    assert linked == ["app.helpers"]
    assert dep_tree.nodes["app.helpers"]["path"] == str(tmp_path.resolve() / f"{prefix}app/helpers.py")
    assert graph.resolve_files_to_nodes([f"{prefix}app/helpers.py"], dep_tree, root_dir=tmp_path) == ["app.helpers"]
    assert graph.resolve_impacted_tests(["app.helpers"], dep_tree) == ["tests.test_core"]


def test_a_changed_file_nothing_imports_is_linked_to_nothing(tmp_path):
    """A script outside the analysed dirs gets a node with no dependents; files the graph has,
    deleted files nothing imports, files outside the rootdir and non-Python files get none."""
    write_files(tmp_path, {"project/app/__init__.py": "", "project/scripts/deploy.py": "", "other/x.py": ""})
    root = tmp_path / "project"
    dep_tree = graph.build_dep_tree("app", root_dir=root).copy()

    linked = graph.link_changed_files(
        ["scripts/deploy.py", "app/__init__.py", "scripts/gone.py", "../other/x.py", "README.md"],
        dep_tree,
        root_dir=root,
    )

    assert linked == ["scripts.deploy"]
    assert not list(dep_tree.successors("scripts.deploy"))


def test_one_name_found_under_two_import_roots_links_both_files(tmp_path):
    """``import shared`` could load ``shared.py`` or ``src/shared.py``, depending on ``sys.path``."""
    write_files(
        tmp_path,
        {
            "src/app/__init__.py": "",
            "src/app/core.py": "import shared\n",
            "shared.py": "",
            "src/shared.py": "",
            "tests/test_core.py": "import app.core\n",
        },
    )

    dep_tree = graph.build_dep_tree("src/app", tests_package="tests", root_dir=tmp_path)

    shared = {node for node, path in dep_tree.nodes(data="path") if path and Path(path).name == "shared.py"}
    assert len(shared) == 2
    assert all(dep_tree.has_edge(node, "app.core") for node in shared)


def test_a_file_found_under_another_name_is_an_alias_of_its_node(tmp_path):
    """The tests-dir walk names ``suite/unit/helpers.py`` ``unit.helpers``; imported as
    ``suite.unit.helpers``, that spelling joins its aliases, as ``-p`` lookups expect."""
    write_files(
        tmp_path,
        {"app/__init__.py": "", "suite/unit/helpers.py": "", "suite/unit/test_a.py": "import suite.unit.helpers\n"},
    )

    dep_tree = graph.build_dep_tree("app", tests_package="suite/unit", root_dir=tmp_path)

    assert dep_tree.graph["aliases"]["suite.unit.helpers"] == "unit.helpers"
    assert dep_tree.has_edge("unit.helpers", "unit.test_a")


def test_a_package_directory_wins_over_a_module_file_of_the_same_name(tmp_path):
    """As in Python: ``import shared`` loads ``shared/__init__.py`` when ``shared.py`` is beside it."""
    write_files(
        tmp_path,
        {"app/__init__.py": "", "app/core.py": "import shared\n", "shared/__init__.py": "", "shared.py": ""},
    )

    dep_tree = graph.build_dep_tree("app", root_dir=tmp_path)

    assert dep_tree.nodes["shared"]["path"] == str((tmp_path / "shared/__init__.py").resolve())


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_a_module_linked_in_from_outside_the_project_is_no_node(tmp_path):
    """Git reports no change to a file outside the project, so it would only be an unreachable node."""
    project = tmp_path / "project"
    write_files(project, {"app/__init__.py": "", "app/core.py": "import vendored.lib\n"})
    write_files(tmp_path, {"elsewhere/lib.py": ""})
    (project / "vendored").symlink_to(tmp_path / "elsewhere")

    dep_tree = graph.build_dep_tree("app", root_dir=project)

    assert "vendored.lib" not in dep_tree


@pytest.mark.parametrize(
    ("package", "tests_dir", "files", "deleted", "importer"),
    [
        pytest.param(
            "app",
            "backend/tests",
            {"app/__init__.py": "", "backend/tests/test_a.py": "def test_a():\n    from tests.helpers import h\n"},
            "backend/tests/helpers.py",
            "tests.test_a",
            id="a_nested_tests_dir_walked_as_tests",
        ),
        pytest.param(
            "src/app",
            "tests",
            {"src/app/core.py": "def f():\n    from app import utils\n", "tests/test_core.py": "import app.core\n"},
            "src/app/utils.py",
            "src.app.core",
            id="a_namespace_package_in_src",
        ),
    ],
)
def test_a_deleted_module_is_named_the_way_the_walks_name_its_neighbours(
    tmp_path, package, tests_dir, files, deleted, importer
):
    """The names a changed file can have are rooted where the walks root theirs."""
    write_files(tmp_path, files)
    dep_tree = graph.build_dep_tree(package, tests_package=tests_dir, root_dir=tmp_path).copy()

    (linked,) = graph.link_changed_files([deleted], dep_tree, root_dir=tmp_path)

    assert dep_tree.has_edge(linked, importer)


def test_a_module_beside_a_namespace_package_in_src_is_a_node(tmp_path):
    """``src/app`` has no ``__init__.py``, yet it imports as ``app``: ``src/`` is on ``sys.path``."""
    write_files(
        tmp_path, {"src/app/core.py": "import shared\n", "src/shared.py": "", "tests/test_core.py": "import app.core\n"}
    )

    dep_tree = graph.build_dep_tree("src/app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.nodes["shared"]["path"] == str((tmp_path / "src/shared.py").resolve())


def test_a_linked_file_takes_no_name_already_in_use(tmp_path):
    """``tests.x`` is an alias of ``app/tests/x.py``: a changed ``tests/x.py`` must not take it,
    nor the last-resort name another node already has."""
    write_files(tmp_path, {"app/__init__.py": "", "app/tests/x.py": "", "tests/x.py": "", "other/x.py": ""})
    dep_tree = graph.build_dep_tree("app", tests_package="app/tests", root_dir=tmp_path).copy()
    dep_tree.add_node("other.x", path="/elsewhere/x.py")
    dep_tree.add_node(".other.x", path="/elsewhere/x/__init__.py")

    linked = graph.link_changed_files(["tests/x.py", "other/x.py"], dep_tree, root_dir=tmp_path)

    assert dep_tree.graph["aliases"]["tests.x"] == "app.tests.x"  # left alone
    names_in_use = set(dep_tree.graph["aliases"]) | {"app.tests.x", "other.x", ".other.x"}
    assert len(linked) == 2 and not names_in_use & set(linked)
    assert dep_tree.nodes[".other.x"]["path"] == "/elsewhere/x/__init__.py"


def test_a_file_found_after_an_alias_of_its_name_gets_its_own_name(tmp_path):
    """``backend.tests.helpers`` finds the walked ``backend/tests/helpers.py`` (named ``tests.helpers``)
    under the rootdir and a second file under ``backend/``: that one must not take the alias."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "backend/tests/helpers.py": "",
            "backend/tests/test_a.py": "import backend.tests.helpers\n",
            "backend/backend/tests/helpers.py": "",
        },
    )

    dep_tree = graph.build_dep_tree("app", tests_package="backend/tests", root_dir=tmp_path)

    aliases = dep_tree.graph["aliases"]
    assert not set(aliases) & set(dep_tree.nodes)
    assert len({path for _, path in dep_tree.nodes(data="path") if path and path.endswith("helpers.py")}) == 2


def test_a_name_that_named_a_new_file_never_becomes_an_alias(tmp_path):
    """The other order: the first root finds a new file, named ``x``; the second finds a walked one."""
    write_files(tmp_path, {"a/x.py": "", "b/x.py": ""})
    walked = str((tmp_path / "b/x.py").resolve())
    linker = graph._Linker(_Discovered({"b.x": walked}, {}, {}), [tmp_path / "a", tmp_path / "b"], tmp_path.resolve())

    assert linker.targets("x") == ["x", "b.x"]
    assert "x" not in linker.aliases


@pytest.mark.parametrize("metadata", ["top_level.txt", "RECORD"])
def test_a_distribution_installed_into_the_project_is_not_followed(tmp_path, metadata):
    """``pip install -t .`` puts ``vendored/`` and its ``.dist-info`` in the rootdir: third-party code.
    A wheel without ``top_level.txt`` (flit, hatchling) lists its files in ``RECORD``."""
    record = "vendored/__init__.py,sha256=x,0\nvendored/deep.py,sha256=y,0\nsingle.py,sha256=z,0\n"
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core.py": "import vendored\nimport single\n",
            "single.py": "",
            "vendored/__init__.py": "import vendored.deep\n",
            "vendored/deep.py": "",
            "vendored-1.0.dist-info/METADATA": "Name: vendored\n",
            f"vendored-1.0.dist-info/{metadata}": "vendored\nsingle\n" if metadata == "top_level.txt" else record,
        },
    )

    dep_tree = graph.build_dep_tree("app", root_dir=tmp_path)

    assert not [node for node in dep_tree if node.startswith(("vendored", "single"))]


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_a_name_reached_through_a_symlink_roots_no_import_root(tmp_path):
    """``pkg.alias.mod`` names ``shared/mod.py`` through ``pkg/alias -> ../shared``: that name does
    not spell the file's path, so it says nothing about which directory is on ``sys.path``."""
    write_files(tmp_path, {"pkg/__init__.py": "", "shared/mod.py": "", "tests/test_a.py": ""})
    (tmp_path / "pkg/alias").symlink_to(tmp_path / "shared")

    dep_tree = graph.build_dep_tree("pkg", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.graph["import_roots"] == [str(tmp_path.resolve())]


def test_other_names_of_a_linked_file_are_aliases(tmp_path):
    """Named ``myplugin`` under ``backend/`` (the tests walk's root), still ``-p backend.myplugin``."""
    write_files(tmp_path, {"app/__init__.py": "", "backend/tests/test_a.py": "", "backend/myplugin.py": ""})
    dep_tree = graph.build_dep_tree("app", tests_package="backend/tests", root_dir=tmp_path).copy()

    (node,) = graph.link_changed_files(["backend/myplugin.py"], dep_tree, root_dir=tmp_path)

    assert {dep_tree.graph["aliases"].get(name, name) for name in ("myplugin", "backend.myplugin")} == {node}


@pytest.mark.parametrize(
    ("package", "tests_dir", "files", "found"),
    [
        pytest.param(
            "app",
            "qa/src/tests",
            {
                "qa/src/tests/__init__.py": "",
                "qa/src/tests/test_a.py": "from src.support import helper\n",
                "qa/src/support.py": "",
            },
            "src.support",
            id="between_the_rootdir_and_a_tests_dir",
        ),
        pytest.param(
            "src/app",
            "tests",
            {"packages/app/__init__.py": "", "packages/app/core.py": "import shared\n", "src/shared.py": ""},
            "shared",
            id="above_a_symlinked_package",
        ),
    ],
)
def test_every_directory_an_import_can_resolve_from_is_a_root(tmp_path, package, tests_dir, files, found):
    write_files(tmp_path, {"app/__init__.py": "", "tests/test_a.py": "", **files})
    if package == "src/app":
        (tmp_path / "src/app").symlink_to(tmp_path / "packages/app")

    dep_tree = graph.build_dep_tree(package, tests_package=tests_dir, root_dir=tmp_path)

    assert found in dep_tree


def test_a_regular_package_between_the_rootdir_and_a_naming_root_is_no_import_root(tmp_path):
    """``app/sub/tests`` is walked as ``tests.x``, rooted at ``app/sub/``; ``app/`` above it is a package,
    so ``import types`` is not ``app/types.py``."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/types.py": "",
            "app/sub/__init__.py": "",
            "app/sub/tests/test_a.py": "import types\n",
        },
    )

    dep_tree = graph.build_dep_tree("app", tests_package="app/sub/tests", root_dir=tmp_path)

    assert str((tmp_path / "app").resolve()) not in dep_tree.graph["import_roots"]
    assert not dep_tree.has_edge("app.types", "app.sub.tests.test_a")


def test_a_deleted_module_of_a_local_package_named_like_the_stdlib_is_linked(tmp_path):
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core.py": "def f():\n    from platform.auth import login\n",
            "platform/__init__.py": "",
            "tests/test_core.py": "import app.core\n",
        },
    )
    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path).copy()

    (linked,) = graph.link_changed_files(["platform/auth.py"], dep_tree, root_dir=tmp_path)

    assert dep_tree.has_edge(linked, "app.core")


def test_a_naming_root_inside_a_regular_package_is_kept(tmp_path):
    """``app/tests`` is walked as ``tests.x``, rooted at ``app/``: with ``pythonpath = app`` a deleted
    ``tests.fixtures_data`` and ``import settings_local`` resolve from there, package or not."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/settings_local.py": "",
            "app/tests/test_a.py": "def test_a():\n    from tests.fixtures_data import X\n",
            "app/tests/test_b.py": "import settings_local\n",
        },
    )
    dep_tree = graph.build_dep_tree("app", tests_package="app/tests", root_dir=tmp_path).copy()

    (linked,) = graph.link_changed_files(["app/tests/fixtures_data.py"], dep_tree, root_dir=tmp_path)

    assert dep_tree.has_edge(linked, "app.tests.test_a")
    assert dep_tree.has_edge("app.settings_local", "app.tests.test_b")


def test_an_analysed_directory_given_as_an_absolute_path_does_not_crash(tmp_path):
    write_files(tmp_path, {"app/__init__.py": "", "tests/__init__.py": "", "tests/test_a.py": ""})

    tests_package = path_to_package_name(str(tmp_path / "tests"))  # as the API names ``--impacted-tests-dir``

    dep_tree = graph.build_dep_tree("app", tests_package=tests_package, root_dir=tmp_path)

    assert str(tmp_path.resolve()) in dep_tree.graph["import_roots"]


@pytest.mark.parametrize(
    ("tests_dir", "files", "deleted", "importer"),
    [
        pytest.param(
            "app/tests",
            {"app/__init__.py": "", "app/settings.py": "", "app/tests/test_a.py": "import settings\n"},
            "settings.py",
            "app.tests.test_a",
            id="a_name_another_root_resolves",
        ),
        pytest.param(
            "tests/unit",
            {
                "app/__init__.py": "",
                "app/core.py": "def f():\n    from utils import strings\n",
                "utils/other.py": "",
                "tests/__init__.py": "",
                "tests/utils.py": "",
                "tests/unit/__init__.py": "",
                "tests/unit/test_a.py": "import app.core\n",
            },
            "utils/strings.py",
            "app.core",
            id="a_submodule_of_a_name_another_root_resolves_to_a_file",
        ),
    ],
)
def test_a_deleted_module_is_linked_even_when_another_root_has_its_name(tmp_path, tests_dir, files, deleted, importer):
    """Which file ``import settings`` loads depends on ``sys.path``: one found elsewhere must not hide
    the import from a deleted file of that name."""
    write_files(tmp_path, files)
    dep_tree = graph.build_dep_tree("app", tests_package=tests_dir, root_dir=tmp_path).copy()

    (linked,) = graph.link_changed_files([deleted], dep_tree, root_dir=tmp_path)

    assert dep_tree.has_edge(linked, importer)


def test_a_test_file_an_extension_adds_is_judged_by_its_file_name(tmp_path):
    """Without a ``test`` attribute, an ``external`` node falls back to its file's name, and a stem with
    dots (``test_flow.v2.py``, collectable with ``--import-mode=importlib``) is still ``test_``-prefixed."""
    dep_tree = nx.DiGraph()
    dep_tree.add_node("mypkg.generated_cases", path=str(tmp_path / "gen/test_generated_cases.py"), external=True)
    dep_tree.add_node("checks.flow", path=str(tmp_path / "checks/test_flow.v2.py"), external=True)
    dep_tree.add_node("mypkg.helper", path=str(tmp_path / "gen/helper.py"), external=True)

    assert graph.is_test_node(dep_tree, "mypkg.generated_cases")
    assert graph.is_test_node(dep_tree, "checks.flow")
    assert not graph.is_test_node(dep_tree, "mypkg.helper")


def test_a_test_module_outside_the_tests_walk_that_a_test_imports_is_itself_a_test(tmp_path):
    write_files(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "",
            "tests/unit/test_child.py": "from tests.integration.test_base import Base\n",
            "tests/integration/test_base.py": "import pkg.core\n",
        },
    )

    dep_tree = graph.build_dep_tree("pkg", tests_package="tests/unit", root_dir=tmp_path)

    assert graph.resolve_impacted_tests(["pkg.core"], dep_tree) == ["tests.integration.test_base", "unit.test_child"]


def test_a_deleted_module_that_only_an_external_module_imports_is_linked(tmp_path):
    """Imports by a module outside the walks are recorded too."""
    write_files(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "testing/factories.py": "def make():\n    from testing import base\n",
            "tests/test_a.py": "import testing.factories\n",
            "tests/test_b.py": "",
        },
    )
    dep_tree = graph.build_dep_tree("pkg", tests_package="tests", root_dir=tmp_path).copy()

    (linked,) = graph.link_changed_files(["testing/base.py"], dep_tree, root_dir=tmp_path)

    assert graph.resolve_impacted_tests([linked], dep_tree) == ["tests.test_a"]


def test_a_module_created_after_caching_is_linked_to_what_imports_it(tmp_path):
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core.py": "def f():\n    from app import helpers\n",
            "tests/test_core.py": "import app.core\n",
        },
    )
    cached = cached_build_dep_tree("app", "tests", root_dir=tmp_path)
    (tmp_path / "app/helpers.py").touch()
    dep_tree = run_copy(cached)

    (linked,) = graph.link_changed_files(["app/helpers.py"], dep_tree, root_dir=tmp_path)

    assert graph.resolve_impacted_tests([linked], dep_tree) == ["tests.test_core"]


def test_clearing_the_caches_forgets_directory_listings(tmp_path):
    """A rebuild after ``clear_dep_tree_cache`` sees a top-level module created since."""
    write_files(tmp_path, {"app/__init__.py": "", "app/core.py": "import shared\n"})
    assert "shared" not in cached_build_dep_tree("app", root_dir=tmp_path)

    (tmp_path / "shared.py").touch()
    clear_dep_tree_cache()

    assert "shared" in cached_build_dep_tree("app", root_dir=tmp_path)


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"myproject.egg-info/top_level.txt": "app\nshared\n"}, id="a_development_install_egg_info"),
        pytest.param({"broken-1.0.dist-info/METADATA": ""}, id="a_dist_info_without_top_level_or_record"),
    ],
)
def test_metadata_that_names_no_installed_distribution_hides_nothing(tmp_path, metadata):
    """An ``.egg-info`` describes the project's own packages; a bare ``.dist-info`` lists nothing."""
    write_files(tmp_path, {"app/__init__.py": "", "app/core.py": "import shared\n", "shared.py": "", **metadata})

    assert "shared" in graph.build_dep_tree("app", root_dir=tmp_path)


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_an_import_root_that_cannot_be_listed_raises_nothing(tmp_path):
    write_files(tmp_path, {"app/__init__.py": "", "qa/src/tests/test_a.py": "import shared\n"})
    (tmp_path / "qa").chmod(0o311)  # searchable, not listable
    try:
        dep_tree = graph.build_dep_tree("app", tests_package="qa/src/tests", root_dir=tmp_path)
    finally:
        (tmp_path / "qa").chmod(0o755)

    assert "tests.test_a" in dep_tree
