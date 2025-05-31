"""CLI entrypoints for pytest-impacted."""

import logging

import click
from rich.console import Console
from rich.logging import RichHandler

from pytest_impacted.api import get_impacted_tests
from pytest_impacted.git import GitMode


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
def impacted_tests_cli(git_mode, base_branch, root_dir, module, tests_dir, verbose):
    """CLI entrypoint for impacted-tests console script."""
    click.echo("impacted-tests", err=True)
    click.secho("  base-branch: {}".format(base_branch), fg="blue", bold=True, err=True)
    click.secho("  git-mode: {}".format(git_mode), fg="blue", bold=True, err=True)
    click.secho("  module: {}".format(module), fg="blue", bold=True, err=True)
    click.secho("  root-dir: {}".format(root_dir), fg="blue", bold=True, err=True)
    click.secho("  tests-dir: {}".format(tests_dir), fg="blue", bold=True, err=True)

    configure_logging(verbose=verbose)

    impacted_tests = get_impacted_tests(
        impacted_git_mode=git_mode,
        impacted_base_branch=base_branch,
        root_dir=root_dir,
        ns_module=module,
        tests_dir=tests_dir,
    )

    if impacted_tests:
        for impacted_test in impacted_tests:
            print(impacted_test)
    else:
        click.secho("No impacted tests found.", fg="red", bold=True, err=True)
