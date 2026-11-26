"""A test package's ``__init__.py`` runs before every test beneath it, as a conftest does.

pytest's default import mode imports ``suite/db/test_db.py`` as ``suite.db.test_db`` when ``suite/`` and
``suite/db/`` are packages, which runs ``suite/__init__.py`` and ``suite/db/__init__.py`` first. No test names
them, so a change reaching what they import reached no test.
"""

import pytest

from .git_helpers import INI, assert_selected, edit_file as edit, run


APP = {
    "app/__init__.py": "",
    "app/db.py": "def connect():\n    return 'conn'\n",
    "app/utils.py": "def add(a, b):\n    return a + b\n",
}
TESTS = {
    "suite/__init__.py": "",
    "suite/db/__init__.py": "",
    "suite/db/test_db.py": "def test_db():\n    assert True\n",
    "suite/other/__init__.py": "",
    "suite/other/test_other.py": "from app.utils import add\n\ndef test_other():\n    assert add(1, 1) == 2\n",
}
HELPER = "def setup():\n    return 'ready'\n"


@pytest.mark.parametrize(
    ("init", "passed", "deselected"),
    [
        pytest.param("suite/db/__init__.py", ["test_db.py"], ["test_other.py"], id="its_directory"),
        pytest.param("suite/__init__.py", ["test_db.py", "test_other.py"], [], id="the_root_test_package"),
    ],
)
def test_test_code_a_test_package_imports_selects_the_tests_beneath_it(make_git_project, init, passed, deselected):
    """As for a conftest importing changed test code: always."""
    files = {**APP, **TESTS, "suite/helpers.py": HELPER, init: "from suite.helpers import setup\n\nsetup()\n"}
    project = make_git_project(files, INI)
    edit(project, "suite/helpers.py")

    assert_selected(run(project), passed=passed, deselected=deselected)


@pytest.mark.parametrize(
    ("args", "passed", "deselected"),
    [
        pytest.param([], [], ["test_db.py", "test_other.py"], id="default"),
        pytest.param(["--impacted-conftest-imports"], ["test_db.py"], ["test_other.py"], id="opted_in"),
    ],
)
def test_application_code_a_test_package_imports_selects_the_tests_beneath_it_on_request(
    make_git_project, args, passed, deselected
):
    """As for a conftest importing changed application code: only with --impacted-conftest-imports, else named."""
    files = {**APP, **TESTS, "suite/db/__init__.py": "from app.db import connect\n\nconnect()\n"}
    project = make_git_project(files, INI)
    edit(project, "app/db.py")

    result = run(project, *args)

    assert_selected(result, passed=passed, deselected=deselected)
    if not args:
        result.stdout.fnmatch_lines(["*Changed application code is imported by *suite/db/__init__.py*"])


def test_an_application_package_s_init_is_no_test_package(make_git_project):
    """``app/__init__.py`` runs for every test importing ``app.*`` too, but its imports reach no further than the tests
    importing them: following it would make every import of the package depend on all it imports."""
    files = {**APP, **TESTS, "app/__init__.py": "from app import db\n"}
    project = make_git_project(files, INI)
    edit(project, "app/db.py")

    assert_selected(run(project, "--impacted-conftest-imports"), passed=[], deselected=["test_db.py", "test_other.py"])
