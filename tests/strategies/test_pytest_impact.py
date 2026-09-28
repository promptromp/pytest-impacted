"""Unit-tests for the strategies module."""

import os
import sys
import tempfile
from pathlib import Path

import networkx as nx
import pytest

from pytest_impacted.strategies import (
    PytestImpactStrategy,
    _tests_under_conftests,
    find_test_modules_under,
)


class TestPytestImpactStrategy:
    """Test the pytest-specific impact strategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.root_dir = Path(self.temp_dir)

    def test_find_impacted_tests_through_the_import_graph(self):
        """Without any conftest involved, the strategy still follows imports on its own."""
        dep_tree = nx.DiGraph(
            [("mypackage.a", "mypackage.b"), ("mypackage.b", "tests.test_b"), ("mypackage.c", "tests.test_c")]
        )

        result = PytestImpactStrategy().find_impacted_tests(
            changed_files=["mypackage/a.py"],
            impacted_modules=["mypackage.a"],
            ns_module="mypackage",
            tests_package="tests",
            root_dir=self.root_dir,
            dep_tree=dep_tree,
        )

        assert result == ["tests.test_b"]

    def test_find_impacted_tests_with_conftest(self):
        """A changed conftest selects every test in its directory and below — and nothing beside it."""
        for rel in ("tests/conftest.py", "tests/test_top.py", "tests/subdir/test_example.py", "other/test_x.py"):
            (self.root_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root_dir / rel).touch()
        dep_tree = nx.DiGraph()
        dep_tree.add_nodes_from(["tests.test_top", "tests.subdir.test_example", "other.test_x", "module_b"])

        result = PytestImpactStrategy().find_impacted_tests(
            changed_files=["tests/conftest.py"],
            impacted_modules=[],
            ns_module="mypackage",
            tests_package="tests",
            root_dir=self.root_dir,
            dep_tree=dep_tree,
        )

        assert result == ["tests.subdir.test_example", "tests.test_top"]

    def test_find_test_modules_under_conftest_dir(self):
        """The conftest rule uses the shared "same directory and below" helper."""
        # Create test directory structure
        test_dir = self.root_dir / "tests"
        test_dir.mkdir()
        subdir = test_dir / "subdir"
        subdir.mkdir()
        (subdir / "test_example.py").touch()
        other_dir = self.root_dir / "other_tests"
        other_dir.mkdir()
        (other_dir / "test_other.py").touch()

        dep_tree = nx.DiGraph()
        dep_tree.add_nodes_from(["tests.subdir.test_example", "other_tests.test_other", "mypackage.core"])

        # Only the module in a subdirectory of the conftest dir is affected
        assert find_test_modules_under(test_dir, dep_tree, root_dir=self.root_dir) == ["tests.subdir.test_example"]

        # A conftest at the repo root reaches every test module...
        assert find_test_modules_under(self.root_dir, dep_tree, root_dir=self.root_dir) == [
            "other_tests.test_other",
            "tests.subdir.test_example",
        ]
        # ...and a directory holding no tests reaches none.
        assert find_test_modules_under(self.root_dir / "docs", dep_tree, root_dir=self.root_dir) == []


def test_only_a_file_named_exactly_conftest_counts(tmp_path):
    """pytest never loads ``myconftest.py``, so editing it must not select a directory."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").touch()
    dep_tree = nx.DiGraph()
    dep_tree.add_node("tests.test_a", path=str(tmp_path / "tests/test_a.py"))

    result = PytestImpactStrategy().find_impacted_tests(
        changed_files=["tests/myconftest.py"],
        impacted_modules=[],
        ns_module="pkg",
        root_dir=tmp_path,
        dep_tree=dep_tree,
    )

    assert result == []


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_the_tests_under_a_directory_include_nodes_placed_through_a_symlink_or_an_unnormalized_path(tmp_path):
    """As 0.33.0 did: an extension's ``path`` may hold ``..``, and a pathless node is placed through the tree."""
    (tmp_path / "real_tests").mkdir()
    (tmp_path / "real_tests/test_a.py").touch()
    (tmp_path / "real_tests/test_b.py").touch()
    (tmp_path / "tests").symlink_to(tmp_path / "real_tests", target_is_directory=True)
    dep_tree = nx.DiGraph()
    dep_tree.add_node("tests.test_a", path=f"{tmp_path.resolve()}/app/../real_tests/test_a.py")
    dep_tree.add_node("tests.test_b")

    found = find_test_modules_under(tmp_path / "real_tests", dep_tree, root_dir=tmp_path)

    assert found == ["tests.test_a", "tests.test_b"]


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_the_tests_under_a_conftest_directory_placed_through_a_symlinked_rootdir(tmp_path):
    """A conftest an extension added without a ``path`` is placed under the rootdir as given, a symlink."""
    (tmp_path / "real/tests").mkdir(parents=True)
    (tmp_path / "real/tests/test_a.py").touch()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    dep_tree = nx.DiGraph()
    dep_tree.add_node("tests.test_a", path=str((tmp_path / "real/tests/test_a.py").resolve()))

    assert _tests_under_conftests({tmp_path / "link/tests"}, dep_tree, tmp_path / "link") == ["tests.test_a"]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_a_node_without_a_path_in_a_listable_but_unsearchable_directory(tmp_path):
    """Rebuilding a node's file from its name must not raise where ``os.path`` says the file is missing."""
    dep_tree = nx.DiGraph([("app.x", "tests.data.test_x")])
    locked = tmp_path / "tests/data"
    locked.mkdir(parents=True)
    locked.chmod(0o644)
    try:
        found = find_test_modules_under(tmp_path / "tests", dep_tree, root_dir=tmp_path)
    finally:
        locked.chmod(0o755)

    assert found == []
