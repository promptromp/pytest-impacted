import pytest
from pytest import UsageError

from pytest_impacted.api import matches_impacted_tests, get_impacted_tests
from pytest_impacted.git import GitMode


def pytest_addoption(parser):
    """pytest hook to add command line options.

    This is called before any tests are collected.

    """
    group = parser.getgroup("impacted")
    group.addoption(
        "--impacted",
        action="store_true",
        default=False,
        dest="impacted",
        help="Run only tests impacted by the chosen git state.",
    )
    group.addoption(
        "--impacted-module",
        action="store",
        default=None,
        dest="impacted_module",
        help="Module name to check for impacted tests.",
    )
    group.addoption(
        "--impacted-git-mode",
        action="store",
        dest="impacted_git_mode",
        choices=GitMode.__members__.values(),
        default=GitMode.UNSTAGED,
        nargs="?",
        help="Git reference for computing impacted files.",
    )
    group.addoption(
        "--impacted-base-branch",
        action="store",
        default=None,
        dest="impacted_base_branch",
        help="Git reference for computing impacted files when running in 'branch' git mode.",
    )
    group.addoption(
        "--impacted-tests-dir",
        action="store",
        default=None,
        dest="impacted_tests_dir",
        help="Directory containing the unit-test files. If not specified, tests will only be found under namespace module directory.",
    )


def pytest_configure(config):
    """pytest hook to configure the plugin.

    This is called after the command line options have been parsed.

    """
    if config.getoption("impacted"):
        if not config.getoption("impacted_module"):
            # If the impacted option is set, we need to check if there is a module specified.
            raise UsageError(
                "No module specified. Please specify a module using --impacted-module."
            )

        if config.getoption(
            "impacted_git_mode"
        ) == GitMode.BRANCH and not config.getoption("impacted_base_branch"):
            # If the git mode is branch, we need to check if there is a base branch specified.
            raise UsageError(
                "No base branch specified. Please specify a base branch using --impacted-base-branch."
            )

    config.addinivalue_line(
        "markers",
        "impacted(state): mark test as impacted by the state of the git repository",
    )


def pytest_collection_modifyitems(session, config, items):
    """pytest hook to modify the collected test items.

    This is called after the tests have been collected and before
    they are run.

    """
    impacted = config.getoption("impacted")
    if not impacted:
        return

    ns_module = config.getoption("impacted_module")
    impacted_git_mode = config.getoption("impacted_git_mode")
    impacted_base_branch = config.getoption("impacted_base_branch")
    impacted_tests_dir = config.getoption("impacted_tests_dir")
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
