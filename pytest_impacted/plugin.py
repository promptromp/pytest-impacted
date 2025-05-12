import pytest
from pytest import UsageError

from pytest_impacted.display import notify, warn
from pytest_impacted.git import GitMode, find_modified_files_in_repo
from pytest_impacted.traversal import resolve_files_to_modules, resolve_modules_to_files
from pytest_impacted.graph import build_dep_tree, resolve_impacted_tests
from pytest_impacted.matchers import matches_impacted_tests


def _get_ns_module(config) -> str:
    """Get the namespace module from the config."""
    # Get the path to the package
    # rootdir = config.rootdir
    impacted_module = config.getoption("impacted_module")

    return impacted_module


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

        if (
            config.getoption("impacted_git_mode") == GitMode.BRANCH
            and not config.getoption("impacted_base_branch")
        ):
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

    ns_module = _get_ns_module(config)
    impacted_tests = _get_impacted_tests(config, ns_module=ns_module, session=session)
    if not impacted_tests:
        # zero out the items list to avoid running any tests.
        items[:] = []
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


def _get_impacted_tests(config, ns_module, session=None) -> list[str] | None:
    """Get the list of impacted tests based on the git state and static analysis."""
    git_mode = config.getoption("impacted_git_mode")
    base_branch = config.getoption("impacted_base_branch")
    modified_files = find_modified_files_in_repo(
        config.rootdir, git_mode=git_mode, base_branch=base_branch
    )
    if not modified_files:
        notify(
            "No modified files found in the repository. Please check your git state and the value supplied to --impacted-git-mode if you expected otherwise.",
            session,
        )
        return None

    notify(
        f"Modified files in the repository: {modified_files}",
        session,
    )

    modified_modules = resolve_files_to_modules(modified_files, ns_module=ns_module)
    if not modified_modules:
        notify(
            "No impacted Python modules detected. Modified files were: {modified_files}",
            session,
        )
        return None

    dep_tree = build_dep_tree(ns_module)

    impacted_test_modules = resolve_impacted_tests(modified_modules, dep_tree)
    if not impacted_test_modules:
        warn(
            "Not unit-test modules impacted by the changes could be detected. Modified Python modules were: {modified_modules}",
            session,
        )
        return None

    impacted_test_files = resolve_modules_to_files(impacted_test_modules)
    if not impacted_test_files:
        warn(
            "No unit-test file paths impacted by the changes could be found. impacted test modules were: {impacted_test_modules}",
            session,
        )
        return None

    notify(
        f"impacted unit-test files in the repository: {impacted_test_files}",
        session,
    )

    return impacted_test_files
