"""What a user sees when the run cannot go ahead: one clear line and a usage error, never a traceback.

And the notices that say why every test runs, wherever pytest leaves out the report header that carries them.
"""

import sys
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from pytest_impacted.cli import impacted_tests_cli
from pytest_impacted.extensions import ConfigOption, clear_extension_cache
from pytest_impacted.strategies import ImpactStrategy

from .git_helpers import edit_file, write_files


FILES = {
    "app/__init__.py": "",
    "app/a.py": "def a():\n    return 1\n",
    "suite/test_a.py": "from app.a import a\n\ndef test_a():\n    assert a() == 1\n",
}
INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"


def assert_usage_error(result, *messages):
    assert result.ret == pytest.ExitCode.USAGE_ERROR, result.stdout.str() + result.stderr.str()
    output = result.stdout.str() + result.stderr.str()
    assert "INTERNALERROR" not in output and "Traceback" not in output, output
    for message in messages:
        assert message in output, output


def test_a_git_mode_setting_that_is_no_mode_is_a_usage_error(make_git_project):
    """``choices`` guards the flag alone; the setting reached ``match`` and raised."""
    project = make_git_project(FILES, INI + "impacted_git_mode = Branch\nimpacted_base_branch = main\n")

    assert_usage_error(project.runpytest("--impacted"), "Invalid impacted_git_mode value 'Branch'")


@pytest.mark.parametrize("mode", ["unstaged", "branch"])
def test_a_directory_outside_any_repository_is_a_usage_error(pytester, mode):
    write_files(pytester.path, FILES)
    pytester.makeini(INI)

    result = pytester.runpytest("--impacted", f"--impacted-git-mode={mode}", "--impacted-base-branch=main")

    assert_usage_error(result, "No git repository found")


@pytest.mark.parametrize("mode", ["unstaged", "branch"])
def test_a_repository_git_refuses_is_a_usage_error_naming_git_s_reason(make_git_project, monkeypatch, mode):
    """In a CI container git often refuses a checkout another user owns; say so, not "no such branch"."""
    project = make_git_project(FILES, INI)
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")

    result = project.runpytest("--impacted", f"--impacted-git-mode={mode}", "--impacted-base-branch=main")

    assert_usage_error(result, "dubious ownership")


class Demo(ImpactStrategy):
    config_options: ClassVar = [ConfigOption(name="threshold", help="A number.", type=int, default=1)]

    def __init__(self, threshold: int = 1):
        self.threshold = threshold

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return []


@pytest.fixture
def demo_extension(monkeypatch):
    entry_point = MagicMock(value=f"{__name__}:Demo")
    entry_point.name = "demo"
    entry_point.load.return_value = Demo
    monkeypatch.setattr("pytest_impacted.extensions.importlib.metadata.entry_points", lambda **_: [entry_point])
    clear_extension_cache()
    yield
    clear_extension_cache()


@pytest.mark.usefixtures("demo_extension")
def test_an_extension_setting_that_will_not_convert_is_a_usage_error(make_git_project):
    project = make_git_project(FILES, INI)

    result = project.runpytest("--impacted", "--impacted-git-mode=unstaged", "--impacted-ext-demo-threshold=abc")

    assert_usage_error(result, "Extension 'demo': invalid value 'abc' for option 'threshold'")


@pytest.fixture
def git_missing_at_import(monkeypatch):
    """As when ``import git`` failed: the plugin knows it, and the analysis cannot run git."""
    monkeypatch.setattr("pytest_impacted.plugin.GIT_AVAILABLE", False)
    monkeypatch.setattr("pytest_impacted.git.GIT_AVAILABLE", False)


@pytest.mark.usefixtures("git_missing_at_import")
@pytest.mark.parametrize("args", [(), ("-q",), ("-qq",), ("--no-header",)], ids=["", "-q", "-qq", "--no-header"])
def test_the_reason_every_test_runs_is_said_once_with_the_header_or_without(make_git_project, args):
    """pytest leaves the report header out under ``-q``; the notice it carries goes in the summary instead."""
    project = make_git_project(FILES, INI)
    edit_file(project, "app/a.py")

    result = project.runpytest("--impacted", "--impacted-git-mode=unstaged", "--impacted-tests-dir=suite", *args)

    assert result.ret == pytest.ExitCode.OK
    if "-qq" not in args:  # which prints no outcomes to parse
        result.assert_outcomes(passed=1)
    assert result.stdout.str().count("Running every test") == 1, result.stdout.str()


@pytest.mark.parametrize(
    ("setup", "args", "message"),
    [
        ("none", ["--git-mode", "unstaged"], "No git repository found"),
        ("repo", ["--git-mode", "branch", "--base-branch", "no-such-branch"], "no-such-branch"),
    ],
    ids=["outside a repository", "an unknown base branch"],
)
def test_the_cli_reports_a_git_failure_in_one_line(make_git_project, pytester, monkeypatch, setup, args, message):
    if setup == "repo":
        make_git_project(FILES, INI)
    else:
        write_files(pytester.path, FILES)
    monkeypatch.chdir(pytester.path)
    edit_file(pytester, "app/a.py")

    result = CliRunner().invoke(impacted_tests_cli, ["--module", "app", "--tests-dir", "suite", *args])

    assert result.exit_code == 1, result.output
    assert message in result.output, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception


@pytest.mark.skipif(sys.platform == "win32", reason="git's ownership check is POSIX")
def test_the_cli_reports_git_refusing_the_repository(make_git_project, pytester, monkeypatch):
    make_git_project(FILES, INI)
    monkeypatch.chdir(pytester.path)
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")

    result = CliRunner().invoke(impacted_tests_cli, ["--module", "app", "--tests-dir", "suite"])

    assert result.exit_code == 1, result.output
    assert "dubious ownership" in result.output, result.output
