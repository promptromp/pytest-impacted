import pytest
from pytest import UsageError

from pytest_affected.display import notify, warn
from pytest_affected.git import GitMode, find_modified_files_in_repo
from pytest_affected.traversal import resolve_files_to_modules, resolve_modules_to_files
from pytest_affected.graph import build_dep_tree, resolve_affected_tests
from pytest_affected.matchers import matches_affected_tests


def _get_ns_module(config) -> str:
    """Get the namespace module from the config."""
    # Get the path to the package
    # rootdir = config.rootdir
    affected_module = config.getoption("affected_module")

    return affected_module


def pytest_addoption(parser):
    """pytest hook to add command line options.

    This is called before any tests are collected.

    """
    group = parser.getgroup("affected")
    group.addoption(
        "--affected",
        action="store_true",
        default=False,
        dest="affected",
        help="Run only tests affected by the chosen git state.",
    )

    group.addoption(
        "--affected-module",
        action="store",
        default=None,
        dest="affected_module",
        help="Module name to check for affected tests.",
    )

    group.addoption(
        "--affected-git-mode",
        action="store",
        default=False,
        dest="affected_git_mode",
        choices=GitMode.__members__.values(),
        const=GitMode.UNSTAGED,
        nargs="?",
        help="Git reference for computing affected files.",
    )
    group.addoption(
        "--affected-base-branch",
        action="store",
        default=None,
        dest="affected_base_branch",
        help="Git reference for computing affected files when running in 'branch' git mode.",
    )


def pytest_configure(config):
    """pytest hook to configure the plugin.

    This is called after the command line options have been parsed.

    """
    if config.getoption("affected"):
        if not config.getoption("affected_module"):
            # If the affected option is set, we need to check if there is a module
            # specified.
            raise UsageError(
                "No module specified. Please specify a module using --affected-module."
            )

        if config.getoption(
            "affected_git_mode"
        ) == GitMode.BRANCH and not config.getoption("affected_base_branch"):
            # If the git mode is branch, we need to check if there is a base branch
            # specified.
            raise UsageError(
                "No base branch specified. Please specify a base branch using --affected-base-branch."
            )

    config.addinivalue_line(
        "markers",
        "affected(state): mark test as affected by the state of the git repository",
    )


def pytest_collection_modifyitems(session, config, items):
    """pytest hook to modify the collected test items.

    This is called after the tests have been collected and before
    they are run.

    """
    affected = config.getoption("affected")
    if not affected:
        return

    ns_module = _get_ns_module(config)
    affected_tests = _get_affected_tests(config, ns_module=ns_module, session=session)
    if not affected_tests:
        items[:] = []
        return

    affected_items = []
    for item in items:
        item_path = item.location[0]
        if matches_affected_tests(item_path, affected_tests=affected_tests):
            # notify(f"matched affected item_path:  {item.location}", session)
            item.add_marker(pytest.mark.affected)
            affected_items.append(item)
        else:
            # Mark the item as skipped if it is not affected. This will be used to
            # let pytest know to skip the test.
            item.add_marker(pytest.mark.skip)


def _get_affected_tests(config, ns_module, session=None) -> list[str] | None:
    """Get the list of affected tests based on the git state and static analysis."""
    git_mode = config.getoption("affected_git_mode")
    base_branch = config.getoption("affected_base_branch")
    modified_files = find_modified_files_in_repo(
        config.rootdir, git_mode=git_mode, base_branch=base_branch
    )
    if not modified_files:
        notify(
            "No modified files found in the repository. Please check your git state and the value supplied to --affected-git-mode if you expected otherwise.",
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
            "No affected Python modules detected. Modified files were: {modified_files}",
            session,
        )
        return None

    dep_tree = build_dep_tree(ns_module)

    affected_test_modules = resolve_affected_tests(modified_modules, dep_tree)
    if not affected_test_modules:
        warn(
            "Not unit-test modules affected by the changes could be detected. Modified Python modules were: {modified_modules}",
            session,
        )
        return None

    affected_test_files = resolve_modules_to_files(affected_test_modules)
    if not affected_test_files:
        warn(
            "No unit-test file paths affected by the changes could be found. Affected test modules were: {affected_test_modules}",
            session,
        )
        return None

    notify(
        f"Affected unit-test files in the repository: {affected_test_files}",
        session,
    )

    return affected_test_files
