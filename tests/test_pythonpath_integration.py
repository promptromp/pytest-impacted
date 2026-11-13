"""End-to-end: a directory on pytest's ``pythonpath`` is an import root.

pytest puts each ``pythonpath`` entry on ``sys.path``, so a flat directory of modules there
(``lib/awake.py``) is imported by its bare name (``import awake``), while the walk of
``--impacted-module=lib`` names it ``lib.awake``. Without the entry as an import root, an edit
to ``lib/awake.py`` selected no test.
"""

import pytest
from click.testing import CliRunner

from pytest_impacted.cli import impacted_tests_cli

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
