from functools import partial

import pytest
from pytest import Config, Parser, UsageError

from pytest_impacted.api import get_impacted_tests, matches_impacted_tests
from pytest_impacted.git import GitMode


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
    get_option = partial(get_option_from_config, config)
    header = [
        f"impacted_module={get_option('impacted_module')}",
        f"impacted_git_mode={get_option('impacted_git_mode')}",
        f"impacted_base_branch={get_option('impacted_base_branch')}",
        f"impacted_tests_dir={get_option('impacted_tests_dir')}",
    ]
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
    root_dir = config.rootdir

    impacted_tests = get_impacted_tests(
        impacted_git_mode=impacted_git_mode,
        impacted_base_branch=impacted_base_branch,
        root_dir=root_dir,
        ns_module=ns_module,
        tests_dir=impacted_tests_dir,
        session=session,
    )
    if not impacted_tests:
        # skip all tests
        for item in items:
            item.add_marker(pytest.mark.skip)
        return

    impacted_items = []
    for item in items:
        item_path = item.location[0]
        if matches_impacted_tests(item_path, impacted_tests=impacted_tests):
            # notify(f"matched impacted item_path:  {item.location}", session)
            item.add_marker(pytest.mark.impacted)
            impacted_items.append(item)
        else:
            # Mark the item as skipped if it is not impacted. This will be used to
            # let pytest know to skip the test.
            item.add_marker(pytest.mark.skip)


def get_option_from_config(config: Config, name: str) -> str | None:
    """Get an option from the config.

    If the option is not set via command line, return the default value
    from the ini configuration file (e.g. pytest.ini, pyproject.toml) if present.

    """
    return config.getoption(name) or config.getini(name)


def validate_config(config: Config):
    """Validate the configuration options"""
    get_option = partial(get_option_from_config, config)
    if get_option("impacted"):
        if not get_option("impacted_module"):
            # If the impacted option is set, we need to check if there is a module specified.
            raise UsageError("No module specified. Please specify a module using --impacted-module.")
        if not get_option("impacted_git_mode"):
            # If the impacted option is set, we need to check if there is a git mode specified.
            raise UsageError("No git mode specified. Please specify a git mode using --impacted-git-mode.")

        if get_option("impacted_git_mode") == GitMode.BRANCH and not get_option("impacted_base_branch"):
            # If the git mode is branch, we need to check if there is a base branch specified.
            raise UsageError("No base branch specified. Please specify a base branch using --impacted-base-branch.")
