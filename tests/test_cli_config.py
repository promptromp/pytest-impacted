"""The ``impacted-tests`` CLI takes its defaults from the pytest configuration, as the plugin does.

A flag given on the command line wins; a setting the configuration lacks keeps the CLI's
own default. So a CI script runs ``impacted-tests`` with the settings ``pytest --impacted``
uses, without repeating them as flags.
"""

import os
import sys
from typing import ClassVar
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from pytest_impacted.cli import INI_NAMES, impacted_tests_cli
from pytest_impacted.extensions import ConfigOption, ExtensionMetadata
from pytest_impacted.plugin import pytest_addoption

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


def test_every_setting_is_read(calls, tmp_path):
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
        "file_map": [],
    }
    assert build.call_args.kwargs == {
        "watch_dep_files": False,
        "invalidate_all_patterns": ["*.json", "my dir/*.yaml"],
        "conftest_imports": True,
        "disabled": ["slow-ext"],
        "ext_config": {},
        "config_file": tmp_path / "pytest.ini",
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


def test_the_configuration_is_found_from_the_root_dir(calls, tmp_path):
    """As pytest finds it for a run there, not from the working directory."""
    run, _ = calls
    write_config("[pytest]\nimpacted_module = suite\n")
    project = tmp_path / "project"
    (project / "app").mkdir(parents=True)
    write_config("[pytest]\nimpacted_module = app\n", name=project / "pytest.ini")

    result = invoke("--root-dir", str(project))

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
    """An extension's setting is passed on raw, for the extension to convert, as in the plugin."""
    _, build = calls
    write_config("[pytest]\nimpacted_module = app\nimpacted_ext_demo_threshold = 7\n")
    option = click.Option(["--impacted-ext-demo-threshold"], type=int, default=None)
    option.name = "impacted_ext_demo_threshold"
    monkeypatch.setattr(impacted_tests_cli, "params", [*impacted_tests_cli.params, option])

    result = invoke()

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["ext_config"] == {"impacted_ext_demo_threshold": "7"}


def test_the_root_dir_is_not_moved_to_the_configuration_s_directory(calls, tmp_path, monkeypatch):
    """As with ``pytest --rootdir``: an explicit ``--module`` means what it meant before.

    A monorepo's top-level pyproject.toml, here with no pytest settings, is found from a
    service directory, as pytest finds it; its directory is not the root for that.
    """
    run, _ = calls
    write_config("[tool.ruff]\nline-length = 100\n", name="pyproject.toml")
    (tmp_path / "svc/pkg").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "svc")

    result = invoke("--module", "pkg")

    assert result.exit_code == 0, result.output
    assert (run.call_args.kwargs["root_dir"], run.call_args.kwargs["ns_module"]) == (".", "pkg")


def test_settings_paths_are_relative_to_the_root_dir(calls, tmp_path):
    """``-c`` names the file; as with ``pytest -c ... --rootdir=.``, its paths are the root's."""
    run, _ = calls
    (tmp_path / "ci").mkdir()
    write_config("[pytest]\nimpacted_module = app\n", name="ci/impacted.ini")

    result = invoke("-c", "ci/impacted.ini")

    assert result.exit_code == 0, result.output
    assert (run.call_args.kwargs["root_dir"], run.call_args.kwargs["ns_module"]) == (".", "app")


def test_every_plugin_setting_the_cli_has_an_option_for_is_read(_mock_extensions):
    """The CLI's names for the plugin's settings must not drift from what the plugin declares."""
    parser = MagicMock()
    pytest_addoption(parser)
    declared = {call.args[0] for call in parser.addini.call_args_list}

    plugin_only = {"impacted", "impacted_unimpacted", "impacted_early_filter"}  # the CLI collects nothing
    assert set(INI_NAMES.values()) == declared - plugin_only


@pytest.fixture
def _mock_extensions():
    with patch("pytest_impacted.plugin.discover_extension_metadata", return_value=()):
        yield


def test_an_error_names_the_setting_once(calls):
    write_config("[pytest]\nimpacted_module = app\nimpacted_conftest_imports = maybe\n")

    result = invoke()

    assert result.exit_code == 2
    assert result.output.count("impacted_conftest_imports in") == 1


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_an_unreadable_configuration_is_a_usage_error(calls, tmp_path):
    write_config("[pytest]\nimpacted_module = app\n")
    (tmp_path / "pytest.ini").chmod(0)

    result = invoke()

    assert result.exit_code == 2, result.output
    assert "pytest.ini" in result.output


def test_an_empty_setting_is_unset(calls):
    """As in the plugin, where ``getoption(name) or getini(name)`` passes over an empty value."""
    run, _ = calls
    write_config("[pytest]\nimpacted_module = app\nimpacted_base_branch =\n")

    result = invoke()

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["impacted_base_branch"] == "main"


def test_an_edit_to_the_configuration_read_selects_every_test(make_git_project):
    """Even under a name no dependency-file pattern knows, as with ``pytest -c``."""
    project = make_git_project({**PROJECT, "ci/impacted.ini": "[pytest]\nimpacted_module = app\n"}, "[pytest]\n")
    edit_file(project, "ci/impacted.ini")

    result = invoke(
        "-c", str(project.path / "ci/impacted.ini"), "--root-dir", str(project.path), "--tests-dir", "suite"
    )

    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.splitlines()) == [
        str((project.path / f"suite/test_{name}.py").resolve()) for name in ("a", "b")
    ]


def test_list_items_that_are_not_strings_are_read_as_strings(calls):
    """``[tool.pytest.ini_options]`` keeps a TOML list as it is; the CLI joins its items to print them."""
    _, build = calls
    write_config(
        '[tool.pytest.ini_options]\nimpacted_module = "app"\nimpacted_disable_ext = [1, "x"]\n', "pyproject.toml"
    )

    result = invoke()

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["disabled"] == ["1", "x"]


def test_an_extension_setting_is_passed_on_as_the_plugin_reads_it(calls, monkeypatch):
    """Raw, for the extension's own conversion: ``on`` is no truth value to it, as in the plugin."""
    _, build = calls
    write_config("[pytest]\nimpacted_module = app\nimpacted_ext_demo_enabled = on\n")
    option = click.Option(["--impacted-ext-demo-enabled"], type=click.BOOL, default=None)
    option.name = "impacted_ext_demo_enabled"
    monkeypatch.setattr(impacted_tests_cli, "params", [*impacted_tests_cli.params, option])

    result = invoke()

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["ext_config"] == {"impacted_ext_demo_enabled": "on"}


@pytest.mark.parametrize(
    "text",
    [
        pytest.param('[tool]\npytest = "x"\n', id="not_a_table"),
        pytest.param(
            "[tool.pytest]\nminversion = '9'\n[tool.pytest.ini_options]\nimpacted_module = 'x'\n",
            id="both_tables",
            marks=pytest.mark.skipif(pytest.version_tuple < (9,), reason="pytest 8 reads no [tool.pytest]"),
        ),
        pytest.param("this is = not [toml", id="unparsable"),
    ],
)
def test_a_configuration_it_cannot_read_is_a_usage_error(calls, text):
    """Loud, as pytest is on the same file: passed over, its settings (a tests dir, say) would be lost."""
    write_config(text, name="pyproject.toml")

    result = invoke("--module", "app")

    assert result.exit_code == 2, result.output
    assert "pyproject.toml" in result.output


def test_a_named_configuration_it_cannot_read_is_a_usage_error(calls):
    write_config("this is = not [toml", name="ci.toml")

    result = invoke("-c", "ci.toml", "--module", "app")

    assert result.exit_code == 2, result.output
    assert "ci.toml" in result.output


class _Demo:
    config_options: ClassVar[list[ConfigOption]] = [
        ConfigOption(name="threshold", help="Minimum", type=int, default=80)
    ]

    def __init__(self, threshold=80):
        self.threshold = threshold

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return []


@pytest.fixture
def demo_extension(tmp_path, monkeypatch):
    """A real extension ``demo``, with an int option, built as the plugin builds it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    metadata = ExtensionMetadata(name="demo", strategy_class=_Demo, config_options=_Demo.config_options)
    monkeypatch.setattr("pytest_impacted.extensions.discover_extension_metadata", lambda: [metadata])
    option = click.Option(["--impacted-ext-demo-threshold"], type=int, default=None)
    option.name = "impacted_ext_demo_threshold"
    monkeypatch.setattr(impacted_tests_cli, "params", [*impacted_tests_cli.params, option])
    with patch("pytest_impacted.cli.get_impacted_tests", return_value=[]) as run:
        yield run


def test_a_malformed_extension_setting_is_a_usage_error(demo_extension):
    write_config("[pytest]\nimpacted_module = app\nimpacted_ext_demo_threshold = abc\n")

    result = invoke()

    assert result.exit_code == 2, result.output
    assert "demo" in result.output and "threshold" in result.output and "'abc'" in result.output


def test_an_empty_extension_setting_takes_the_option_s_default(demo_extension):
    write_config("[pytest]\nimpacted_module = app\nimpacted_ext_demo_threshold =\n")

    result = invoke()

    assert result.exit_code == 0, result.output
    assert demo_extension.call_args.kwargs["strategy"].strategies[-1].threshold == 80


def test_an_empty_extension_setting_is_passed_on_as_the_plugin_passes_it(calls, monkeypatch):
    _, build = calls
    write_config("[pytest]\nimpacted_module = app\nimpacted_ext_demo_enabled =\n")
    option = click.Option(["--impacted-ext-demo-enabled"], type=click.BOOL, default=None)
    option.name = "impacted_ext_demo_enabled"
    monkeypatch.setattr(impacted_tests_cli, "params", [*impacted_tests_cli.params, option])

    result = invoke()

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["ext_config"] == {"impacted_ext_demo_enabled": ""}


def test_a_bad_directory_from_the_configuration_names_the_setting(calls):
    write_config("[pytest]\nimpacted_module = app\nimpacted_tests_dir = nope\n")

    result = invoke()

    assert result.exit_code == 2
    assert "impacted_tests_dir" in result.output and "pytest.ini" in result.output


def test_a_list_for_a_string_setting_is_a_usage_error(calls):
    write_config('[tool.pytest.ini_options]\nimpacted_module = ["app"]\n', name="pyproject.toml")

    result = invoke()

    assert result.exit_code == 2
    assert "impacted_module" in result.output and "expected a string" in result.output


def test_an_option_with_no_setting_reads_none(calls, monkeypatch):
    """Only an extension's option is named as its setting; another would read a stray key."""
    write_config("[pytest]\nimpacted_module = app\noutput = surprise\n")
    option = click.Option(["--output"], default="out")
    option.name = "output"
    monkeypatch.setattr(impacted_tests_cli, "params", [*impacted_tests_cli.params, option])
    seen = {}
    monkeypatch.setattr("pytest_impacted.cli._run", lambda config, sources, **options: seen.update(options))

    invoke()

    assert seen["output"] == "out"
