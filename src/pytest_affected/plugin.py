import pytest

from pytest_affected.git import find_modified_files_in_repo
from pytest_affected.traversal import resolve_files_to_modules, resolve_modules_to_files
from pytest_affected.graph import build_dep_tree, resolve_affected_tests
from pytest_affected.matchers import matches_affected_tests


def _get_ns_module(config) -> str:
    """Get the namespace module from the config."""
    # Get the path to the package
    # rootdir = config.rootdir

    # TODO: Parse from pytest CLI / config?
    return "mockstack"


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
            _notify(f"matched affected item_path:  {item_path}", session)
            item.add_marker(pytest.mark.affected)
            affected_items.append(item)

    items[:] = affected_items


def _get_affected_tests(config, ns_module, session=None) -> list[str] | None:
    """Get the list of affected tests based on the git state and static analysis."""
    modified_files = find_modified_files_in_repo(config.rootdir)
    if not modified_files:
        _notify(
            "No modified files found in the repository. Please check your git state and pytest-affected git_mode if you expected otherwise.",
            session,
        )
        return None

    modified_modules = resolve_files_to_modules(modified_files, ns_module=ns_module)
    if not modified_modules:
        _notify(
            "No affected Python modules detected. Modified files were: {modified_files}",
            session,
        )
        return None

    dep_tree = build_dep_tree(ns_module)

    affected_test_modules = resolve_affected_tests(modified_modules, dep_tree)
    if not affected_test_modules:
        _warn(
            "Not unit-test files affected by the changes could be detected. Modified Python modules were: {modified_modules}",
            session,
        )
        return None

    affected_test_files = resolve_modules_to_files(affected_test_modules)

    print(f"affected_test_files: {affected_test_files}")
    return affected_test_files


def _notify(message: str, session) -> None:
    """Print a message to the console."""
    session.config.pluginmanager.getplugin("terminalreporter").write(
        f"\n{message}\n",
        yellow=True,
        bold=True,
    )


def _warn(message: str, session) -> None:
    """Print a warning message to the console."""
    session.config.pluginmanager.getplugin("terminalreporter").write(
        f"\nWARNING: {message}\n",
        yellow=True,
        bold=True,
    )
