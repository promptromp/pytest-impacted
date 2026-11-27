"""A test package's ``__init__.py`` runs before every test beneath it, as a conftest does.

pytest runs ``suite/__init__.py`` and ``suite/db/__init__.py`` before ``suite/db/test_db.py``, in every import
mode (``Package.setup``). No test names them, so a change reaching what they import reached no test.
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
    """``app/__init__.py`` runs before the tests in ``app/tests`` too, but its imports reach no further than the tests
    importing them: following it would make every import of the package depend on all it imports."""
    files = {
        **APP,
        "app/__init__.py": "from app import db\n",
        "app/tests/__init__.py": "",
        "app/tests/test_a.py": "def test_a():\n    assert True\n",
        "app/tests/test_b.py": "def test_b():\n    assert True\n",
    }
    project = make_git_project(files, INI.replace("impacted_tests_dir = suite", "impacted_tests_dir = app/tests"))
    edit(project, "app/db.py")

    assert_selected(run(project, "--impacted-conftest-imports"), passed=[], deselected=["test_a.py", "test_b.py"])


@pytest.mark.parametrize(
    ("files", "ini", "edited", "passed", "deselected"),
    [
        pytest.param(
            {
                **APP,
                **TESTS,
                "libs/common/__init__.py": "from libs.common.helpers import setup\n",
                "libs/common/helpers.py": HELPER,
                "app/service.py": "from libs.common import setup\n",
                "suite/test_service.py": "import app.service\n\ndef test_service():\n    assert True\n",
            },
            INI,
            "libs/common/helpers.py",
            ["test_service.py"],
            ["test_db.py", "test_other.py"],
            id="a_library_s",
        ),
        pytest.param(
            {
                "app/__init__.py": "from app.core.models import Model\n",
                "app/core/__init__.py": "",
                "app/core/models.py": "class Model:\n    pass\n",
                "app/tests/__init__.py": "",
                "app/tests/test_a.py": "def test_a():\n    assert True\n",
                "app/tests/test_b.py": "from app import Model\n\ndef test_b():\n    assert Model\n",
            },
            INI.replace("= app\n", "= app/core\n").replace("= suite", "= app/tests"),
            "app/core/models.py",
            ["test_b.py"],
            ["test_a.py"],
            id="the_package_above_the_analysed_one",
        ),
    ],
)
def test_an_init_outside_the_tests_dir_is_no_test_package(make_git_project, files, ini, edited, passed, deselected):
    """Only the tests directory's ``__init__.py`` files count: one only found by import is a library's, or holds the
    analysed package (``app/__init__.py`` above ``--impacted-module=app/core``), a facade as an application package's
    is. Neither is named in the notice, nor followed with ``--impacted-conftest-imports``."""
    project = make_git_project(files, ini)
    edit(project, edited)

    reported = run(project)
    assert "Changed application code is imported by" not in reported.stdout.str()
    assert_selected(reported, passed=passed, deselected=deselected)
    assert_selected(run(project, "--impacted-conftest-imports"), passed=passed, deselected=deselected)
