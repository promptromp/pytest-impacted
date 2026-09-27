"""End-to-end tests for changed ``.py`` files no walk names, via pytester and a real git repo.

A module outside ``--impacted-module`` and ``--impacted-tests-dir`` that an analysed module
imports (``testing/factories.py``, a shared library), a module deleted while something still
imports it, and a file nothing imports (a script) used to resolve to nothing, and selected
nothing.
"""

import pytest

from .git_helpers import edit_file


INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"
FACTORY = "def make():\n    return 1\n"
DB_FIXTURE = "import pytest\nfrom testing.factories import make\n\n@pytest.fixture\ndef db():\n    return make()\n"
USES_FACTORY = "from testing.factories import make\n\ndef test_other():\n    assert make()\n"
LOADS_PLUGIN = 'pytest_plugins = ["{plugin}"]\n\ndef test_db(db):\n    assert db\n'
TESTS = {
    "app/__init__.py": "",
    "suite/db/test_db.py": "def test_db(db):\n    assert db\n",
    "suite/other/test_other.py": "def test_other():\n    assert True\n",
}


def run(pytester, *args):
    return pytester.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


def assert_selected(result, *, passed=(), skipped=()):
    result.assert_outcomes(passed=len(passed), skipped=len(skipped))
    result.stdout.fnmatch_lines_random(
        [f"*{name}::* PASSED*" for name in passed] + [f"*{name}::* SKIPPED*" for name in skipped]
    )


@pytest.mark.parametrize(
    ("files", "passed", "skipped"),
    [
        pytest.param({"suite/db/conftest.py": DB_FIXTURE}, ["test_db.py"], ["test_other.py"], id="a_conftest"),
        pytest.param(
            {"suite/db/conftest.py": DB_FIXTURE, "suite/other/test_other.py": USES_FACTORY},
            ["test_db.py", "test_other.py"],
            [],
            id="a_test",
        ),
        pytest.param(
            {
                "suite/plugin_fixtures.py": DB_FIXTURE,
                "suite/db/test_db.py": LOADS_PLUGIN.format(plugin="suite.plugin_fixtures"),
            },
            ["test_db.py", "test_other.py"],
            [],
            id="a_pytest_plugin",
        ),
        pytest.param(
            {"testing/fixtures.py": DB_FIXTURE, "suite/db/test_db.py": LOADS_PLUGIN.format(plugin="testing.fixtures")},
            ["test_db.py", "test_other.py"],
            [],
            id="a_pytest_plugin_outside_too",
        ),
    ],
)
def test_an_edit_outside_the_analysed_dirs_reaches_what_imports_it(make_git_project, files, passed, skipped):
    """``testing/factories.py`` is neither application code nor in the tests dir: test code, followed."""
    project = make_git_project({**TESTS, "testing/factories.py": FACTORY, **files}, INI)
    edit_file(project, "testing/factories.py")

    assert_selected(run(project, "suite"), passed=passed, skipped=skipped)


APP_FIXTURE = "import pytest\nfrom app.core import thing\n\n@pytest.fixture\ndef db():\n    return thing()\n"
SHARED = {
    "app/core.py": "from libs.shared import helper\n\ndef thing():\n    return helper()\n",
    "libs/shared.py": "def helper():\n    return 1\n",
    "suite/conftest.py": APP_FIXTURE,
    "suite/core/test_core.py": "from app.core import thing\n\ndef test_core():\n    assert thing()\n",
}


@pytest.mark.parametrize(
    ("args", "passed", "skipped"),
    [
        pytest.param([], ["test_core.py"], ["test_db.py", "test_other.py"], id="default"),
        pytest.param(
            ["--impacted-conftest-imports"], ["test_core.py", "test_db.py", "test_other.py"], [], id="opted_in"
        ),
    ],
)
def test_a_library_the_application_imports_is_application_code(make_git_project, args, passed, skipped):
    """A root conftest importing the app must not turn every library edit into a full run by default."""
    project = make_git_project({**TESTS, **SHARED}, INI)
    edit_file(project, "libs/shared.py")

    result = run(project, "suite", *args)

    assert_selected(result, passed=passed, skipped=skipped)
    if not args:
        result.stdout.fnmatch_lines(["*Changed application code is imported by *suite/conftest.py*"])


def test_deleting_a_module_selects_what_still_imports_it(make_git_project):
    """An import inside a function is no collection error: without the edge, the broken test is skipped."""
    project = make_git_project(
        {
            **TESTS,
            "app/utils.py": "def add(a, b):\n    return a + b\n",
            "suite/db/test_db.py": "def test_db():\n    from app.utils import add\n    assert add(1, 1) == 2\n",
        },
        INI,
    )
    (project.path / "app/utils.py").unlink()

    result = run(project, "suite")

    result.assert_outcomes(failed=1, skipped=1)
    result.stdout.fnmatch_lines(["*test_db.py::test_db FAILED*"])


def test_a_changed_file_nothing_imports_is_named_in_a_notice(make_git_project):
    files = {**TESTS, "suite/db/test_db.py": TESTS["suite/other/test_other.py"], "scripts/deploy.py": ""}
    project = make_git_project(files, INI)
    edit_file(project, "scripts/deploy.py")

    result = run(project, "suite")

    result.assert_outcomes(skipped=2)
    result.stdout.fnmatch_lines(["*No analysed module imports *scripts/deploy.py*"])
