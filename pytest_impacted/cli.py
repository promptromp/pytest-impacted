"""CLI entrypoints for pytest-impacted."""

import logging
import os
import tomllib
from pathlib import Path
from typing import Any

import click
import iniconfig
from click.core import ParameterSource
from rich.console import Console
from rich.logging import RichHandler

from pytest_impacted.api import build_strategy_with_extensions, get_impacted_tests
from pytest_impacted.extensions import (
    discover_extension_metadata,
    get_ext_cli_flag,
    get_ext_ini_name,
)
from pytest_impacted.git import GitMode, GitUnavailableError
from pytest_impacted.ini import PytestConfig, find_pytest_config, load_pytest_config


# The pytest setting each option defaults to; an extension's option is named as its setting.
INI_NAMES = {
    "git_mode": "impacted_git_mode",
    "base_branch": "impacted_base_branch",
    "module": "impacted_module",
    "tests_dir": "impacted_tests_dir",
    "no_merge_base": "impacted_no_merge_base",
    "no_dep_files": "no_impacted_dep_files",
    "invalidate_all": "impacted_invalidate_all",
    "conftest_imports": "impacted_conftest_imports",
    "disable_ext": "impacted_disable_ext",
}
# Options with no pytest setting.
_CLI_ONLY = {"root_dir", "config_file", "verbose"}

_CLICK_TYPE_MAP: dict[type, click.ParamType] = {
    str: click.STRING,
    int: click.INT,
    float: click.FLOAT,
    bool: click.BOOL,
}


def configure_logging(verbose: bool) -> None:
    """Configure logging for the CLIs."""
    # Default to using stderr for logs as we want stdout for pipe-able output.
    console = Console(stderr=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(funcName)-20s | %(message)s",
        datefmt="[%x]",
        handlers=[RichHandler(console=console, markup=True, rich_tracebacks=True)],
    )


@click.command(context_settings={"show_default": True})
@click.option(
    "--git-mode",
    type=click.Choice([mode.value for mode in GitMode]),
    default=GitMode.UNSTAGED.value,
    help="Which changes count.",
)
@click.option("--base-branch", default="main", help="Base branch.")
@click.option(
    "--root-dir",
    default=".",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help=(
        "Root directory for project repository, as pytest's --rootdir: the pytest configuration is "
        + "found from here, and the paths it and the options give are relative to it."
    ),
)
@click.option(
    "-c",
    "--config-file",
    type=click.Path(exists=True, dir_okay=False),
    help=(
        "The pytest configuration file to take defaults from. By default, the one pytest finds "
        + "for a run from --root-dir (pytest.ini, pyproject.toml, tox.ini, ...)."
    ),
)
@click.option(
    "--module",
    help=(
        "Namespace (top-level) module for package we are testing, relative to --root-dir. "
        + "Required, here or as impacted_module in the pytest configuration."
    ),
)
@click.option(
    "--tests-dir",
    help=(
        "Directory containing the unit-test files, relative to --root-dir. If not specified, "
        + "tests will only be found under namespace module directory."
    ),
)
@click.option(
    "--no-merge-base/--merge-base",
    default=False,
    help="In branch mode, diff against the base branch's tip instead of the fork point.",
)
@click.option("--verbose", is_flag=True, help="Verbose output.")
@click.option(
    "--no-dep-files/--dep-files", default=False, help="Disable dependency and test-config file change detection."
)
@click.option(
    "--invalidate-all",
    multiple=True,
    default=(),
    metavar="PATTERN",
    help="Glob for files that, when changed, mark ALL tests as impacted (repeatable).",
)
@click.option(
    "--conftest-imports/--no-conftest-imports",
    default=False,
    help="Also treat a conftest.py that imports changed application code as impacting every test beneath it.",
)
@click.option("--disable-ext", multiple=True, default=(), help="Disable a strategy extension by name (repeatable).")
@click.pass_context
def impacted_tests_cli(ctx, **options):
    """CLI entrypoint for impacted-tests console script.

    An option the command line leaves out takes its pytest setting (``impacted_module``
    for ``--module``, and so on), and only then its own default: the settings
    ``pytest --rootdir=<root-dir>`` run from ``--root-dir`` would read, with paths
    relative to ``--root-dir``, as there.
    """
    config = _pytest_config(options["root_dir"], options.pop("config_file"))
    options |= _settings(ctx, config)
    if not options["module"]:
        raise click.UsageError("Missing option '--module' (or impacted_module in the pytest configuration).")
    _run(config, **options)


def _pytest_config(root_dir: str, config_file: str | None) -> PytestConfig:
    """The configuration named by ``--config-file``, or the one pytest finds from *root_dir*."""
    try:
        return load_pytest_config(Path(config_file)) if config_file else find_pytest_config(Path(root_dir))
    except (OSError, ValueError, iniconfig.ParseError, tomllib.TOMLDecodeError) as err:
        raise click.UsageError(str(err)) from err


def _settings(ctx: click.Context, config: PytestConfig) -> dict[str, Any]:
    """The pytest settings for the options the command line left at their defaults.

    Each is read as the plugin declares it (a flag, a list, or a string), and a string
    is checked as the option's own value would be (``--git-mode`` is a choice).
    """
    settings: dict[str, Any] = {}
    for param in ctx.command.params:
        name = param.name
        if name is None or name in _CLI_ONLY or ctx.get_parameter_source(name) is not ParameterSource.DEFAULT:
            continue
        setting = INI_NAMES.get(name, name)
        try:
            value = _read(config, setting, param)
        except ValueError as err:  # it names the setting and the file
            raise click.UsageError(str(err)) from err
        # Empty is unset, as in the plugin, which reads ``getoption(name) or getini(name)``.
        if value is None or value in ("", []):
            continue
        try:
            settings[name] = value if param.multiple or _is_flag(param) else param.type.convert(value, param, ctx)
        except click.BadParameter as err:
            raise click.UsageError(f"{setting} in {config.path}: {err.format_message()}") from err
    return settings


def _read(config: PytestConfig, setting: str, param: click.Parameter) -> Any:
    if _is_flag(param):
        return config.flag(setting)
    return config.args(setting) if param.multiple else config.string(setting)


def _is_flag(param: click.Parameter) -> bool:
    return isinstance(param, click.Option) and param.is_flag


def _run(
    config: PytestConfig,
    *,
    git_mode,
    base_branch,
    root_dir,
    module,
    tests_dir,
    no_merge_base,
    verbose,
    no_dep_files,
    invalidate_all,
    conftest_imports,
    disable_ext,
    **ext_kwargs,
) -> None:
    click.echo("impacted-tests", err=True)
    click.secho(f"  config-file: {config.path}", fg="blue", bold=True, err=True)
    click.secho(f"  base-branch: {base_branch}", fg="blue", bold=True, err=True)
    click.secho(f"  git-mode: {git_mode}", fg="blue", bold=True, err=True)
    click.secho(f"  module: {module}", fg="blue", bold=True, err=True)
    click.secho(f"  root-dir: {root_dir}", fg="blue", bold=True, err=True)
    click.secho(f"  tests-dir: {tests_dir}", fg="blue", bold=True, err=True)
    click.secho(f"  no-dep-files: {no_dep_files}", fg="blue", bold=True, err=True)
    if no_merge_base:
        click.secho("  no-merge-base: True", fg="blue", bold=True, err=True)
    if invalidate_all:
        click.secho("  invalidate-all: {}".format(", ".join(invalidate_all)), fg="blue", bold=True, err=True)
    if conftest_imports:
        click.secho("  conftest-imports: True", fg="blue", bold=True, err=True)
    if disable_ext:
        click.secho("  disable-ext: {}".format(", ".join(disable_ext)), fg="blue", bold=True, err=True)

    configure_logging(verbose=verbose)

    # Paths are relative to --root-dir, which need not be the working directory. Dotted
    # names are accepted here as they are by discover_submodules, but ``./suite`` is a path.
    for option, value in (("--module", module), ("--tests-dir", tests_dir)):
        candidates = (Path(root_dir) / value, Path(root_dir) / value.replace(".", os.sep)) if value else ()
        if value and not any(os.path.isdir(candidate) for candidate in candidates):
            raise click.BadParameter(
                f"Directory '{value}' does not exist under root-dir '{root_dir}'.", param_hint=option
            )

    strategy = build_strategy_with_extensions(
        watch_dep_files=not no_dep_files,
        invalidate_all_patterns=invalidate_all,
        conftest_imports=conftest_imports,
        disabled=disable_ext,
        ext_config=ext_kwargs,
        config_file=config.path,
    )

    try:
        impacted_tests = get_impacted_tests(
            impacted_git_mode=git_mode,
            impacted_base_branch=base_branch,
            root_dir=root_dir,
            ns_module=module,
            tests_dir=tests_dir,
            strategy=strategy,
            use_merge_base=not no_merge_base,
        )
    except GitUnavailableError as err:
        # Printing nothing would read as "no tests impacted" to a script piping
        # this output into pytest, so fail instead.
        raise click.ClickException(str(err)) from err

    if impacted_tests:
        for impacted_test in impacted_tests:
            print(impacted_test)
    else:
        click.secho("No impacted tests found.", fg="red", bold=True, err=True)


def _register_extension_options(cmd: click.Command) -> None:
    """Dynamically add extension config options to the Click command."""
    for ext in discover_extension_metadata():
        for opt in ext.config_options:
            flag = get_ext_cli_flag(ext.name, opt.name)
            param_name = get_ext_ini_name(ext.name, opt.name)
            click_opt = click.Option(
                [flag],
                default=opt.default,
                help=f"[ext:{ext.name}] {opt.help}",
                type=_CLICK_TYPE_MAP.get(opt.type, click.STRING),
            )
            click_opt.name = param_name
            cmd.params.append(click_opt)


_register_extension_options(impacted_tests_cli)
