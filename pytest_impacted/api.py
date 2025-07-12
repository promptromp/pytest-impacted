"""Matchers used for pattern matching and unit-tests."""

import logging
import sys
from pathlib import Path

from pytest_impacted.display import notify, warn
from pytest_impacted.git import GitMode, find_impacted_files_in_repo
from pytest_impacted.strategies import (
    ASTImpactStrategy,
    CompositeImpactStrategy,
    ImpactStrategy,
    PytestImpactStrategy,
)
from pytest_impacted.traversal import (
    path_to_package_name,
    resolve_files_to_modules,
    resolve_modules_to_files,
)


def matches_impacted_tests(item_path: str, *, impacted_tests: list[str]) -> bool:
    """Check if the item path matches any of the impacted tests."""
    for test in impacted_tests:
        if test.endswith(item_path):
            return True

    return False


def get_impacted_tests(
    impacted_git_mode: GitMode,
    impacted_base_branch: str,
    root_dir: Path,
    ns_module: str,
    tests_dir: str | None = None,
    session=None,
    strategy: ImpactStrategy | None = None,
) -> list[str] | None:
    """Get the list of impacted tests based on the git state and static analysis."""
    git_mode = impacted_git_mode
    base_branch = impacted_base_branch

    # Use default strategy if none provided
    if strategy is None:
        strategy = CompositeImpactStrategy(
            [
                ASTImpactStrategy(),
                PytestImpactStrategy(),
            ]
        )

    tests_package = None
    if tests_dir:
        # Add the parent directory of the tests_dir to sys.path
        # so that we can import the tests_dir as a module.
        tests_dir_path = Path(tests_dir).resolve().parent
        if str(tests_dir_path) not in sys.path:
            logging.debug("Adding tests_dir parent directory to sys.path: %s", tests_dir_path)
            sys.path.insert(0, str(tests_dir_path))
        tests_package = path_to_package_name(tests_dir)

    impacted_files = find_impacted_files_in_repo(
        root_dir,
        git_mode=git_mode,
        base_branch=base_branch,
    )
    if not impacted_files:
        notify(
            "No modified files found in the repository. "
            + "Please check your git state and the value supplied to --impacted-git-mode if you expected otherwise.",
            session,
        )
        return None

    notify(
        f"Impacted files in the repository: {impacted_files}",
        session,
    )

    impacted_modules = resolve_files_to_modules(impacted_files, ns_module=ns_module, tests_package=tests_package)
    if not impacted_modules:
        notify(
            f"No impacted Python modules detected. Impacted files were: {impacted_files}",
            session,
        )
        return None

    # Use the strategy to find impacted test modules
    impacted_test_modules = strategy.find_impacted_tests(
        changed_files=impacted_files,
        impacted_modules=impacted_modules,
        ns_module=ns_module,
        tests_package=tests_package,
        root_dir=root_dir,
        session=session,
    )

    if not impacted_test_modules:
        warn(
            "No unit-test modules impacted by the changes could be detected. "
            + f"Impacted Python modules were: {impacted_modules}",
            session,
        )
        return None

    impacted_test_files = resolve_modules_to_files(impacted_test_modules)
    if not impacted_test_files:
        warn(
            "No unit-test file paths impacted by the changes could be found. "
            + f"impacted test modules were: {impacted_test_modules}",
            session,
        )
        return None

    notify(
        f"impacted unit-test files in the repository: {impacted_test_files}",
        session,
    )

    return impacted_test_files
