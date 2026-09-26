"""End-to-end tests for implicit namespace sub-packages, via pytester and a real git repo.

A directory without ``__init__.py`` inside a package still imports (PEP 420), so a
change under one must select the tests that depend on it — and tests kept in one
inside the package must be found at all.
"""

import pytest

from .git_helpers import edit_file


FILES = {
    "app/__init__.py": "",
    "app/processors/ocr.py": "def scan():\n    return 'text'\n",
    "app/core.py": "def add(a, b):\n    return a + b\n",
    # In-package tests, in a directory without __init__.py, with no --impacted-tests-dir.
    "app/checks/test_ocr.py": "from app.processors.ocr import scan\n\ndef test_scan():\n    assert scan()\n",
    "app/checks/test_core.py": "from app.core import add\n\ndef test_add():\n    assert add(1, 1) == 2\n",
}
INI = "[pytest]\npythonpath = .\nimpacted_module = app\n"


@pytest.mark.parametrize(
    ("edited", "runs"),
    [
        pytest.param("app/processors/ocr.py", "*test_ocr.py::test_scan PASSED*", id="source_in_a_namespace_subpackage"),
        pytest.param("app/checks/test_core.py", "*test_core.py::test_add PASSED*", id="test_in_a_namespace_subpackage"),
    ],
)
def test_a_change_in_a_namespace_subpackage_selects_its_tests(make_git_project, edited, runs):
    project = make_git_project(FILES, INI)
    edit_file(project, edited)

    result = project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v")

    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines([runs])
