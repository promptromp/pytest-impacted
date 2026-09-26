"""End-to-end tests for conftests that depend on changed code, via pytester and a real git repo.

Tests never import their conftest — pytest injects its fixtures by name — so a
change that reaches a conftest through the import graph must select the tests in
that conftest's directory, exactly as editing the conftest itself does.
"""

import pytest

from .git_helpers import edit_file as edit


APP = {
    "app/__init__.py": "",
    "app/db.py": "def connect():\n    return 'conn'\n",
    "app/utils.py": "def add(a, b):\n    return a + b\n",
}
DB_FIXTURE = "import pytest\nfrom app.db import connect\n\n@pytest.fixture\ndef db():\n    return connect()\n"
TESTS = {
    "suite/db/test_db.py": "def test_db(db):\n    assert db\n",
    "suite/other/test_other.py": "from app.utils import add\n\ndef test_other():\n    assert add(1, 1) == 2\n",
}
# Not "tests": pytester runs in-process and shares sys.modules with this repo's own
# ``tests`` package, so ``tests.db`` could never be imported by the project under test.
INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"


@pytest.fixture
def make_project(make_git_project):
    """The shared factory, defaulting to this module's ini."""
    return lambda files, ini=INI: make_git_project(files, ini)


def run(pytester):
    return pytester.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged")


def test_app_change_reaching_a_conftest_selects_its_directory(make_project):
    project = make_project({**APP, **TESTS, "suite/db/conftest.py": DB_FIXTURE})
    edit(project, "app/db.py")

    result = run(project)

    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*test_db.py*"])


def test_app_change_reaching_a_root_conftest_selects_every_test(make_project):
    """A conftest above the analysed packages is invisible to package discovery."""
    project = make_project({**APP, **TESTS, "conftest.py": DB_FIXTURE})
    edit(project, "app/db.py")

    run(project).assert_outcomes(passed=2)


def test_conftest_reached_through_a_helper_module(make_project):
    helper = "from app.db import connect\n\ndef make():\n    return connect()\n"
    fixture = "import pytest\nfrom suite.db.helpers import make\n\n@pytest.fixture\ndef db():\n    return make()\n"
    project = make_project({**APP, **TESTS, "suite/db/helpers.py": helper, "suite/db/conftest.py": fixture})
    edit(project, "app/db.py")

    run(project).assert_outcomes(passed=1, skipped=1)


def test_change_not_reaching_the_conftest_does_not_select_its_directory(make_project):
    project = make_project({**APP, **TESTS, "suite/db/conftest.py": DB_FIXTURE})
    edit(project, "app/utils.py")

    result = run(project)

    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*test_other.py*"])


def test_src_layout_conftest_inside_the_package(make_project):
    """Module names drop ``src/`` while the files keep it; the graph's recorded paths resolve both."""
    project = make_project(
        {
            "src/app/__init__.py": "",
            "src/app/tests/__init__.py": "",
            "src/app/tests/conftest.py": "import pytest\n\n@pytest.fixture\ndef value():\n    return 1\n",
            "src/app/tests/test_value.py": "def test_value(value):\n    assert value == 1\n",
            "src/app/other/__init__.py": "",
            "src/app/other/test_unrelated.py": "def test_unrelated():\n    assert True\n",
        },
        ini="[pytest]\npythonpath = src\nimpacted_module = src/app\n",
    )
    edit(project, "src/app/tests/conftest.py")

    run(project).assert_outcomes(passed=1, skipped=1)


PLUGIN_FIXTURES = "import pytest\nfrom app.db import connect\n\n@pytest.fixture\ndef db():\n    return connect()\n"


@pytest.mark.parametrize("edited", ["suite/plugin_fixtures.py", "app/db.py"])
def test_fixtures_loaded_through_pytest_plugins(make_project, edited):
    """Plugins are registered for the whole session, so a change reaching one runs every test.

    Declared in a test module on purpose: a root conftest would select everything
    through the directory rule anyway, and prove nothing about session-wide scope.
    """
    project = make_project(
        {
            **APP,
            **TESTS,
            "suite/plugin_fixtures.py": PLUGIN_FIXTURES,
            "suite/db/test_db.py": 'pytest_plugins = ["suite.plugin_fixtures"]\n\ndef test_db(db):\n    assert db\n',
        }
    )
    edit(project, edited)

    run(project).assert_outcomes(passed=2)


def test_plugins_declared_by_a_plugin_are_followed(make_project):
    """pytest reads ``pytest_plugins`` from every plugin it loads, not just conftests and test modules."""
    project = make_project(
        {
            **APP,
            **TESTS,
            "suite/plugin_fixtures.py": 'pytest_plugins = ["suite.db_fixtures"]\n',
            "suite/db_fixtures.py": PLUGIN_FIXTURES,
            "suite/db/test_db.py": 'pytest_plugins = ["suite.plugin_fixtures"]\n\ndef test_db(db):\n    assert db\n',
        }
    )
    edit(project, "suite/db_fixtures.py")

    run(project).assert_outcomes(passed=2)


def test_editing_a_test_module_that_loads_a_third_party_plugin_selects_only_it(make_project):
    """A body edit in a module declaring ``pytest_plugins = "pytester"`` must not run the whole suite."""
    project = make_project(
        {
            **APP,
            **TESTS,
            "suite/db/test_db.py": 'pytest_plugins = "pytester"\n\ndef test_db():\n    assert True\n',
        }
    )
    edit(project, "suite/db/test_db.py")

    run(project).assert_outcomes(passed=1, skipped=1)


def test_plugins_loaded_with_dash_p_are_session_wide(make_project):
    project = make_project(
        {**APP, **TESTS, "suite/plugin_fixtures.py": PLUGIN_FIXTURES},
        ini=INI + "addopts = -p suite.plugin_fixtures\n",
    )
    edit(project, "suite/plugin_fixtures.py")

    run(project).assert_outcomes(passed=2)


def test_plugins_loaded_through_the_environment_are_session_wide(make_project, monkeypatch):
    monkeypatch.setenv("PYTEST_PLUGINS", "suite.plugin_fixtures")
    project = make_project({**APP, **TESTS, "suite/plugin_fixtures.py": PLUGIN_FIXTURES})
    edit(project, "suite/plugin_fixtures.py")

    run(project).assert_outcomes(passed=2)
