"""The AST strategy: the tests that import a changed module, directly or through other modules."""

import networkx as nx

from pytest_impacted.strategies import ASTImpactStrategy


def test_the_tests_importing_a_changed_module_directly_or_not():
    """An edge points from a module to its importer: ``core`` reaches ``api``, and both tests through it."""
    dep_tree = nx.DiGraph(
        [
            ("pkg.core", "pkg.api"),
            ("pkg.api", "tests.test_api"),
            ("pkg.core", "tests.test_core"),
            ("pkg.other", "tests.test_other"),
        ]
    )

    result = ASTImpactStrategy().find_impacted_tests(
        changed_files=["pkg/core.py"],
        impacted_modules=["pkg.core"],
        ns_module="pkg",
        tests_package="tests",
        dep_tree=dep_tree,
    )

    assert sorted(result) == ["tests.test_api", "tests.test_core"]
