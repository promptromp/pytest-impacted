"""Unit tests for the CLI module."""

import logging
import os
import sys
from pathlib import Path
from typing import NamedTuple
from unittest.mock import ANY, MagicMock, patch

import pytest
from click.testing import CliRunner

from pytest_impacted.api import build_strategy_with_extensions
from pytest_impacted.cli import configure_logging, impacted_tests_cli
from pytest_impacted.git import GitMode, GitUnavailableError
from pytest_impacted.strategies import CompositeImpactStrategy

from .git_helpers import edit_file


class TestConfigureLogging:
    """Tests for the configure_logging function."""

    def test_configure_logging_verbose_true(self):
        """Test that verbose=True sets DEBUG level."""
        with patch("logging.basicConfig") as mock_basic_config:
            configure_logging(verbose=True)

            mock_basic_config.assert_called_once()
            call_args = mock_basic_config.call_args
            assert call_args[1]["level"] == logging.DEBUG

    def test_configure_logging_verbose_false(self):
        """Test that verbose=False sets INFO level."""
        with patch("logging.basicConfig") as mock_basic_config:
            configure_logging(verbose=False)

            mock_basic_config.assert_called_once()
            call_args = mock_basic_config.call_args
            assert call_args[1]["level"] == logging.INFO

    def test_configure_logging_format(self):
        """Test that logging is configured with the expected format."""
        with patch("logging.basicConfig") as mock_basic_config:
            configure_logging(verbose=False)

            mock_basic_config.assert_called_once()
            call_args = mock_basic_config.call_args
            assert call_args[1]["format"] == "%(funcName)-20s | %(message)s"
            assert call_args[1]["datefmt"] == "[%x]"


#: What the CLI hands ``get_impacted_tests`` for ``--module test_ns`` and nothing else.
DEFAULT_CALL = {
    "impacted_git_mode": GitMode.UNSTAGED,
    "impacted_base_branch": "main",
    "root_dir": ".",
    "ns_module": "test_ns",
    "tests_dir": None,
    "strategy": ANY,
    "use_merge_base": True,
    "file_map": [],
    "pythonpath": [],
    "ignore_type_checking_imports": False,
    "backend": "auto",
}

#: What it hands ``build_strategy_with_extensions`` then.
DEFAULT_STRATEGY = {
    "watch_dep_files": True,
    "invalidate_all_patterns": (),
    "conftest_imports": False,
    "disabled": (),
    "ext_config": {},
    "config_file": None,
}


class Cli(NamedTuple):
    """The CLI with its analysis stubbed: ``run`` is ``get_impacted_tests``, ``strategy`` the (real, recorded)
    ``build_strategy_with_extensions``, ``logging`` ``configure_logging``."""

    run: MagicMock
    strategy: MagicMock
    logging: MagicMock

    def invoke(self, *args: str):
        return CliRunner().invoke(impacted_tests_cli, ["--module", "test_ns", *args])


@pytest.fixture
def cli(tmp_path, monkeypatch, entry_points):
    """Run from an empty directory holding ``test_ns/`` and ``tests/``, so ``--root-dir .`` (the default) lands there
    and no pytest configuration is found, and no strategy extension is installed; two impacted tests unless a test
    sets ``cli.run``'s result."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_ns").mkdir()
    (tmp_path / "tests").mkdir()
    impacted = ["tests/test_example.py", "tests/test_other.py"]
    with (
        patch("pytest_impacted.cli.get_impacted_tests", return_value=impacted) as run,
        patch("pytest_impacted.cli.build_strategy_with_extensions", wraps=build_strategy_with_extensions) as strategy,
        patch("pytest_impacted.cli.configure_logging") as logging_setup,
    ):
        yield Cli(run, strategy, logging_setup)


@pytest.mark.parametrize(
    ("args", "call", "echoed"),
    [
        pytest.param([], {}, ["git-mode: unstaged", "base-branch: main", "tests-dir: None"], id="defaults"),
        pytest.param(
            ["--git-mode", "unstaged", "--base-branch", "main", "--root-dir", "."],
            {},
            ["git-mode: unstaged", "base-branch: main", "root-dir: ."],
            id="defaults spelled",
        ),
        pytest.param(
            ["--git-mode", "branch", "--base-branch", "develop", "--tests-dir", "tests"],
            {"impacted_git_mode": GitMode.BRANCH, "impacted_base_branch": "develop", "tests_dir": "tests"},
            ["git-mode: branch", "base-branch: develop", "tests-dir: tests"],
            id="branch mode, a tests dir",
        ),
        pytest.param(
            ["--git-mode", "branch", "--no-merge-base"],
            {"impacted_git_mode": GitMode.BRANCH, "use_merge_base": False},
            ["git-mode: branch", "no-merge-base: True"],
            id="no merge base",
        ),
        pytest.param(
            ["--ignore-type-checking-imports"],
            {"ignore_type_checking_imports": True},
            ["ignore-type-checking-imports: True"],
            id="type-only",
        ),
        pytest.param(["--backend", "python"], {"backend": "python"}, ["backend: python"], id="the python backend"),
    ],
)
def test_the_cli_hands_the_analysis_its_options_and_says_so(cli, args, call, echoed):
    result = cli.invoke(*args)

    assert result.exit_code == 0, result.output
    cli.run.assert_called_once_with(**(DEFAULT_CALL | call))
    assert isinstance(cli.run.call_args.kwargs["strategy"], CompositeImpactStrategy)
    for line in ["impacted-tests", "module: test_ns", *echoed]:
        assert line in result.stderr


@pytest.mark.parametrize(
    ("args", "strategy", "echoed"),
    [
        pytest.param([], {}, [], id="defaults"),
        pytest.param(
            ["--invalidate-all", "*.json", "--invalidate-all", "config/*.yaml"],
            {"invalidate_all_patterns": ("*.json", "config/*.yaml")},
            ["invalidate-all: *.json, config/*.yaml"],
            id="invalidate-all, repeatable",
        ),
        pytest.param(["--conftest-imports"], {"conftest_imports": True}, ["conftest-imports: True"], id="conftest"),
        pytest.param(["--no-dep-files"], {"watch_dep_files": False}, ["no-dep-files: True"], id="no dep files"),
    ],
)
def test_the_cli_hands_the_strategy_builder_its_options_and_says_so(cli, args, strategy, echoed):
    result = cli.invoke(*args)

    assert result.exit_code == 0, result.output
    cli.strategy.assert_called_once_with(**(DEFAULT_STRATEGY | strategy))
    for line in ["impacted-tests", "git-mode: unstaged", "base-branch: main", "module: test_ns", *echoed]:
        assert line in result.stderr


@pytest.mark.parametrize("verbose", [True, False])
def test_the_cli_logs_verbosely_on_request(cli, verbose):
    result = cli.invoke(*(["--verbose"] if verbose else []))

    assert result.exit_code == 0, result.output
    cli.logging.assert_called_once_with(verbose=verbose)


def test_the_cli_prints_the_impacted_tests_alone_on_stdout(cli):
    """A script pipes them into pytest: the settings go to stderr."""
    result = cli.invoke()

    assert result.stdout.split() == ["tests/test_example.py", "tests/test_other.py"]


@pytest.mark.parametrize("found", [None, []], ids=["None", "empty"])
def test_the_cli_says_when_no_test_is_impacted(cli, found):
    cli.run.return_value = found

    result = cli.invoke()

    assert result.exit_code == 0
    assert "No impacted tests found." in result.output


def test_the_cli_fails_when_git_is_unavailable(cli):
    """Unknown changes must not print as "no impacted tests" with exit code 0."""
    cli.run.side_effect = GitUnavailableError()

    result = cli.invoke()

    assert result.exit_code == 1
    assert "changed files cannot be determined" in result.output
    assert "No impacted tests found." not in result.output


class TestImpactedTestsCLI:
    """Tests for the impacted_tests_cli Click command."""

    def setup_method(self):
        """Set up test environment."""
        self.runner = CliRunner()

    @pytest.fixture
    def in_tmp_dir(self, tmp_path, monkeypatch):
        """Run the test from an empty temporary directory, so relative paths such as
        ``--root-dir .`` and ``Path("test_ns")`` land there.

        Replaces ``CliRunner.isolated_filesystem()``, deprecated in Click 8.5.
        """
        monkeypatch.chdir(tmp_path)

    @pytest.mark.usefixtures("in_tmp_dir")
    def test_cli_missing_required_arg(self):
        """Test CLI fails when required argument is missing (from a directory whose configuration sets none)."""
        result = self.runner.invoke(
            impacted_tests_cli,
            [
                "--git-mode",
                "unstaged",
                "--base-branch",
                "main",
                "--root-dir",
                ".",
                # Missing --module
            ],
        )

        assert result.exit_code != 0
        assert "Missing option" in result.output

    @pytest.mark.usefixtures("in_tmp_dir")
    def test_cli_invalid_git_mode(self):
        """Test CLI fails with invalid git mode."""
        Path("test_ns").mkdir()

        result = self.runner.invoke(
            impacted_tests_cli,
            ["--git-mode", "invalid_mode", "--base-branch", "main", "--root-dir", ".", "--module", "test_ns"],
        )

        # Rejected up front as a usage error, not a traceback from deep in git.py
        assert result.exit_code == 2
        assert "is not one of 'unstaged', 'branch'" in result.output

    def test_cli_nonexistent_root_dir(self):
        """Test CLI fails with non-existent root directory."""
        result = self.runner.invoke(
            impacted_tests_cli,
            [
                "--git-mode",
                "unstaged",
                "--base-branch",
                "main",
                "--root-dir",
                "/nonexistent/path",
                "--module",
                "test_ns",
            ],
        )

        assert result.exit_code != 0

    @pytest.mark.usefixtures("in_tmp_dir")
    def test_cli_nonexistent_module(self):
        """Test CLI fails with non-existent namespace module."""
        result = self.runner.invoke(
            impacted_tests_cli,
            [
                "--git-mode",
                "unstaged",
                "--base-branch",
                "main",
                "--root-dir",
                ".",
                "--module",
                "/nonexistent/module",
            ],
        )

        assert result.exit_code != 0

    @pytest.mark.usefixtures("in_tmp_dir")
    def test_cli_nonexistent_tests_dir(self):
        """Test CLI fails with non-existent tests directory."""
        Path("test_ns").mkdir()

        result = self.runner.invoke(
            impacted_tests_cli,
            [
                "--git-mode",
                "unstaged",
                "--base-branch",
                "main",
                "--root-dir",
                ".",
                "--module",
                "test_ns",
                "--tests-dir",
                "/nonexistent/tests",
            ],
        )

        assert result.exit_code != 0

    def test_cli_module_is_relative_to_root_dir(self, tmp_path, monkeypatch):
        """--module names a package under --root-dir, not under the working directory."""
        (tmp_path / "proj" / "pkg").mkdir(parents=True)
        (tmp_path / "elsewhere").mkdir()
        monkeypatch.chdir(tmp_path / "elsewhere")

        with patch("pytest_impacted.cli.get_impacted_tests", return_value=[]):
            ok = self.runner.invoke(impacted_tests_cli, ["--root-dir", str(tmp_path / "proj"), "--module", "pkg"])

        assert ok.exit_code == 0, ok.output

    def test_cli_module_missing_under_root_dir_is_rejected(self, tmp_path):
        """A package that exists only in the working directory must not satisfy --module."""
        (tmp_path / "proj").mkdir()

        result = self.runner.invoke(impacted_tests_cli, ["--root-dir", str(tmp_path / "proj"), "--module", "pkg"])

        assert result.exit_code != 0
        assert "Module 'pkg' not found" in result.output

    def test_cli_accepts_dotted_module_names(self, tmp_path):
        """src-layout projects are addressed as ``src.pkg``, as discover_submodules accepts."""
        (tmp_path / "proj" / "src" / "pkg").mkdir(parents=True)

        with patch("pytest_impacted.cli.get_impacted_tests", return_value=[]):
            result = self.runner.invoke(
                impacted_tests_cli, ["--root-dir", str(tmp_path / "proj"), "--module", "src.pkg"]
            )

        assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("directory", "tests_dir", "accepted"),
    [
        ("tests/unit", "tests.unit", True),  # a dotted spelling of a real path
        ("tests.unit", "tests.unit", False),  # a directory with a dot in its name: walked as tests/unit
        (".tests", ".tests", False),
    ],
    ids=["dotted spelling", "dot in the name", "hidden directory"],
)
def test_the_cli_refuses_a_tests_dir_the_analysis_cannot_name(tmp_path, directory, tests_dir, accepted):
    (tmp_path / "pkg").mkdir()
    (tmp_path / directory).mkdir(parents=True)

    with patch("pytest_impacted.cli.get_impacted_tests", return_value=[]):
        result = CliRunner().invoke(
            impacted_tests_cli, ["--root-dir", str(tmp_path), "--module", "pkg", "--tests-dir", tests_dir]
        )

    assert (result.exit_code == 0) is accepted, result.output
    assert accepted or "its path inside the" in result.output


@pytest.mark.parametrize("tests_dir", ["suite", "./suite", "suite/"])
def test_the_cli_prints_the_impacted_test_files_of_a_real_repo(make_git_project, monkeypatch, tmp_path, tests_dir):
    """End to end, from elsewhere: ``--root-dir`` is honoured, and only test paths reach stdout."""
    project = make_git_project(
        {
            "app/__init__.py": "",
            "app/db.py": "X = 1\n",
            "suite/test_db.py": "from app.db import X\n\ndef test_db():\n    assert X\n",
            "suite/test_other.py": "def test_other():\n    assert True\n",
        },
        "[pytest]\n",
    )
    edit_file(project, "app/db.py")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        impacted_tests_cli, ["--root-dir", str(project.path), "--module", "app", "--tests-dir", tests_dir]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str((project.path / "suite/test_db.py").resolve())]


def test_the_cli_prints_the_tests_importing_anything_inside_an_edited_package(make_git_project, monkeypatch, tmp_path):
    project = make_git_project(
        {
            "src/app/__init__.py": "",
            "src/app/core/__init__.py": "READY = True\n",
            "src/app/core/db.py": "X = 1\n",
            "suite/test_db.py": "from app.core.db import X\n\ndef test_db():\n    assert X\n",
            "suite/test_other.py": "def test_other():\n    assert True\n",
        },
        "[pytest]\n",
    )
    edit_file(project, "src/app/core/__init__.py")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        impacted_tests_cli, ["--root-dir", str(project.path), "--module", "src/app", "--tests-dir", "suite"]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str((project.path / "suite/test_db.py").resolve())]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_a_directory_in_a_listable_but_unsearchable_one_is_a_bad_parameter(tmp_path):
    """``Path.is_dir()`` raises there on 3.11–3.13; the CLI must report the option, not crash."""
    locked = tmp_path / "pkg/data"
    locked.mkdir(parents=True)
    locked.chmod(0o644)
    try:
        result = CliRunner().invoke(impacted_tests_cli, ["--root-dir", str(tmp_path), "--module", "pkg/data/sub"])
    finally:
        locked.chmod(0o755)

    assert result.exit_code == 2, result.output
    assert "not found" in result.output and "--module" in result.output


@pytest.mark.parametrize("module", ["./pkg", "pkg/", "pkg"])
def test_the_cli_and_the_plugin_read_a_module_path_alike(tmp_path, module):
    """``./pkg`` passed the CLI and failed the plugin (read as ``//pkg``); one check now, and one spelling for the
    analysis (``api.get_impacted_tests`` normalizes it)."""
    from pytest_impacted.plugin import validate_module  # noqa: PLC0415 — the plugin's half of the comparison

    (tmp_path / "pkg").mkdir()
    validate_module(module, tmp_path)
    with patch("pytest_impacted.cli.get_impacted_tests", return_value=[]) as run:
        result = CliRunner().invoke(impacted_tests_cli, ["--root-dir", str(tmp_path), "--module", module])

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["ns_module"] == module  # normalized inside get_impacted_tests, for every caller


@pytest.mark.parametrize(
    ("args", "ini", "suggested"),
    [
        (["--module", "my-pkg"], "", "--module=my_pkg"),
        ([], "impacted_module = my-pkg\n", "impacted_module=my_pkg"),
    ],
    ids=["on the command line", "in the configuration"],
)
def test_a_suggested_fix_is_spelled_as_the_value_was_given(tmp_path, args, ini, suggested):
    (tmp_path / "my_pkg").mkdir()
    (tmp_path / "pytest.ini").write_text(f"[pytest]\n{ini}")

    result = CliRunner().invoke(impacted_tests_cli, ["--root-dir", str(tmp_path), *args])

    assert result.exit_code == 2, result.output
    assert f"Did you mean: {suggested}" in " ".join(result.output.split())


def test_a_map_without_a_path_is_a_usage_error(tmp_path):
    """Not a traceback: the option named, exit 2."""
    (tmp_path / "pkg").mkdir()

    result = CliRunner().invoke(
        impacted_tests_cli, ["--root-dir", str(tmp_path), "--module", "pkg", "--map", "nonsense"]
    )

    assert result.exit_code == 2, result.output
    assert "--map" in result.output
