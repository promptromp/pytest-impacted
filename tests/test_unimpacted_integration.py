"""End-to-end tests for what happens to tests no change impacts: deselected, or skipped.

They are deselected by default (pytest's idiom: ``-k`` and ``-m`` do the same), and
``--impacted-unimpacted=skip`` marks them skipped instead, as releases before 0.36 did.
A run that deselects every test exits 0, as one that skips every test does: a change that
impacts nothing is no failure, while pytest exits 5 when no test runs.
"""

import pytest
from pytest import ExitCode

from .git_helpers import edit_file as edit


FILES = {
    "app/__init__.py": "",
    "app/a.py": "def a():\n    return 1\n",
    "app/b.py": "def b():\n    return 2\n",
    "suite/test_a.py": "from app.a import a\n\ndef test_a():\n    assert a() == 1\n",
    "suite/test_b.py": "from app.b import b\n\ndef test_b():\n    assert b() == 2\n",
    "docs/notes.md": "notes\n",
}
INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"
NOT_IMPACTED = "pytest-impacted: 1 test not impacted by the changes, deselected"


def run(project, *args):
    return project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


@pytest.fixture
def project(make_git_project):
    return make_git_project(FILES, INI)


def test_unimpacted_tests_are_deselected_by_default(project):
    edit(project, "app/a.py")

    result = run(project)

    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines(["*test_a.py::test_a PASSED*", NOT_IMPACTED])
    result.stdout.no_fnmatch_line("*test_b.py::*")


def test_skip_marks_them_skipped_with_a_reason(project):
    edit(project, "app/a.py")

    result = run(project, "--impacted-unimpacted=skip", "-rs")

    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*test_b.py::test_b SKIPPED (not impacted by the changes)*"])
    result.stdout.no_fnmatch_line("*deselected*")


def test_the_ini_setting_chooses_skip(make_git_project):
    project = make_git_project(FILES, INI + "impacted_unimpacted = skip\n")
    edit(project, "app/a.py")

    run(project).assert_outcomes(passed=1, skipped=1)


def test_the_flag_overrides_the_ini_setting(make_git_project):
    project = make_git_project(FILES, INI + "impacted_unimpacted = skip\n")
    edit(project, "app/a.py")

    run(project, "--impacted-unimpacted=deselect").assert_outcomes(passed=1, deselected=1)


@pytest.mark.parametrize("args", [[], ["--impacted-unimpacted=skip"]], ids=["deselect", "skip"])
def test_a_change_impacting_nothing_exits_0(project, args):
    edit(project, "docs/notes.md")

    result = run(project, *args)

    assert result.ret == ExitCode.OK
    result.assert_outcomes(**{"deselected" if not args else "skipped": 2})


def test_without_impacted_nothing_is_deselected(project):
    edit(project, "app/a.py")

    result = project.runpytest("-p", "no:cacheprovider")

    result.assert_outcomes(passed=2)
    result.stdout.no_fnmatch_line("*pytest-impacted: * deselected*")


def test_a_run_another_filter_empties_still_exits_5(project):
    """Only a run *this plugin* emptied exits 0: ``-k`` deselecting the rest is pytest's business."""
    edit(project, "app/a.py")

    result = run(project, "-k", "no_such_test")

    assert result.ret == ExitCode.NO_TESTS_COLLECTED


def test_a_suite_without_tests_still_exits_5(make_git_project):
    project = make_git_project({"app/__init__.py": "", "suite/__init__.py": ""}, INI)
    edit(project, "app/__init__.py")

    assert run(project).ret == ExitCode.NO_TESTS_COLLECTED


def test_an_unknown_value_is_a_usage_error(make_git_project):
    project = make_git_project(FILES, INI + "impacted_unimpacted = hide\n")

    result = run(project)

    assert result.ret == ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*impacted_unimpacted*'hide'*deselect*skip*"])


@pytest.mark.parametrize(
    ("edited", "outcomes", "line"),
    [
        pytest.param("app/a.py", {"passed": 1}, NOT_IMPACTED, id="some"),
        pytest.param(
            "docs/notes.md", {}, "pytest-impacted: 2 tests not impacted by the changes, deselected", id="none"
        ),
    ],
)
def test_under_xdist(project, edited, outcomes, line):
    """The workers deselect, and the controller exits and reports as a run without xdist does."""
    pytest.importorskip("xdist")
    edit(project, edited)

    result = project.runpytest_subprocess(
        "--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-n", "2"
    )

    assert result.ret == ExitCode.OK
    result.assert_outcomes(**outcomes)
    result.stdout.fnmatch_lines([line])
