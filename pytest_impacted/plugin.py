import os
from functools import partial
from typing import Any

import pytest
from pytest import Config, Parser, UsageError

from pytest_impacted.api import get_impacted_tests, matches_impacted_tests
from pytest_impacted.extensions import (
    build_strategy_with_extensions,
    discover_extension_metadata,
    get_ext_cli_flag,
    get_ext_ini_name,
)
from pytest_impacted.git import GIT_AVAILABLE, GitMode, find_repo


def pytest_addoption(parser: Parser):
    """pytest hook to add command line options.

    This is called before any tests are collected.

    """
    group = parser.getgroup("impacted")
    group.addoption(
        "--impacted",
        action="store_true",
        default=None,
        dest="impacted",
        help="Run only tests impacted by the chosen git state.",
    )
    parser.addini(
        "impacted",
        help="default value for --impacted",
        default=False,
    )

    group.addoption(
        "--impacted-module",
        default=None,
        dest="impacted_module",
        metavar="MODULE",
        help="Module name to check for impacted tests.",
    )
    parser.addini(
        "impacted_module",
        help="default value for --impacted-module",
        default=None,
    )

    group.addoption(
        "--impacted-git-mode",
        action="store",
        dest="impacted_git_mode",
        choices=GitMode.__members__.values(),
        default=None,
        nargs="?",
        help="Git reference for computing impacted files.",
    )
    parser.addini(
        "impacted_git_mode",
        help="default value for --impacted-git-mode",
        default=GitMode.UNSTAGED,
    )

    group.addoption(
        "--impacted-base-branch",
        action="store",
        default=None,
        dest="impacted_base_branch",
        help="Git reference for computing impacted files when running in 'branch' git mode.",
    )
    parser.addini(
        "impacted_base_branch",
        help="default value for --impacted-base-branch",
        default=None,
    )

    group.addoption(
        "--impacted-tests-dir",
        action="store",
        default=None,
        dest="impacted_tests_dir",
        help=(
            "Directory containing the unit-test files. If not specified, "
            + "tests will only be found under namespace module directory."
        ),
    )
    parser.addini(
        "impacted_tests_dir",
        help="default value for --impacted-tests-dir",
        default=None,
    )

    group.addoption(
        "--no-impacted-dep-files",
        action="store_true",
        default=None,
        dest="no_impacted_dep_files",
        help="Disable dependency file change detection (uv.lock, requirements.txt, etc.).",
    )
    parser.addini(
        "no_impacted_dep_files",
        help="default value for --no-impacted-dep-files",
        default=False,
    )

    # Extension management
    group.addoption(
        "--impacted-disable-ext",
        action="append",
        default=[],
        dest="impacted_disable_ext",
        help="Disable a strategy extension by name (repeatable).",
    )
    parser.addini(
        "impacted_disable_ext",
        help="Strategy extensions to disable",
        type="args",
        default=[],
    )

    # Register config options from discovered extensions
    for ext in discover_extension_metadata():
        for opt in ext.config_options:
            flag = get_ext_cli_flag(ext.name, opt.name)
            ini_name = get_ext_ini_name(ext.name, opt.name)
            add_kwargs: dict[str, Any] = {
                "default": None,
                "dest": ini_name,
                "help": f"[ext:{ext.name}] {opt.help}",
            }
            if opt.type is bool:
                add_kwargs["action"] = "store_true"
            group.addoption(flag, **add_kwargs)
            ini_default = str(opt.default) if opt.default is not None else None
            parser.addini(ini_name, help=opt.help, default=ini_default)


def pytest_configure(config: Config):
    """pytest hook to configure the plugin.

    This is called after the command line options have been parsed.

    """
    validate_config(config)

    config.addinivalue_line(
        "markers",
        "impacted(state): mark test as impacted by the state of the git repository",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_report_header(config: Config) -> list[str]:
    """Add pytest-impacted config to pytest header."""
    from pytest_impacted._rust import RUST_AVAILABLE  # noqa: PLC0415

    get_option = partial(get_option_from_config, config)
    backend = "rust (ruff parser + rayon)" if RUST_AVAILABLE else "python (astroid)"
    ext_names = [e.name for e in discover_extension_metadata()]
    header = [
        f"impacted_module={get_option('impacted_module')}",
        f"impacted_git_mode={get_option('impacted_git_mode')}",
        f"impacted_base_branch={get_option('impacted_base_branch')}",
        f"impacted_tests_dir={get_option('impacted_tests_dir')}",
        f"no_impacted_dep_files={get_option('no_impacted_dep_files')}",
        f"backend={backend}",
    ]
    if ext_names:
        header.append(f"extensions={','.join(ext_names)}")
    return [
        "pytest-impacted: " + ", ".join(header),
    ]


def pytest_collection_modifyitems(session, config, items):
    """pytest hook to modify the collected test items.

    This is called after the tests have been collected and before
    they are run.

    """
    get_option = partial(get_option_from_config, config)
    impacted = get_option("impacted")
    if not impacted:
        return

    ns_module = get_option("impacted_module")
    impacted_git_mode = get_option("impacted_git_mode")
    impacted_base_branch = get_option("impacted_base_branch")
    impacted_tests_dir = get_option("impacted_tests_dir")
    no_dep_files = get_option("no_impacted_dep_files")
    root_dir = config.rootdir

    disabled_ext = get_option("impacted_disable_ext") or []
    ext_config = _collect_ext_config(config)
    strategy = build_strategy_with_extensions(
        watch_dep_files=not no_dep_files,
        disabled=disabled_ext,
        ext_config=ext_config,
    )

    impacted_tests = get_impacted_tests(
        impacted_git_mode=impacted_git_mode,
        impacted_base_branch=impacted_base_branch,
        root_dir=root_dir,
        ns_module=ns_module,
        tests_dir=impacted_tests_dir,
        session=session,
        strategy=strategy,
    )
    if not impacted_tests:
        # skip all tests
        for item in items:
            item.add_marker(pytest.mark.skip)
        return

    for item in items:
        item_path = item.location[0]
        if matches_impacted_tests(item_path, impacted_tests=impacted_tests):
            item.add_marker(pytest.mark.impacted)
        else:
            item.add_marker(pytest.mark.skip)


def get_option_from_config(config: Config, name: str) -> str | None:
    """Get an option from the config.

    If the option is not set via command line, return the default value
    from the ini configuration file (e.g. pytest.ini, pyproject.toml) if present.

    """
    return config.getoption(name) or config.getini(name)


def validate_config(config: Config):
    """Validate the configuration options."""
    get_option = partial(get_option_from_config, config)
    if not get_option("impacted"):
        return

    if not get_option("impacted_module"):
        raise UsageError("No module specified. Please specify a module using --impacted-module.")
    if not get_option("impacted_git_mode"):
        raise UsageError("No git mode specified. Please specify a git mode using --impacted-git-mode.")

    if get_option("impacted_git_mode") == GitMode.BRANCH and not get_option("impacted_base_branch"):
        raise UsageError("No base branch specified. Please specify a base branch using --impacted-base-branch.")

    module_name = get_option("impacted_module")
    assert module_name is not None  # guarded by the check above
    validate_module(module_name)

    tests_dir = get_option("impacted_tests_dir")
    if tests_dir:
        validate_tests_dir(tests_dir)

    base_branch = get_option("impacted_base_branch")
    if get_option("impacted_git_mode") == GitMode.BRANCH and base_branch:
        validate_base_branch(base_branch, str(config.rootdir))  # type: ignore[attr-defined]


def validate_module(module_name: str) -> None:
    """Validate that --impacted-module refers to a discoverable Python package."""
    module_dir = module_name.replace(".", os.sep)
    if os.path.isdir(module_dir):
        return

    # The directory doesn't exist — try to give a helpful suggestion
    if "-" in module_name:
        suggestion = module_name.replace("-", "_")
        suggestion_dir = suggestion.replace(".", os.sep)
        if os.path.isdir(suggestion_dir):
            raise UsageError(
                f"Module '{module_name}' not found. Python module names use underscores, not hyphens. "
                f"Did you mean: --impacted-module={suggestion}"
            )

    # Check for src-layout: module might be under src/
    src_dir = os.path.join("src", module_dir)
    if os.path.isdir(src_dir):
        raise UsageError(
            f"Module '{module_name}' not found in the current directory, but found at '{src_dir}'. "
            f"For src-layout projects, use: --impacted-module=src/{module_dir}"
        )

    raise UsageError(
        f"Module '{module_name}' not found (no '{module_dir}/' directory in the current working directory). "
        f"Make sure --impacted-module is a valid Python package name and you are running from the project root."
    )


def _collect_ext_config(config: Config) -> dict[str, Any]:
    """Collect all extension config values from pytest config."""
    ext_config: dict[str, Any] = {}
    for ext in discover_extension_metadata():
        for opt in ext.config_options:
            ini_name = get_ext_ini_name(ext.name, opt.name)
            value = get_option_from_config(config, ini_name)
            if value is not None:
                ext_config[ini_name] = value
    return ext_config


def validate_tests_dir(tests_dir: str) -> None:
    """Validate that --impacted-tests-dir refers to an existing directory."""
    if not os.path.isdir(tests_dir):
        raise UsageError(
            f"Tests directory '{tests_dir}' does not exist. Please check the path passed to --impacted-tests-dir."
        )


def validate_base_branch(base_branch: str, root_dir: str) -> None:
    """Validate that --impacted-base-branch refers to a valid git ref."""
    if not GIT_AVAILABLE:
        return

    from git import GitCommandError, InvalidGitRepositoryError  # noqa: PLC0415

    try:
        repo = find_repo(root_dir)
        repo.git.rev_parse("--verify", base_branch)
    except InvalidGitRepositoryError as err:
        raise UsageError(
            f"No git repository found at or above '{root_dir}'. Make sure you are running from within a git repository."
        ) from err
    except GitCommandError as err:
        # List available local branches for the suggestion
        try:
            branches = [ref.name for ref in repo.references]
            branch_list = ", ".join(sorted(branches)[:10])
            suffix = f" Available refs: {branch_list}"
            if len(repo.references) > 10:
                suffix += ", ..."
        except (AttributeError, TypeError):
            suffix = ""

        raise UsageError(
            f"Base branch '{base_branch}' does not exist in the git repository. "
            f"Please check the value passed to --impacted-base-branch.{suffix}"
        ) from err
