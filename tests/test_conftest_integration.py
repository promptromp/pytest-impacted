"""End-to-end tests for conftests, via pytester and a real git repo.

Tests never import their conftest — pytest injects its fixtures by name. A conftest that
was edited, or imports changed test code (a fixture module, another conftest), therefore
selects every test in its directory and below. One importing changed *application* code
does the same only with ``--impacted-conftest-imports``: a top-level conftest importing the
app would otherwise select almost every test on almost every change.
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
HELPER = "from app.db import connect\n\ndef make():\n    return connect()\n"
HELPER_FIXTURE = "import pytest\nfrom suite.db.helpers import make\n\n@pytest.fixture\ndef db():\n    return make()\n"


@pytest.fixture
def make_project(make_git_project):
    """The shared factory, defaulting to this module's ini."""
    return lambda files, ini=INI: make_git_project(files, ini)


def run(pytester, *args):
    return pytester.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


def assert_selected(result, *, passed=(), skipped=()):
    """Which test files ran and which were skipped, not just how many."""
    result.assert_outcomes(passed=len(passed), skipped=len(skipped))
    result.stdout.fnmatch_lines_random(
        [f"*{name}::* PASSED*" for name in passed] + [f"*{name}::* SKIPPED*" for name in skipped]
    )


# Every way of turning the opt-in rule on: (extra ini lines, extra command-line args).
OPT_IN = [
    pytest.param("", ["--impacted-conftest-imports"], id="flag"),
    pytest.param("impacted_conftest_imports = true\n", [], id="ini"),
]
# Conftests that import the changed application module, and the test files each then selects.
APP_REACHES_A_CONFTEST = [
    pytest.param({"suite/db/conftest.py": DB_FIXTURE}, ["test_db.py"], ["test_other.py"], id="one_directory"),
    pytest.param({"conftest.py": DB_FIXTURE}, ["test_db.py", "test_other.py"], [], id="root_conftest"),
    pytest.param(
        {"suite/db/helpers.py": HELPER, "suite/db/conftest.py": HELPER_FIXTURE},
        ["test_db.py"],
        ["test_other.py"],
        id="through_a_helper_module",
    ),
]


@pytest.mark.parametrize(("conftest", "passed", "skipped"), APP_REACHES_A_CONFTEST)
@pytest.mark.parametrize(("ini", "args"), OPT_IN)
def test_opted_in_application_code_reaching_a_conftest_selects_its_directory(
    make_project, conftest, passed, skipped, ini, args
):
    """A root conftest is above the analysed packages, invisible to package discovery, and still counts."""
    project = make_project({**APP, **TESTS, **conftest}, ini=INI + ini)
    edit(project, "app/db.py")

    result = run(project, *args)

    assert_selected(result, passed=passed, skipped=skipped)
    result.stdout.fnmatch_lines(["*impacted_conftest_imports=True*"])
    result.stdout.no_fnmatch_line("*pass --impacted-conftest-imports*")


@pytest.mark.parametrize(
    "conftest", [p.values[0] for p in APP_REACHES_A_CONFTEST], ids=[p.id for p in APP_REACHES_A_CONFTEST]
)
def test_by_default_application_code_reaching_only_a_conftest_selects_nothing(make_project, conftest):
    """No test imports ``app.db``: only a conftest does. The notice says so, and names the option."""
    project = make_project({**APP, **TESTS, **conftest})
    edit(project, "app/db.py")

    result = run(project)

    assert_selected(result, skipped=["test_db.py", "test_other.py"])
    result.stdout.fnmatch_lines(["*impacted_conftest_imports=False*", "*conftest.py*--impacted-conftest-imports*"])


def test_an_explicit_false_in_the_ini_keeps_the_rule_off(make_project):
    """A typed bool: the string ``"false"`` must not read as truthy."""
    ini = INI + "impacted_conftest_imports = false\n"
    project = make_project({**APP, **TESTS, "suite/db/conftest.py": DB_FIXTURE}, ini=ini)
    edit(project, "app/db.py")

    assert_selected(run(project), skipped=["test_db.py", "test_other.py"])


STAR_IMPORTED_FIXTURES = {
    "suite/fixtures.py": DB_FIXTURE,
    "suite/db/conftest.py": "from suite.fixtures import *  # noqa: F403\n",
}
CONFTEST_IMPORTING_A_CONFTEST = {
    "suite/db/conftest.py": DB_FIXTURE,
    "suite/other/conftest.py": "from suite.db.conftest import db  # noqa: F401\n",
    "suite/other/test_other.py": "def test_other(db):\n    assert db\n",
}


@pytest.mark.parametrize("args", [[], ["--impacted-conftest-imports"]], ids=["default", "opted_in"])
@pytest.mark.parametrize(
    ("files", "edited", "passed", "skipped"),
    [
        pytest.param(
            STAR_IMPORTED_FIXTURES, "suite/fixtures.py", ["test_db.py"], ["test_other.py"], id="fixture_module"
        ),
        pytest.param(
            CONFTEST_IMPORTING_A_CONFTEST,
            "suite/db/conftest.py",
            ["test_db.py", "test_other.py"],
            [],
            id="conftest_importing_the_edited_conftest",
        ),
    ],
)
def test_test_code_reaching_a_conftest_selects_its_directory(make_project, files, edited, passed, skipped, args):
    """Fixture modules and conftests are test code, not the application: they never needed the option."""
    project = make_project({**APP, **TESTS, **files})
    edit(project, edited)

    assert_selected(run(project, *args), passed=passed, skipped=skipped)


USES_HELPER = "from backend.conftest import helper\n\ndef test_other():\n    assert helper()\n"
LOADS_PLUGIN = 'pytest_plugins = ["suite.plugin_fixtures"]\n\ndef test_db(db):\n    assert db\n'
BACKEND = {
    "backend/app/__init__.py": "",
    "backend/app/db.py": "def connect():\n    return 'conn'\n",
    "backend/conftest.py": DB_FIXTURE.replace("from app.db", "from backend.app.db") + "\ndef helper():\n    return 1\n",
    "suite/db/test_db.py": "def test_db(db):\n    assert db\n",
    "suite/other/test_other.py": "def test_other():\n    assert True\n",
}


@pytest.mark.parametrize("args", [[], ["--impacted-conftest-imports"]], ids=["default", "opted_in"])
@pytest.mark.parametrize(
    ("files", "passed", "skipped"),
    [
        pytest.param(
            {"suite/db/conftest.py": "from backend.conftest import *  # noqa: F403\n"},
            ["test_db.py"],
            ["test_other.py"],
            id="a_conftest_imports_it",
        ),
        pytest.param(
            {
                "suite/db/conftest.py": "from backend.conftest import *  # noqa: F403\n",
                "suite/other/test_other.py": USES_HELPER,
            },
            ["test_db.py", "test_other.py"],
            [],
            id="a_test_imports_it",
        ),
        pytest.param(
            {
                "suite/plugin_fixtures.py": "from backend.conftest import *  # noqa: F403\n",
                "suite/db/test_db.py": LOADS_PLUGIN,
            },
            ["test_db.py", "test_other.py"],
            [],
            id="a_pytest_plugin_imports_it",
        ),
    ],
)
def test_an_edited_conftest_above_the_package_reaches_what_imports_it(make_project, files, passed, skipped, args):
    """``backend/conftest.py`` sits above ``--impacted-module=backend/app``: an edit resolves to its node."""
    project = make_project({**BACKEND, **files}, INI.replace("impacted_module = app", "impacted_module = backend/app"))
    edit(project, "backend/conftest.py")

    assert_selected(run(project, "suite", *args), passed=passed, skipped=skipped)


@pytest.mark.parametrize(
    ("tests_dir", "passed", "skipped"),
    [
        pytest.param(
            "impacted_tests_dir = app/tests\n", ["test_db.py"], ["test_other.py"], id="named_as_the_tests_dir"
        ),
        pytest.param("", [], ["test_db.py", "test_other.py"], id="counted_as_application_code"),
    ],
)
def test_a_fixture_module_of_tests_kept_inside_the_package(make_project, tests_dir, passed, skipped):
    """Test code is what ``--impacted-tests-dir`` names; without it, the package is all application code."""
    files = {
        **APP,
        "app/tests/__init__.py": "",
        "app/tests/fixtures.py": DB_FIXTURE,
        "app/tests/db/conftest.py": "from app.tests.fixtures import *  # noqa: F403\n",
        "app/tests/db/test_db.py": "def test_db(db):\n    assert db\n",
        "app/tests/other/test_other.py": "def test_other():\n    assert True\n",
    }
    project = make_project(files, ini="[pytest]\npythonpath = .\nimpacted_module = app\n" + tests_dir)
    edit(project, "app/tests/fixtures.py")

    assert_selected(run(project), passed=passed, skipped=skipped)


@pytest.mark.parametrize("args", [[], ["--impacted-conftest-imports"]], ids=["default", "opted_in"])
def test_an_edited_conftest_selects_its_directory(make_project, args):
    """Editing the conftest itself is not what the option governs: it always selects its directory."""
    project = make_project({**APP, **TESTS, "suite/db/conftest.py": DB_FIXTURE})
    edit(project, "suite/db/conftest.py")

    assert_selected(run(project, *args), passed=["test_db.py"], skipped=["test_other.py"])


@pytest.mark.parametrize("args", [[], ["--impacted-conftest-imports"]], ids=["default", "opted_in"])
def test_change_not_reaching_the_conftest_does_not_select_its_directory(make_project, args):
    project = make_project({**APP, **TESTS, "suite/db/conftest.py": DB_FIXTURE})
    edit(project, "app/utils.py")

    assert_selected(run(project, *args), passed=["test_other.py"], skipped=["test_db.py"])


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
