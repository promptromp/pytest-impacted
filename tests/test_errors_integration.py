"""What a user sees when the run cannot go ahead: one clear line and a usage error, never a traceback.

And the notices that say why every test runs, wherever pytest leaves out the report header that carries them.
"""

import re
import subprocess
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


def _git_version() -> tuple[int, ...]:
    output = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout
    return tuple(int(part) for part in re.findall(r"\d+", output)[:3])


#: git honours GIT_TEST_ASSUME_DIFFERENT_OWNER from 2.35.2; the project supports git 2.24 and later.
pretends_another_owner = pytest.mark.skipif(
    sys.platform == "win32" or _git_version() < (2, 35, 2), reason="needs git's ownership check (POSIX, git 2.35.2+)"
)


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


@pretends_another_owner
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

    assert result.exit_code == 2, result.output  # a usage error, unlike git missing (exit 1)
    assert message in result.output, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception


@pretends_another_owner
def test_the_cli_reports_git_refusing_the_repository(make_git_project, pytester, monkeypatch):
    make_git_project(FILES, INI)
    monkeypatch.chdir(pytester.path)
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")

    result = CliRunner().invoke(impacted_tests_cli, ["--module", "app", "--tests-dir", "suite"])

    assert result.exit_code == 2, result.output
    assert "dubious ownership" in result.output, result.output


def test_help_is_shown_whatever_the_settings_and_wherever_it_runs(pytester):
    """``--impacted`` in addopts, outside any repository (an unpacked sdist): ``--help`` still helps."""
    write_files(pytester.path, FILES)
    pytester.makeini(INI + "addopts = --impacted --impacted-git-mode=unstaged\n")

    result = pytester.runpytest("--help")

    assert result.ret == pytest.ExitCode.OK, result.stdout.str() + result.stderr.str()
    result.stdout.fnmatch_lines(["*--impacted-module*"])


class Failing(ImpactStrategy):
    """An extension broken in one of the ways it can be: a required setting missing, or raising in a hook."""

    config_options: ClassVar = [ConfigOption(name="fail", help="Where to fail.", type=str, default="enrich")]

    def __init__(self, fail: str = "enrich"):
        if fail == "init":
            raise RuntimeError("boom in __init__")
        self.fail = fail

    def enrich_dep_tree(self, dep_tree, **kwargs):
        if self.fail == "enrich":
            raise RuntimeError("boom in enrich_dep_tree")

    def setup(self, **kwargs):
        if self.fail == "setup":
            raise RuntimeError("boom in setup")

    def teardown(self):
        if self.fail == "teardown":
            raise RuntimeError("boom in teardown")
        if self.fail == "unprintable":
            raise Unprintable()

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return []


class Unprintable(Exception):
    def __str__(self):
        raise AttributeError("never set")


class NeedsAKey(Failing):
    config_options: ClassVar = [ConfigOption(name="key", help="Required.", type=str, required=True)]

    def __init__(self, key: str):
        super().__init__(fail="none")


@pytest.fixture
def broken_extension(request, monkeypatch):
    """Installs the extension class the test is parametrized with (``indirect``), as the entry point ``broken``."""
    entry_point = MagicMock(value=f"{__name__}:{request.param.__name__}")
    entry_point.name = "broken"
    entry_point.load.return_value = request.param
    monkeypatch.setattr("pytest_impacted.extensions.importlib.metadata.entry_points", lambda **_: [entry_point])
    clear_extension_cache()
    yield
    clear_extension_cache()


@pytest.mark.parametrize(
    ("broken_extension", "args", "reason"),
    [
        (NeedsAKey, [], "missing required config option(s): key"),
        (Failing, ["--impacted-ext-broken-fail=init"], "boom in __init__"),
        (Failing, ["--impacted-ext-broken-fail=enrich"], "boom in enrich_dep_tree"),
        (Failing, ["--impacted-ext-broken-fail=setup"], "boom in setup"),
        (Failing, ["--impacted-ext-broken-fail=teardown"], "boom in teardown"),
        (Failing, ["--impacted-ext-broken-fail=unprintable"], "Unprintable: <unprintable>"),
    ],
    ids=[
        "a required setting missing",
        "__init__ raises",
        "enrich raises",
        "setup raises",
        "teardown raises",
        "an exception that cannot be printed",
    ],
    indirect=["broken_extension"],
)
@pytest.mark.usefixtures("broken_extension")
def test_a_broken_extension_is_named_on_the_terminal_and_the_run_goes_on(make_git_project, args, reason):
    """Its rule then contributes nothing, so fewer tests may run: the user must see it, not only a log record."""
    project = make_git_project(FILES, INI)
    edit_file(project, "app/a.py")

    result = project.runpytest("--impacted", "--impacted-git-mode=unstaged", "-p", "no:logging", *args)

    result.assert_outcomes(passed=1)
    # Named by its entry point when loading, by its class once loaded (the composite knows no entry points).
    warnings = [line for line in result.stdout.lines if "WARNING" in line and ("'broken'" in line or "Failing" in line)]
    assert len(warnings) == 1, result.stdout.str()
    assert reason in warnings[0], warnings[0]
