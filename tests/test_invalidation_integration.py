"""End-to-end tests for user-supplied invalidation patterns via pytester and a real git repo."""

import subprocess
import textwrap

import pytest

from pytest_impacted.strategies import clear_dep_tree_cache


@pytest.fixture
def git_project(pytester):
    """A committed project with a package, two test directories, and non-Python data files.

    Returns a helper that modifies a file in the working tree (unstaged) so that
    ``unstaged`` git mode sees exactly that change. pytester runs in-process, so
    the module-name-keyed dependency-tree cache is cleared to keep runs isolated.
    """
    clear_dep_tree_cache()
    pytester.mkpydir("pkg")
    pytester.makepyfile(**{"pkg/core": "def add(a, b):\n    return a + b\n"})
    pytester.mkdir("tests")
    pytester.mkdir("tests/unit")
    pytester.mkdir("tests/integration")
    pytester.makepyfile(
        **{
            "tests/unit/test_core": "from pkg.core import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
            "tests/integration/test_api": "from pkg.core import add\n\ndef test_api():\n    assert add(0, 0) == 0\n",
        }
    )
    pytester.mkdir("config")
    data_files = {
        "config/settings.json": "{}",
        "tests/unit/fixture.json": "{}",
        "tests/integration/schema.sql": "select 1;",
    }
    for rel, content in data_files.items():
        (pytester.path / rel).write_text(content)
    (pytester.path / ".gitignore").write_text("__pycache__/\n")
    pytester.makeini(INI)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=pytester.path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-q", "-m", "init")

    def touch(rel: str) -> None:
        path = pytester.path / rel
        path.write_text(path.read_text() + "\n")

    pytester.touch = touch
    yield pytester
    clear_dep_tree_cache()


INI = textwrap.dedent(
    """
    [pytest]
    pythonpath = .
    impacted_module = pkg
    impacted_tests_dir = tests
    """
)


def run(pytester, *args):
    return pytester.runpytest("--impacted", "-p", "no:cacheprovider", *args)


def test_json_change_is_ignored_by_default(git_project):
    git_project.touch("config/settings.json")
    run(git_project).assert_outcomes(skipped=2)


def test_invalidate_all_runs_every_test(git_project):
    git_project.touch("config/settings.json")
    result = run(git_project, "--impacted-invalidate-all=*.json")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*Invalidation file changes detected*config/settings.json*"])


def test_invalidate_all_dir_scoped_glob_does_not_match_elsewhere(git_project):
    git_project.touch("config/settings.json")
    run(git_project, "--impacted-invalidate-all=other/*.json").assert_outcomes(skipped=2)


def test_invalidate_dir_runs_only_tests_under_changed_file(git_project):
    git_project.touch("tests/unit/fixture.json")
    result = run(git_project, "--impacted-invalidate-dir=*.json", "-v")
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*tests/unit/test_core.py::test_add PASSED*"])


def test_invalidate_dir_outside_test_tree_runs_nothing(git_project):
    git_project.touch("config/settings.json")
    run(git_project, "--impacted-invalidate-dir=*.json").assert_outcomes(skipped=2)


def test_patterns_from_ini(git_project):
    git_project.makeini(INI + "impacted_invalidate_all = *.json config/*.yaml\nimpacted_invalidate_dir = *.sql\n")
    git_project.touch("tests/integration/schema.sql")
    result = run(git_project, "-v")
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*tests/integration/test_api.py::test_api PASSED*"])

    git_project.touch("config/settings.json")
    run(git_project).assert_outcomes(passed=2)


def test_no_dep_files_flag_does_not_disable_invalidation(git_project):
    """The two features are independent: disabling built-in dep files keeps user rules active."""
    git_project.touch("config/settings.json")
    run(git_project, "--impacted-invalidate-all=*.json", "--no-impacted-dep-files").assert_outcomes(passed=2)


def test_help_lists_options(pytester):
    result = pytester.runpytest("--help")
    result.stdout.fnmatch_lines(["*--impacted-invalidate-all=PATTERN*", "*--impacted-invalidate-dir=PATTERN*"])
