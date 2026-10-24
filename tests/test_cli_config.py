"""The ``impacted-tests`` CLI takes its defaults from the pytest configuration, as the plugin does.

A flag given on the command line wins; a setting the configuration lacks keeps the CLI's
own default. So a CI script runs ``impacted-tests`` with the settings ``pytest --impacted``
uses, without repeating them as flags.
"""

from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from pytest_impacted.cli import impacted_tests_cli

from .git_helpers import edit_file


PROJECT = {
    "app/__init__.py": "",
    "app/a.py": "def a():\n    return 1\n",
    "suite/test_a.py": "from app.a import a\n\ndef test_a():\n    assert a() == 1\n",
    "suite/test_b.py": "def test_b():\n    assert True\n",
}


def invoke(*args):
    return CliRunner().invoke(impacted_tests_cli, list(args))


def test_the_cli_runs_on_the_settings_pytest_reads(make_git_project):
    project = make_git_project(PROJECT, "[pytest]\nimpacted_module = app\nimpacted_tests_dir = suite\n")
    edit_file(project, "app/a.py")

    result = invoke("--root-dir", str(project.path))

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str((project.path / "suite/test_a.py").resolve())]
    assert "config-file: " + str(project.path / "tox.ini") in result.output  # pytester.makeini writes tox.ini


@pytest.fixture
def calls(tmp_path, monkeypatch):
    """Run from *tmp_path* with an ``app`` package; record what the CLI passes on."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "suite").mkdir()
    with (
        patch("pytest_impacted.cli.get_impacted_tests", return_value=[]) as run,
        patch("pytest_impacted.cli.build_strategy_with_extensions") as build,
    ):
        yield run, build


def write_config(text, name="pytest.ini"):
    with open(name, "w") as config:
        config.write(text)


FULL = """[pytest]
impacted_module = app
impacted_tests_dir = suite
impacted_git_mode = branch
impacted_base_branch = develop
impacted_no_merge_base = true
no_impacted_dep_files = yes
impacted_invalidate_all = *.json 'my dir/*.yaml'
impacted_conftest_imports = 1
impacted_disable_ext = slow-ext
"""


def test_every_setting_is_read(calls):
    run, build = calls
    write_config(FULL)

    result = invoke()

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs | {"strategy": None} == {
        "impacted_git_mode": "branch",
        "impacted_base_branch": "develop",
        "root_dir": ".",
        "ns_module": "app",
        "tests_dir": "suite",
        "strategy": None,
        "use_merge_base": False,
    }
    assert build.call_args.kwargs == {
        "watch_dep_files": False,
        "invalidate_all_patterns": ["*.json", "my dir/*.yaml"],
        "conftest_imports": True,
        "disabled": ["slow-ext"],
        "ext_config": {},
    }


def test_flags_win_over_the_configuration(calls, tmp_path):
    run, build = calls
    (tmp_path / "other").mkdir()
    write_config(FULL)

    result = invoke(
        "--module", "other", "--git-mode", "unstaged", "--merge-base", "--dep-files", "--no-conftest-imports",
        "--invalidate-all", "*.sql", "--disable-ext", "x",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["ns_module"] == "other"
    assert run.call_args.kwargs["impacted_git_mode"] == "unstaged"
    assert run.call_args.kwargs["use_merge_base"] is True
    assert build.call_args.kwargs["watch_dep_files"] is True
    assert build.call_args.kwargs["conftest_imports"] is False
    assert build.call_args.kwargs["invalidate_all_patterns"] == ("*.sql",)
    assert build.call_args.kwargs["disabled"] == ("x",)


def test_settings_the_configuration_lacks_keep_the_cli_defaults(calls):
    run, build = calls
    write_config("[pytest]\nimpacted_module = app\n")

    result = invoke()

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["impacted_git_mode"] == "unstaged"
    assert run.call_args.kwargs["impacted_base_branch"] == "main"
    assert run.call_args.kwargs["use_merge_base"] is True
    assert build.call_args.kwargs["conftest_imports"] is False


def test_pyproject_ini_options_are_read(calls):
    run, build = calls
    write_config(
        '[tool.pytest.ini_options]\nimpacted_module = "app"\nimpacted_invalidate_all = ["*.json"]\n',
        name="pyproject.toml",
    )

    result = invoke()

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["ns_module"] == "app"
    assert build.call_args.kwargs["invalidate_all_patterns"] == ["*.json"]


def test_the_configuration_is_found_from_the_root_dir(calls, tmp_path, monkeypatch):
    """As pytest finds it for a run there, not from the working directory."""
    run, _ = calls
    write_config("[pytest]\nimpacted_module = app\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = invoke("--root-dir", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["ns_module"] == "app"


def test_config_file_names_the_file(calls, tmp_path):
    run, _ = calls
    write_config("[pytest]\nimpacted_module = suite\n")
    write_config("[pytest]\nimpacted_module = app\n", name="ci.ini")

    result = invoke("-c", "ci.ini")

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["ns_module"] == "app"
    assert f"config-file: {tmp_path / 'ci.ini'}" in result.output


def test_a_module_is_required_from_somewhere(calls):
    result = invoke()

    assert result.exit_code == 2
    assert "--module" in result.output
    assert "impacted_module" in result.output


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ("[pytest]\nimpacted_module = app\nimpacted_git_mode = all\n", "impacted_git_mode"),
        ("[pytest]\nimpacted_module = app\nimpacted_conftest_imports = maybe\n", "invalid truth value"),
    ],
)
def test_an_invalid_setting_is_a_usage_error(calls, config, message):
    write_config(config)

    result = invoke()

    assert result.exit_code == 2, result.output
    assert message in result.output


def test_extension_options_are_read(calls, monkeypatch):
    """An extension's setting is read as its option's value would be."""
    _, build = calls
    write_config("[pytest]\nimpacted_module = app\nimpacted_ext_demo_threshold = 7\n")
    option = click.Option(["--impacted-ext-demo-threshold"], type=int, default=None)
    option.name = "impacted_ext_demo_threshold"
    monkeypatch.setattr(impacted_tests_cli, "params", [*impacted_tests_cli.params, option])

    result = invoke()

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["ext_config"] == {"impacted_ext_demo_threshold": 7}
