import pytest

from pytest_affected.git import find_modified_files_in_repo
from pytest_affected.traversal import resolve_files_to_modules
from pytest_affected.graph import build_dep_tree, resolve_affected_tests


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


def pytest_configure(config):
    """pytest configuration hook.

    This is called after command line options have been parsed and
    before any tests are collected.

    """
    affected = config.getoption("affected")
    if not affected:
        return

    # TODO: Display any meta-data / information about the mode and affected tests.


def pytest_collection_modifyitems(session, config, items):
    """pytest hook to modify the collected test items.

    This is called after the tests have been collected and before
    they are run.

    """
    affected = config.getoption("affected")
    if not affected:
        return

    # TODO parse from pytest cli args?
    ns_module = "mockstack"

    affected_tests = _get_affected_tests(config, ns_module=ns_module)
    if not affected_tests:
        return

    affected_items = []
    for item in items:
        item_path = item.location[0]
        if item_path in affected_tests:
            item.add_marker(pytest.mark.affected)

    items[:] = affected_items


def _get_affected_tests(config, ns_module):
    """Get the list of affected tests based on the git state and static analysis."""
    modified_files = find_modified_files_in_repo(config.rootdir)
    modified_modules = resolve_files_to_modules(modified_files, ns_module=ns_module)
    dep_tree = build_dep_tree(ns_module)

    return resolve_affected_tests(modified_modules, dep_tree)
