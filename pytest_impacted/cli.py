"""CLI entrypoints for pytest-impacted."""

import logging

import click
from rich.console import Console
from rich.logging import RichHandler

from pytest_impacted.api import get_impacted_tests
from pytest_impacted.extensions import (
    build_strategy_with_extensions,
    discover_extension_metadata,
    get_ext_cli_flag,
    get_ext_ini_name,
)
from pytest_impacted.git import GitMode


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
@click.option("--git-mode", default=GitMode.UNSTAGED, help="Git mode.")
@click.option("--base-branch", default="main", help="Base branch.")
@click.option(
    "--root-dir",
    default=".",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Root directory for project repository.",
)
@click.option(
    "--module",
    required=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Namespace (top-level) module for package we are testing.",
)
@click.option(
    "--tests-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help=(
        "Directory containing the unit-test files. If not specified, "
        + "tests will only be found under namespace module directory."
    ),
)
@click.option("--verbose", is_flag=True, help="Verbose output.")
@click.option("--no-dep-files", is_flag=True, default=False, help="Disable dependency file change detection.")
@click.option("--disable-ext", multiple=True, default=(), help="Disable a strategy extension by name (repeatable).")
@click.pass_context
def impacted_tests_cli(
    ctx, git_mode, base_branch, root_dir, module, tests_dir, verbose, no_dep_files, disable_ext, **ext_kwargs
):
    """CLI entrypoint for impacted-tests console script."""
    click.echo("impacted-tests", err=True)
    click.secho("  base-branch: {}".format(base_branch), fg="blue", bold=True, err=True)
    click.secho("  git-mode: {}".format(git_mode), fg="blue", bold=True, err=True)
    click.secho("  module: {}".format(module), fg="blue", bold=True, err=True)
    click.secho("  root-dir: {}".format(root_dir), fg="blue", bold=True, err=True)
    click.secho("  tests-dir: {}".format(tests_dir), fg="blue", bold=True, err=True)
    click.secho("  no-dep-files: {}".format(no_dep_files), fg="blue", bold=True, err=True)
    if disable_ext:
        click.secho("  disable-ext: {}".format(", ".join(disable_ext)), fg="blue", bold=True, err=True)

    configure_logging(verbose=verbose)

    strategy = build_strategy_with_extensions(
        watch_dep_files=not no_dep_files,
        disabled=disable_ext,
        ext_config=ext_kwargs,
    )

    impacted_tests = get_impacted_tests(
        impacted_git_mode=git_mode,
        impacted_base_branch=base_branch,
        root_dir=root_dir,
        ns_module=module,
        tests_dir=tests_dir,
        strategy=strategy,
    )

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
