"""Imports made by name are imports: in the graph, through the pipeline, and in a real run.

``importlib.import_module("app.target")`` and ``mock.patch("app.target.VALUE")`` import ``app.target`` as an
import statement does (see :mod:`pytest_impacted.dynamic_imports`), so a project using them must select
exactly what the same project written with the import statement selects.
"""

import os
from pathlib import Path

import pytest

from pytest_impacted import _rust, api, backends, graph

from .git_helpers import INI, assert_selected, edit_file, run, write_files


pytestmark = pytest.mark.usefixtures("by_name_backend")  # every test, with each backend


def build(tmp_path, files):
    write_files(tmp_path, {"app/__init__.py": "", **files})
    return graph.build_dep_tree("app", tests_package="suite", root_dir=tmp_path)


def imports_of(dep_tree, module):
    """The modules *module* imports: the graph runs from each module to its importers."""
    return set(dep_tree.predecessors(module))


TARGET = "VALUE = 1\nTABLE = {}\n\nclass Model:\n    def save(self):\n        pass\n"


def test_the_backend_finds_the_imports_by_name(tmp_path, monkeypatch, by_name_backend):
    """Not vacuous: the backend each test here is parametrized with is the one that reads the files."""
    where = (
        (_rust, "rust_parse_all_dynamic_imports") if by_name_backend == "rust" else (backends, "parse_dynamic_imports")
    )
    real, calls = getattr(*where), []
    monkeypatch.setattr(*where, lambda *args: calls.append(args) or real(*args))

    dep_tree = build(
        tmp_path,
        {"app/target.py": TARGET, "app/loader.py": "import importlib\nimportlib.import_module('app.target')\n"},
    )

    assert calls
    assert imports_of(dep_tree, "app.loader") == {"app.target"}


def test_a_module_named_is_a_plain_edge(tmp_path):
    loader = "import importlib\n\ndef load():\n    return importlib.import_module('app.target')\n"
    dep_tree = build(tmp_path, {"app/target.py": TARGET, "app/loader.py": loader})

    assert imports_of(dep_tree, "app.loader") == {"app.target"}
    assert dep_tree.edges["app.target", "app.loader"] == {}  # placed and linked as an import statement's


PACKAGE_WITH_SUBPACKAGE = {
    "app/target.py": TARGET,
    "app/sub/__init__.py": "from app.sub.mod import func\n",
    "app/sub/mod.py": "def func():\n    pass\n",
}


def patching(call):
    return {"suite/test_patching.py": f"from unittest import mock\n\n@{call}\ndef test_it(m):\n    pass\n"}


@pytest.mark.parametrize(
    ("call", "modules"),
    [
        ("mock.patch('app.target.VALUE')", {"app.target"}),
        ("mock.patch('app.target.Model.save')", {"app.target"}),  # the longest prefix that is a module
        ("mock.patch('app.sub.mod.func')", {"app.sub.mod"}),  # not the subpackage, whose __init__ re-exports
        ("mock.patch('app.CONSTANT')", {"app"}),  # an attribute of the package's own __init__
        ("mock.patch('app.sub')", {"app", "app.sub"}),  # the attribute sub of app: from app import sub
        ("mock.patch.multiple('app.sub', func=None)", {"app.sub"}),  # a whole object path, here a module
        ("mock.patch.dict('app.target.TABLE')", {"app.target"}),
    ],
)
def test_an_object_path_is_an_import_of_its_module(tmp_path, call, modules):
    dep_tree = build(tmp_path, {**PACKAGE_WITH_SUBPACKAGE, **patching(call)})

    assert imports_of(dep_tree, "suite.test_patching") == modules


def test_an_attribute_s_module_is_the_path_before_it(tmp_path):
    """``patch`` replaces the attribute ``helper`` on ``app.utils``, which Python imports from ``app/utils.py``
    even beside an ``app/utils/`` holding ``helper.py``: the same as ``from app.utils import helper``."""
    files = {"app/utils.py": "def helper():\n    pass\n", "app/utils/helper.py": ""}
    by_name = build(tmp_path / "by_name", {**files, **patching("mock.patch('app.utils.helper')")})
    statement = {"suite/test_patching.py": "from app.utils import helper\n\ndef test_it():\n    pass\n"}
    by_statement = build(tmp_path / "by_statement", {**files, **statement})

    assert imports_of(by_name, "suite.test_patching") == imports_of(by_statement, "suite.test_patching")
    assert "app.utils" in imports_of(by_name, "suite.test_patching")


@pytest.mark.parametrize(
    "call",
    [
        "mock.patch('os.environ')",
        "mock.patch.dict('os.environ', {})",
        "mock.patch.dict('sys.modules', {})",
        "mock.patch('builtins.open')",
        "mock.patch('requests.get')",
    ],
)
def test_a_module_outside_the_project_adds_no_node(tmp_path, call):
    """Most patch targets are the standard library's or a distribution's: the project has no such module."""
    test = f"from unittest import mock\n\ndef test_it():\n    with {call}:\n        pass\n"
    dep_tree = build(tmp_path, {"app/target.py": TARGET, "suite/test_patching.py": test})

    assert set(dep_tree) == {"app", "app.target", "suite.test_patching"}
    assert imports_of(dep_tree, "suite.test_patching") == set()


def test_a_missing_module_is_kept_for_a_deleted_file(tmp_path):
    """As for an import statement: a module deleted since is linked to what names it (link_changed_files)."""
    loader = (
        "import importlib\nfrom unittest import mock\n\n"
        "importlib.import_module('app.gone')\nmock.patch('app.also_gone.Model.save')\n"
    )
    unresolved = build(tmp_path, {"app/loader.py": loader}).graph["unresolved"]

    assert unresolved["app.gone"] == ["app.loader"]
    assert unresolved["app.also_gone"] == ["app.loader"]


def test_a_path_no_prefix_of_which_is_a_module_is_kept_by_its_parent(tmp_path):
    """No module of the project starts it, as for a top-level module deleted since: ``from helpers import func``."""
    test = "from unittest import mock\n\n@mock.patch('helpers.func')\ndef test_it(m):\n    pass\n"
    unresolved = build(tmp_path, {"suite/test_patching.py": test}).graph["unresolved"]

    assert unresolved["helpers"] == ["suite.test_patching"]


def test_an_object_path_s_module_may_be_one_beside_the_test(tmp_path):
    """pytest puts a rootless test directory on ``sys.path``: ``helpers`` is ``suite/helpers.py`` there, as for
    ``import helpers`` (a ``basedir`` edge, see ``_Linker.local_targets``)."""
    files = {
        "suite/helpers.py": "class Factory:\n    def make(self):\n        pass\n",
        "suite/test_patching.py": (
            "from unittest import mock\n\n@mock.patch('helpers.Factory.make')\ndef test_it(m):\n    pass\n"
        ),
    }
    dep_tree = build(tmp_path, files)

    assert imports_of(dep_tree, "suite.test_patching") == {"suite.helpers"}
    assert dep_tree.edges["suite.helpers", "suite.test_patching"] == {"basedir": True}


def test_an_object_path_s_module_outside_the_walks_is_its_longest_prefix(tmp_path):
    """``shared.factories`` is found on disk under ``tests/``, where the tests dir's names are rooted, though no
    walk names it. The path's module is it, not the ``shared`` package above, whose ``__init__`` imports whatever
    it re-exports."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "tests/conftest.py": "",
            "tests/shared/__init__.py": "from shared.factories import Factory\n",
            "tests/shared/factories.py": "class Factory:\n    def make(self):\n        pass\n",
            "tests/unit/test_patching.py": (
                "from unittest import mock\n\n@mock.patch('shared.factories.Factory.make')\ndef test_it(m):\n    pass\n"
            ),
        },
    )
    dep_tree = graph.build_dep_tree("app", tests_package="tests/unit", root_dir=tmp_path)
    files = {node: Path(path).relative_to(tmp_path).as_posix() for node, path in dep_tree.nodes(data="path")}
    (test,) = (node for node, file in files.items() if file == "tests/unit/test_patching.py")

    assert {files[node] for node in imports_of(dep_tree, test)} == {"tests/shared/factories.py"}


def test_a_module_found_on_disk_joins_the_graph(tmp_path):
    """A module no walk finds is looked up under the import roots, as for an import statement."""
    files = {
        "helpers/__init__.py": "",
        "helpers/factories.py": "def make():\n    pass\n",
        "suite/test_made.py": "import pytest\n\nfactories = pytest.importorskip('helpers.factories')\n",
    }
    dep_tree = build(tmp_path, files)

    assert imports_of(dep_tree, "suite.test_made") == {"helpers.factories"}
    assert dep_tree.nodes["helpers.factories"]["external"]


# Each form of import by name, and the import statement that imports the same: the two must select alike.
FORMS = {
    "import_module": ("import importlib\nimportlib.import_module('app.target')", "import app.target"),
    "import_module, relative": (
        "from importlib import import_module\nimport_module('.target', 'app')",
        "import app.target",
    ),
    "__import__": ("__import__('app.target')", "import app.target"),
    "__import__, fromlist": ("__import__('app', fromlist=['target'])", "from app import target"),
    "run_module": ("import runpy\nrunpy.run_module('app.target')", "import app.target"),
    "importorskip": ("import pytest\npytest.importorskip('app.target')", "import app.target"),
    "resolve_name, colon": ("import pkgutil\npkgutil.resolve_name('app.target:VALUE')", "import app.target"),
    "resolve_name, dotted": (
        "import pkgutil\npkgutil.resolve_name('app.target.VALUE')",
        "from app.target import VALUE",
    ),
    "patch": ("from unittest import mock\nmock.patch('app.target.VALUE')", "from app.target import VALUE"),
    "patch, a submodule": ("from unittest import mock\nmock.patch('app.target')", "from app import target"),
    "patch, nested": ("from unittest import mock\nmock.patch('app.target.Model.save')", "from app.target import Model"),
    "patch.dict": (
        "from unittest import mock\nmock.patch.dict('app.target.TABLE', {})",
        "from app.target import TABLE",
    ),
    "patch.multiple": ("from unittest import mock\nmock.patch.multiple('app.target', VALUE=2)", "import app.target"),
    "mocker.patch": ("def f(mocker):\n    mocker.patch('app.target.VALUE')", "from app.target import VALUE"),
    "monkeypatch.setattr": (
        "def f(monkeypatch):\n    monkeypatch.setattr('app.target.VALUE', 2)",
        "from app.target import VALUE",
    ),
    "monkeypatch.delattr": (
        "def f(monkeypatch):\n    monkeypatch.delattr('app.target.VALUE')",
        "from app.target import VALUE",
    ),
}
PLACES = {"application code": "app/loader.py", "a test": "suite/test_consumer.py", "a conftest": "suite/conftest.py"}
OTHER = {"suite/test_other.py": "def test_other():\n    pass\n"}  # an unrelated test, never selected
PROJECT = {
    "app/__init__.py": "",
    "app/target.py": TARGET,
    "app/loader.py": "",
    "suite/conftest.py": "",
    "suite/test_consumer.py": "import app.loader\n\ndef test_consumer():\n    pass\n",
    **OTHER,
}


@pytest.fixture
def selection(monkeypatch):
    """Factory: the test files ``get_impacted_tests`` selects in a project of *files* for *change* ("edit" or
    "delete") to *changed*. No git: the change is handed to the pipeline as git would report it."""

    def select(root, files, changed, change, conftest_imports=False):
        write_files(root, files)
        if change == "delete":
            (root / changed).unlink()
        monkeypatch.setattr(api, "find_impacted_files_in_repo", lambda *args, **kwargs: [changed])
        found = api.get_impacted_tests("unstaged", "main", root, "app", "suite", conftest_imports=conftest_imports)
        return sorted(Path(os.path.relpath(path, root)).as_posix() for path in found or ())

    return select


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("place", PLACES)
@pytest.mark.parametrize("change", ["edit", "delete"])
@pytest.mark.parametrize("conftest_imports", [False, True], ids=["", "conftest-imports"])
def test_an_import_by_name_selects_as_the_import_statement_does(
    tmp_path, selection, form, place, change, conftest_imports
):
    def written_with(statement):
        return {**PROJECT, PLACES[place]: PROJECT[PLACES[place]] + statement + "\n"}

    dynamic, static = FORMS[form]

    by_name = selection(tmp_path / "by_name", written_with(dynamic), "app/target.py", change, conftest_imports)
    by_statement = selection(tmp_path / "by_statement", written_with(static), "app/target.py", change, conftest_imports)

    assert by_name == by_statement
    if place != "a conftest":
        assert "suite/test_consumer.py" in by_name  # not vacuous: the import selects its importer's test


LAYOUTS = {  # where the patched module lives, and the name the test imports it by
    "a regular package": ("app/target.py", "app.target"),
    "a namespace package": ("app/ns/target.py", "app.ns.target"),
    "beside the tests": ("suite/target.py", "target"),
}


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("change", ["edit", "delete"])
def test_a_nested_path_selects_as_the_import_statement_does(tmp_path, selection, layout, change):
    """``patch("target.Model.save")``: which part is the module, only the import statement can tell. A deleted
    one is no prefix the project resolves, yet must still reach the test, as ``from target import Model`` does."""
    changed, module = LAYOUTS[layout]

    def written_with(test):
        return {"app/__init__.py": "", changed: TARGET, "suite/test_consumer.py": test, **OTHER}

    by_name = selection(
        tmp_path / "by_name",
        written_with(
            f"from unittest import mock\n\n@mock.patch('{module}.Model.save')\ndef test_consumer(m):\n    pass\n"
        ),
        changed,
        change,
    )
    by_statement = selection(
        tmp_path / "by_statement",
        written_with(f"from {module} import Model\n\ndef test_consumer():\n    pass\n"),
        changed,
        change,
    )

    assert by_name == by_statement == ["suite/test_consumer.py"]


# Real runs: the dependency an import by name records is one the test has.
CONSUMER = "from app.loader import load\n\ndef test_value():\n    assert load() == 1\n"


@pytest.fixture
def loader_project(make_git_project):
    """Factory: a project whose ``app/loader.py`` is *loader*, imported by one test, beside an unrelated one."""

    def make(loader):
        files = {
            "app/__init__.py": "",
            "app/target.py": "VALUE = 1\n",
            "app/loader.py": loader,
            "suite/test_consumer.py": CONSUMER,
            **OTHER,
        }
        return make_git_project(files, INI)

    return make


LOADERS = {
    "import_module": "import importlib\n\ndef load():\n    return importlib.import_module('app.target').VALUE\n",
    "relative import_module": (
        "from importlib import import_module\n\ndef load():\n    return import_module('.target', __package__).VALUE\n"
    ),
    "__import__ with a fromlist": "def load():\n    return __import__('app.target', fromlist=['VALUE']).VALUE\n",
    "resolve_name": "import pkgutil\n\ndef load():\n    return pkgutil.resolve_name('app.target:VALUE')\n",
}
CHANGES = {  # to app/target.py
    "edited": lambda project: edit_file(project, "app/target.py"),
    "broken": lambda project: (project.path / "app/target.py").write_text("VALUE = 2\n"),
    "deleted": lambda project: (project.path / "app/target.py").unlink(),
}


@pytest.mark.parametrize("loader", LOADERS)
@pytest.mark.parametrize("change", CHANGES)
def test_a_module_loaded_by_name_selects_its_loader_s_tests(loader_project, loader, change):
    """The issue's case: a test importing the loader, not the module it loads, runs when that module changes.
    Broken or deleted, the module fails the test, so the dependency is real. An unrelated test stays deselected."""
    project = loader_project(LOADERS[loader])
    CHANGES[change](project)

    result = run(project)

    if change == "edited":
        assert_selected(result, passed=["test_consumer.py"], deselected=["test_other.py"])
    else:
        result.assert_outcomes(failed=1, deselected=1)
        result.stdout.fnmatch_lines(["*suite/test_consumer.py::test_value FAILED*"])


@pytest.mark.parametrize(
    "loader",
    [
        "import importlib\n\ndef load():\n    return importlib.import_module('app.' + 'target').VALUE\n",
        "def load():\n    '''Loads app.target with importlib.import_module(\"app.target\").'''\n    return 1\n",
    ],
    ids=["a computed name", "a docstring"],
)
def test_a_name_no_literal_argument_spells_selects_nothing(loader_project, loader):
    """A known limit, pinned: a computed name is no import here (map it with ``--impacted-map``), and a
    loader's name in a docstring calls nothing."""
    project = loader_project(loader)
    edit_file(project, "app/target.py")

    assert_selected(run(project), deselected=["test_consumer.py", "test_other.py"])


PATCHING_TESTS = {
    "mock.patch": (
        "from unittest import mock\n\nfrom app.service import total\n\n"
        "@mock.patch('app.prices.Price.amount', 5)\ndef test_total():\n    assert total() == 10\n"
    ),
    "mocker.patch": (
        "from app.service import total\n\n"
        "def test_total(mocker):\n    mocker.patch('app.prices.Price.amount', 5)\n    assert total() == 10\n"
    ),
    "monkeypatch.setattr": (
        "from app.service import total\n\n"
        "def test_total(monkeypatch):\n    monkeypatch.setattr('app.prices.Price.amount', 5)\n"
        "    assert total() == 10\n"
    ),
}
# pytest-mock's fixture, in the little the test uses: an import by name is read from the call, not the plugin.
MOCKER = (
    "from unittest import mock\n\nimport pytest\n\n\n@pytest.fixture\ndef mocker():\n"
    "    started = []\n\n    class Mocker:\n        def patch(self, *args, **kwargs):\n"
    "            started.append(mock.patch(*args, **kwargs))\n            return started[-1].start()\n\n"
    "    yield Mocker()\n    for patcher in started:\n        patcher.stop()\n"
)


@pytest.mark.parametrize("patching", PATCHING_TESTS)
def test_a_test_patching_a_module_runs_when_it_changes(make_git_project, patching):
    """The test imports ``app.service`` only, which reaches ``app.prices`` through no import: renaming the
    attribute the test patches breaks it, so an edit to ``app/prices.py`` must select it."""
    project = make_git_project(
        {
            "suite/conftest.py": MOCKER,
            "app/__init__.py": "",
            "app/prices.py": "class Price:\n    amount = 3\n",
            "app/service.py": (
                "import importlib\n\ndef total():\n"
                "    return 2 * importlib.import_module('app.' + 'prices').Price.amount\n"
            ),
            "suite/test_total.py": PATCHING_TESTS[patching],
            **OTHER,
        },
        INI,
    )
    (project.path / "app/prices.py").write_text("class Price:\n    cost = 3\n")  # the attribute renamed

    result = run(project)

    result.assert_outcomes(failed=1, deselected=1)
    result.stdout.fnmatch_lines(["*suite/test_total.py::test_total FAILED*"])
