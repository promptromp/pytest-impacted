"""End-to-end tests for what happens to tests no change impacts: deselected, or skipped.

They are deselected by default (pytest's idiom: ``-k`` and ``-m`` do the same), and
``--impacted-unimpacted=skip`` marks them skipped instead, as releases up to 0.35 did.
A run that deselects every test exits 0, as one that skips every test does: a change that
impacts nothing is no failure, while pytest exits 5 when no test runs.
"""

from types import SimpleNamespace

import pytest
from pytest import ExitCode

from pytest_impacted.plugin import _DESELECTION, _UNIMPACTED_ITEMS, _Deselection, pytest_deselect_unimpacted

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


@pytest.mark.parametrize("edited", ["app/a.py", "docs/notes.md"], ids=["impacting_one", "impacting_none"])
@pytest.mark.parametrize("args", [[], ["--impacted-unimpacted=skip"]], ids=["deselect", "skip"])
def test_a_run_another_filter_empties_still_exits_5(project, edited, args):
    """Only a run *this plugin* emptied exits 0: a ``-k`` that matches nothing is a mistake to report.

    Also when the changes impact nothing, whichever of the two filters pytest runs first.
    """
    edit(project, edited)

    result = run(project, "-k", "no_such_test", *args)

    assert result.ret == ExitCode.NO_TESTS_COLLECTED


@pytest.mark.parametrize("args", [[], ["--impacted-unimpacted=skip"]], ids=["deselect", "skip"])
def test_m_impacted_selects_the_impacted_tests(project, args):
    """The ``impacted`` marker is added before ``-m`` filters, so ``-m impacted`` keeps them."""
    edit(project, "app/a.py")

    result = run(project, "-m", "impacted", *args)

    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines(["*test_a.py::test_a PASSED*"])


def test_an_item_added_after_the_judging_is_kept():
    """An item a plugin adds between the marking and the deselection was never judged, so it runs.

    Called directly: pytest offers no way to place a hook between two others.
    """
    judged, added, impacted = object(), object(), object()
    deselected = []
    config = SimpleNamespace(
        stash=pytest.Stash(), hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items))
    )
    config.stash[_UNIMPACTED_ITEMS] = {judged}
    items = [impacted, judged, added]

    pytest_deselect_unimpacted(config, items)

    assert (items, deselected) == ([impacted, added], [judged])
    assert config.stash[_DESELECTION] == _Deselection(count=1)


def test_only_tests_left_by_k_are_deselected(project):
    """``-k`` keeps ``test_b``, which nothing impacts: deselecting it empties the run, which passes."""
    edit(project, "app/a.py")

    result = run(project, "-k", "test_b")

    assert result.ret == ExitCode.OK
    result.assert_outcomes(deselected=2)
    result.stdout.fnmatch_lines([NOT_IMPACTED])


def test_a_shard_left_with_only_deselected_tests_passes(make_git_project):
    """A sharding plugin running after this one may hand a shard none of the impacted tests.

    The shard is empty because this plugin deselected what it would have run, so it
    passes, as a shard of skipped tests did.
    """
    shard = (
        "import pytest\n\n@pytest.hookimpl(wrapper=True)\ndef pytest_collection_modifyitems(config, items):\n"
        "    yield\n    config.hook.pytest_deselected(items=list(items))\n    items[:] = []\n"
    )
    project = make_git_project({**FILES, "conftest.py": shard}, INI)
    edit(project, "app/a.py")

    assert run(project).ret == ExitCode.OK


def test_other_plugins_see_the_status_of_an_emptied_run(make_git_project):
    """Their ``pytest_sessionfinish`` runs after this plugin's, which has made the status 0."""
    seen = "def pytest_sessionfinish(session):\n    print('status seen:', int(session.exitstatus))\n"
    project = make_git_project({**FILES, "conftest.py": seen}, INI)
    edit(project, "docs/notes.md")

    result = run(project, "-s")

    assert result.ret == ExitCode.OK
    result.stdout.fnmatch_lines(["*status seen: 0*"])


def test_a_failure_another_plugin_reports_is_kept(make_git_project):
    """A conftest setting a failing status in ``pytest_sessionfinish`` must not be turned into 0."""
    project = make_git_project(
        {**FILES, "conftest.py": "def pytest_sessionfinish(session):\n    session.exitstatus = 3\n"}, INI
    )
    edit(project, "docs/notes.md")

    assert run(project).ret == 3


def test_qq_hides_the_summary_line(project):
    edit(project, "app/a.py")

    result = project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-qq")

    assert result.ret == ExitCode.OK
    result.stdout.no_fnmatch_line("*pytest-impacted:*deselected*")


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


def test_under_xdist_the_controller_analyses_once_for_every_worker(make_git_project):
    """Workers analysing for themselves could disagree, and xdist aborts a run whose workers
    collect different tests: here each writes a file the invalidation glob matches before it
    collects. The controller's analysis, made before any worker starts, is every worker's.
    Its notices reach the terminal too, which a worker's never do.
    """
    pytest.importorskip("xdist")
    # By workerinput: PYTEST_XDIST_WORKER is inherited from this suite's own workers too.
    writes = (
        "def pytest_configure(config):\n"
        "    if hasattr(config, 'workerinput'):\n"
        "        open('late.json', 'w').close()\n"
    )
    project = make_git_project({**FILES, "conftest.py": writes}, INI + "impacted_invalidate_all = *.json\n")
    edit(project, "app/a.py")

    result = project.runpytest_subprocess(
        "--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-n", "2"
    )

    assert result.ret == ExitCode.OK
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*Impacted files in the repository: ['app/a.py'*", NOT_IMPACTED])
