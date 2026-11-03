"""End-to-end tests for ``--impacted-early-filter``: unimpacted test files are never imported.

The analysis runs when collection starts, and ``pytest_ignore_collect`` passes over a test
file it does not impact, so its module-level code never runs: on a large suite, importing
every test module is most of a run whose changes impact few. Opt-in: an item can belong to a
file other than its own (an inherited test whose base changed with no import edge), and a
file never collected cannot be matched that way.
"""

import pytest
from pytest import ExitCode

from pytest_impacted import plugin

from .git_helpers import edit_file as edit


# Each test module says it was imported, by leaving a file behind.
IMPORTED = "open({name!r}, 'w').close()\n"
FILES = {
    "app/__init__.py": "",
    "app/a.py": "def a():\n    return 1\n",
    "app/b.py": "def b():\n    '''\n    >>> b()\n    2\n    '''\n    return 2\n",
    "suite/test_a.py": IMPORTED.format(name="imported-a")
    + "from app.a import a\n\ndef test_a():\n    assert a() == 1\n",
    "suite/test_b.py": IMPORTED.format(name="imported-b")
    + "from app.b import b\n\ndef test_b():\n    assert b() == 2\n",
    "docs/notes.md": "notes\n",
}
INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"


def run(project, *args):
    return project.runpytest(
        "--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", "--impacted-early-filter", *args
    )


@pytest.fixture
def project(make_git_project):
    return make_git_project(FILES, INI)


def imported(project):
    return {name for name in ("imported-a", "imported-b") if (project.path / name).exists()}


def test_an_unimpacted_test_file_is_never_imported(project):
    edit(project, "app/a.py")

    result = run(project)

    result.assert_outcomes(passed=1)
    assert imported(project) == {"imported-a"}
    result.stdout.fnmatch_lines(["pytest-impacted: 1 test file not impacted by the changes, not collected"])


def test_without_it_every_test_file_is_imported(project):
    edit(project, "app/a.py")

    project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged")

    assert imported(project) == {"imported-a", "imported-b"}


def test_a_change_impacting_nothing_exits_0(project):
    edit(project, "docs/notes.md")

    result = run(project)

    assert result.ret == ExitCode.OK
    assert imported(project) == set()


def test_a_file_named_on_the_command_line_is_collected(project):
    """pytest never asks about the paths it was given: the file is collected, then deselected."""
    edit(project, "app/a.py")

    result = run(project, "suite/test_b.py")

    assert imported(project) == {"imported-b"}
    result.assert_outcomes(deselected=1)


def test_doctest_modules_turn_it_off(project):
    """Doctests always run, and come from modules import analysis does not rank."""
    edit(project, "app/a.py")

    result = run(project, "--doctest-modules")

    assert imported(project) == {"imported-a", "imported-b"}
    result.stdout.fnmatch_lines(["*app/b.py::app.b.b PASSED*"])


def test_it_needs_deselection(project):
    result = run(project, "--impacted-unimpacted=skip")

    assert result.ret == ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--impacted-early-filter*deselect*"])


def test_the_ini_setting_turns_it_on(make_git_project):
    project = make_git_project(FILES, INI + "impacted_early_filter = true\n")
    edit(project, "app/a.py")

    project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged")

    assert imported(project) == {"imported-a"}


def test_the_analysis_runs_once(project, monkeypatch):
    """Collection asks first, and the items are then filtered by the same answer."""
    calls = []
    original = plugin.get_impacted_tests
    monkeypatch.setattr(plugin, "get_impacted_tests", lambda **kwargs: calls.append(1) or original(**kwargs))
    edit(project, "app/a.py")

    run(project).assert_outcomes(passed=1)

    assert len(calls) == 1


def test_under_xdist_the_workers_ignore_by_the_controller_s_analysis(project):
    pytest.importorskip("xdist")
    edit(project, "app/a.py")

    result = project.runpytest_subprocess(
        "--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "--impacted-early-filter", "-n", "2"
    )

    assert result.ret == ExitCode.OK
    result.assert_outcomes(passed=1)
    assert imported(project) == {"imported-a"}


def test_a_k_matching_nothing_still_exits_5(project):
    edit(project, "app/a.py")

    assert run(project, "-k", "no_such_test").ret == ExitCode.NO_TESTS_COLLECTED


def test_an_analysis_error_fails_the_run_as_without_the_filter(project, monkeypatch):
    """Once, from the items' hook, not as a collection error of every test directory."""
    calls = []

    def broken(**kwargs):
        calls.append(1)
        raise RuntimeError("the analysis broke")

    monkeypatch.setattr(plugin, "get_impacted_tests", broken)
    edit(project, "app/a.py")

    result = run(project)

    assert result.ret == ExitCode.INTERNAL_ERROR
    result.stdout.no_fnmatch_line("*ERROR collecting*")
    assert len(calls) == 2  # the early filter's, then the items' as without it


def test_with_an_extension_it_filters_nothing(project, monkeypatch):
    """An extension may pick tests from ``session.items``, which is empty this early."""
    monkeypatch.setattr(
        "pytest_impacted.extensions.discover_extension_metadata",
        lambda: [type("Ext", (), {"name": "demo", "config_options": []})()],
    )
    edit(project, "app/a.py")

    run(project)

    assert imported(project) == {"imported-a", "imported-b"}


def test_conftests_and_init_files_are_never_passed_over(make_git_project):
    files = {**FILES, "suite/__init__.py": IMPORTED.format(name="imported-init"), "suite/conftest.py": ""}
    project = make_git_project(files, INI + "python_files = *.py\n")
    edit(project, "app/a.py")

    run(project)

    assert (project.path / "imported-init").exists()


def test_python_files_are_matched_as_pytest_matches_them(make_git_project):
    """A pattern with a ``/`` is matched against the path, as pytest does, not the name."""
    project = make_git_project(FILES, INI + "python_files = suite/test_*.py\n")
    edit(project, "app/a.py")

    run(project)

    assert imported(project) == {"imported-a"}


def test_an_invalid_unimpacted_value_is_named_as_such(project):
    result = run(project, "-o", "impacted_unimpacted=deslect")

    assert result.ret == ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*Invalid impacted_unimpacted value 'deslect'*"])
