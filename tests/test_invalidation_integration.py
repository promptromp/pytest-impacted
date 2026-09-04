"""End-to-end tests for user-supplied invalidation patterns via pytester and a real git repo."""

import os
import subprocess
import sys
import textwrap

import pytest

from pytest_impacted.strategies import clear_dep_tree_cache

from .conftest import isolated_git_env


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
        "config/app.yaml": "key: value\n",
    }
    for rel, content in data_files.items():
        (pytester.path / rel).write_text(content)
    (pytester.path / ".gitignore").write_text("__pycache__/\n")
    pytester.makeini(INI)

    env = {**os.environ, **isolated_git_env(pytester.path / "git-home")}

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=pytester.path, env=env, check=True, capture_output=True)

    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "init")

    def touch(rel: str) -> None:
        path = pytester.path / rel
        path.write_text(path.read_text() + "\n")

    pytester.touch = touch
    pytester.git = git
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


def test_patterns_from_ini(git_project):
    """Patterns configured in the ini file are applied, and every one of them counts."""
    git_project.makeini(INI + "impacted_invalidate_all = *.json config/*.yaml\n")
    # Matched by the second pattern only — the first (*.json) does not match a .yaml file.
    git_project.touch("config/app.yaml")
    run(git_project).assert_outcomes(passed=2)


def test_no_dep_files_flag_does_not_disable_invalidation(git_project):
    """The two features are independent: disabling built-in dep files keeps user rules active."""
    git_project.touch("config/settings.json")
    run(git_project, "--impacted-invalidate-all=*.json", "--no-impacted-dep-files").assert_outcomes(passed=2)


def test_help_lists_options(pytester):
    result = pytester.runpytest("--help")
    result.stdout.fnmatch_lines(["*--impacted-invalidate-all=PATTERN*"])


def test_runs_from_a_subdirectory_of_the_rootdir(git_project):
    """Paths resolve against the rootdir, so running from elsewhere must select the same tests.

    Driven through a real subprocess with ``cwd`` set to a subdirectory —
    ``runpytest_subprocess`` always runs in the rootdir, where a
    working-directory regression is invisible.
    """
    git_project.touch("pkg/core.py")
    root = git_project.path

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--impacted",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(root),
            "-q",
            str(root / "tests"),
        ],
        cwd=root / "tests" / "unit",
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert "2 passed" in result.stdout, result.stdout + result.stderr


def test_deleted_data_file_still_invalidates_every_test(git_project):
    """A deleted file reaches the pattern strategies: it is gone, so anything depending on it may break."""
    git_project.git("rm", "-q", "config/settings.json")

    result = run(git_project, "--impacted-invalidate-all=*.json")

    result.assert_outcomes(passed=2)


def test_deleted_conftest_impacts_the_tests_below_it(git_project):
    """Removing a conftest.py removes its fixtures; those tests must run (and fail) rather than be skipped."""
    (git_project.path / "tests" / "unit" / "conftest.py").write_text("")
    git_project.git("add", "tests/unit/conftest.py")
    git_project.git("commit", "-q", "-m", "add conftest")
    git_project.git("rm", "-q", "tests/unit/conftest.py")

    result = run(git_project)

    result.assert_outcomes(passed=1, skipped=1)
