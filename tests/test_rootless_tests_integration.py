"""End-to-end: imports between the files of a tests directory outside any package are followed.

Before importing a test module or conftest outside any package, pytest's default import mode
puts its directory on ``sys.path``, so its neighbours import each other by their bare names
(``import helpers``, ``from test_orders import Order``). Those names matched nothing, so an edit
to a helper selected none of the tests importing it.
"""

import pytest

from .git_helpers import edit_file as edit


FILES = {
    "app/__init__.py": "",
    "app/a.py": "",
    "suite/helpers.py": "def helper():\n    return 1\n",
    "suite/test_uses_helper.py": "import helpers\n\ndef test_helper():\n    assert helpers.helper() == 1\n",
    "suite/test_orders.py": "class Order:\n    x = 1\n\ndef test_orders():\n    assert Order.x == 1\n",
    "suite/test_orders_totals.py": "from test_orders import Order\n\ndef test_totals():\n    assert Order.x == 1\n",
    "suite/test_other.py": "def test_other():\n    assert True\n",
}
INI = "[pytest]\nimpacted_module = app\nimpacted_tests_dir = suite\n"


def run(project, *args):
    return project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


@pytest.mark.parametrize(
    ("edited", "selected"),
    [
        pytest.param("suite/helpers.py", ["test_uses_helper.py::test_helper"], id="a_helper"),
        pytest.param(
            "suite/test_orders.py",
            ["test_orders.py::test_orders", "test_orders_totals.py::test_totals"],
            id="a_test_module_another_imports",
        ),
    ],
)
def test_an_edit_selects_the_tests_importing_it_by_its_bare_name(make_git_project, edited, selected):
    project = make_git_project(FILES, INI)
    edit(project, edited)

    result = run(project)

    result.assert_outcomes(passed=len(selected), deselected=4 - len(selected))
    result.stdout.fnmatch_lines_random([f"*suite/{test} PASSED*" for test in selected])


def test_a_conftest_importing_an_edited_helper_selects_every_test_beneath_it(make_git_project):
    """The helper is test code, so the conftest rule selects the directory, as for any fixture module."""
    project = make_git_project({**FILES, "suite/conftest.py": "import helpers\n"}, INI)
    edit(project, "suite/helpers.py")

    result = run(project)

    result.assert_outcomes(passed=4)


def test_a_deleted_helper_selects_the_tests_importing_it(make_git_project):
    """Imported in a function, so the importer still collects: the deletion must bring its tests."""
    lazy = {"suite/test_lazy.py": "def f():\n    import gone\n\ndef test_lazy():\n    assert f\n"}
    lazy["suite/gone.py"] = ""
    project = make_git_project({**FILES, **lazy}, INI)
    (project.path / "suite/gone.py").unlink()

    result = run(project)

    result.assert_outcomes(passed=1, deselected=4)
    result.stdout.fnmatch_lines(["*suite/test_lazy.py::test_lazy PASSED*"])


def test_an_app_module_s_bare_name_through_a_basedir_places_nothing(make_git_project):
    """A root conftest makes the rootdir a basedir, so ``app/x.py``'s ``import helpers`` reaches
    ``suite/helpers.py`` — perhaps by coincidence. It must not make what that helper imports application
    code: the conftest importing ``testing/factories.py`` still selects every test beneath it."""
    files = {
        "conftest.py": "",
        "app/__init__.py": "",
        "app/x.py": "import helpers\n",
        "testing/__init__.py": "",
        "testing/factories.py": "def make():\n    return 1\n",
        "suite/helpers.py": "from testing import factories\n",
        "suite/conftest.py": "import testing.factories\n",
        "suite/test_a.py": "def test_a():\n    assert True\n",
        "suite/test_b.py": "def test_b():\n    assert True\n",
    }
    project = make_git_project(files, INI)
    edit(project, "testing/factories.py")

    result = run(project)

    result.assert_outcomes(passed=2)


def test_a_deleted_helper_an_app_module_imports_through_a_basedir_stays_test_code(make_git_project):
    """Its links through basedirs only reach: the app module's coincidental ``import helpers`` must not make
    the deleted helper application code, which the conftest importing it would then select only on request."""
    files = {
        "conftest.py": "",
        "app/__init__.py": "",
        "app/x.py": "def f():\n    import helpers\n",
        "suite/helpers.py": "",
        "suite/conftest.py": "def fixture_helpers():\n    import helpers\n",
        "suite/test_a.py": "def test_a():\n    assert True\n",
        "suite/test_b.py": "def test_b():\n    assert True\n",
    }
    project = make_git_project(files, INI)
    (project.path / "suite/helpers.py").unlink()

    result = run(project)

    result.assert_outcomes(passed=2)


def test_a_root_conftest_fixture_importing_an_edited_helper_selects_the_tests(make_git_project):
    """The fixture's ``import helpers`` runs at test time, with ``suite/`` on ``sys.path``: the conftest reaches
    the helper, and the conftest rule selects every test beneath it."""
    files = {
        "conftest.py": "import pytest\n\n@pytest.fixture\ndef h():\n    import helpers\n    return helpers\n",
        "app/__init__.py": "",
        "suite/helpers.py": "def val():\n    return 1\n",
        "suite/test_a.py": "def test_a(h):\n    assert h.val() == 1\n",
    }
    project = make_git_project(files, INI)
    edit(project, "suite/helpers.py")

    result = run(project)

    result.assert_outcomes(passed=1)
