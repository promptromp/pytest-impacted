"""Integration tests for the strategies sub-modules."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import networkx as nx

from pytest_impacted.strategies import (
    PytestImpactStrategy,
)


class TestIntegration:
    """Integration tests for the strategy system."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.root_dir = Path(self.temp_dir)

    def test_absolute_and_relative_paths(self):
        """``changed_files`` may mix repo-relative and absolute paths; both resolve against ``root_dir``."""
        test_dir = self.root_dir / "tests"
        test_dir.mkdir()
        (test_dir / "conftest.py").touch()
        (test_dir / "test_example.py").touch()

        with patch("pytest_impacted.strategies.resolve_impacted_tests") as mock_resolve:
            dep_tree = nx.DiGraph()
            dep_tree.add_node("tests.test_example")
            mock_resolve.return_value = []

            strategy = PytestImpactStrategy()
            for conftest in ("tests/conftest.py", str(test_dir / "conftest.py")):
                result = strategy.find_impacted_tests(
                    changed_files=["src/module.py", conftest],
                    impacted_modules=[],
                    ns_module="mypackage",
                    tests_package="tests",
                    root_dir=self.root_dir,
                    dep_tree=dep_tree,
                )
                assert result == ["tests.test_example"]
