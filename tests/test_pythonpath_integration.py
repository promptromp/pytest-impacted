"""End-to-end: a directory on pytest's ``pythonpath`` is an import root.

pytest puts each ``pythonpath`` entry on ``sys.path``, so a flat directory of modules there
(``lib/awake.py``) is imported by its bare name (``import awake``), while the walk of
``--impacted-module=lib`` names it ``lib.awake``. Without the entry as an import root, an edit
to ``lib/awake.py`` selected no test.
"""

import pytest
from click.testing import CliRunner

from pytest_impacted.cli import impacted_tests_cli
from pytest_impacted.plugin import _pythonpath

from .git_helpers import edit_file as edit


FILES = {
    "lib/awake.py": "def up():\n    return True\n",
    "lib/ledger.py": "import awake\n\ndef record():\n    return awake.up()\n",
    "lib/other.py": "def other():\n    return 1\n",
    "suite/test_awake.py": "import awake\n\ndef test_awake():\n    assert awake.up()\n",
    "suite/test_ledger.py": "import ledger\n\ndef test_ledger():\n    assert ledger.record()\n",
    "suite/test_other.py": "import other\n\ndef test_other():\n    assert other.other() == 1\n",
}
INI = "[pytest]\npythonpath = lib\nimpacted_module = lib\nimpacted_tests_dir = suite\n"


def run(project, *args):
    return project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


@pytest.fixture
def project(make_git_project):
    return make_git_project(FILES, INI)


@pytest.mark.parametrize(
    ("edited", "selected"),
    [
        pytest.param("lib/awake.py", ["test_awake", "test_ledger"], id="imported_directly_and_through_ledger"),
        pytest.param("lib/ledger.py", ["test_ledger"], id="imported_directly"),
    ],
)
def test_an_edit_on_the_pythonpath_selects_its_importers(project, edited, selected):
    edit(project, edited)

    result = run(project)

    result.assert_outcomes(passed=len(selected), deselected=3 - len(selected))
    result.stdout.fnmatch_lines_random([f"*suite/{test}.py::{test} PASSED*" for test in selected])


def test_the_cli_reads_the_pythonpath_setting(project):
    edit(project, "lib/ledger.py")

    result = CliRunner().invoke(impacted_tests_cli, ["--root-dir", str(project.path)])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str((project.path / "suite/test_ledger.py").resolve())]
    assert "pythonpath: " + str(project.path / "lib") in result.output


@pytest.mark.parametrize(
    ("deleted", "importer"),
    [
        pytest.param("plugins/myplug.py", "def f():\n    import myplug\n", id="in_an_entry_outside_the_package"),
        pytest.param("lib/secrets.py", "def f():\n    import secrets\n", id="named_like_the_standard_library"),
    ],
)
def test_a_module_deleted_from_the_pythonpath_selects_the_tests_of_its_importers(make_git_project, deleted, importer):
    """Imported in a function, so the importer still collects: the deletion must bring its tests."""
    lazy = {"lib/lazy.py": importer, "suite/test_lazy.py": "import lazy\n\ndef test_lazy():\n    assert lazy.f\n"}
    project = make_git_project(
        {**FILES, **lazy, "plugins/myplug.py": "", "lib/secrets.py": ""},
        INI.replace("pythonpath = lib", "pythonpath = lib plugins"),
    )
    (project.path / deleted).unlink()

    result = run(project)

    result.assert_outcomes(passed=1, deselected=3)
    result.stdout.fnmatch_lines(["*suite/test_lazy.py::test_lazy PASSED*"])


SHARED_INI = "[pytest]\npythonpath = . lib\nimpacted_module = app\nimpacted_tests_dir = suite\n"
SHARED = {
    "app/__init__.py": "",
    "app/x.py": "import util\n",
    "lib/util.py": "def helper():\n    return 1\n",
    "suite/conftest.py": "import app.x\n",
    "suite/test_a.py": "import app.x\n\ndef test_a():\n    assert app.x\n",
    "suite/test_b.py": "def test_b():\n    assert True\n",
}


@pytest.mark.parametrize(
    ("args", "passed"), [([], 1), (["--impacted-conftest-imports"], 2)], ids=["default", "opted_in"]
)
def test_a_library_on_the_pythonpath_the_app_imports_is_application_code(make_git_project, args, passed):
    """``lib/util.py`` is certainly the ``util`` the app imports, so it is application code: the conftest
    reaching it through the app selects the tests beneath it only on request, as for any such library."""
    project = make_git_project(SHARED, SHARED_INI)
    edit(project, "lib/util.py")

    result = run(project, *args)

    result.assert_outcomes(passed=passed, deselected=2 - passed)


def test_without_the_python_path_plugin_the_run_goes_on(make_git_project):
    """Before pytest 8.4 ``-p no:python_path`` takes the setting away, and ``getini`` raises: no entries then."""
    files = {"app/__init__.py": "", "app/a.py": "", "suite/test_a.py": "import app.a\n\ndef test_a():\n    pass\n"}
    project = make_git_project(files, "[pytest]\nimpacted_module = app\nimpacted_tests_dir = suite\n")
    edit(project, "app/a.py")

    result = project.runpytest_subprocess(
        "--impacted", "-p", "no:cacheprovider", "-p", "no:python_path", "--impacted-git-mode=unstaged"
    )

    result.stdout.no_fnmatch_line("*INTERNALERROR*")
    result.assert_outcomes(passed=1)


def test_a_pythonpath_setting_no_plugin_declares_reads_as_none():
    class Config:
        def getini(self, name):
            raise ValueError(f"unknown configuration value: {name!r}")

    assert _pythonpath(Config()) == []
