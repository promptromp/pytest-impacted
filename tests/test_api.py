"""Unit-tests for the api module."""

import sys
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import networkx as nx
import pytest

from pytest_impacted import graph
from pytest_impacted.api import get_impacted_tests, matches_impacted_tests
from pytest_impacted.git import GitMode
from pytest_impacted.strategies import ImpactStrategy, cached_build_dep_tree, run_copy

from .git_helpers import write_files


@pytest.mark.parametrize(
    ("item_path", "impacted_tests", "expected"),
    [
        pytest.param(
            "tests/test_example.py",
            ["project/module/tests/test_example.py", "project/another_module/tests/test_other.py"],
            True,
            id="suffix_match",
        ),
        pytest.param(
            "tests/test_another.py",
            ["project/module/tests/test_example.py", "project/another_module/tests/test_other.py"],
            False,
            id="no_match",
        ),
        pytest.param("tests/test_example.py", [], False, id="empty_impacted_list"),
        pytest.param(
            "project/module/tests/test_example.py",
            ["project/module/tests/test_example.py"],
            True,
            id="exact_match",
        ),
        pytest.param(
            "test_example.py",
            ["project/module/tests/test_example.pyc"],
            False,
            id="substring_not_suffix",
        ),
        pytest.param(
            "longer/path/to/tests/test_example.py",
            ["tests/test_example.py"],
            False,
            id="item_path_longer_than_impacted",
        ),
        pytest.param(
            "test_example.py",
            ["project/module/tests/foo_test_example.py"],
            False,
            id="false_suffix_no_boundary",
        ),
    ],
)
def test_matches_impacted_tests(item_path, impacted_tests, expected):
    assert matches_impacted_tests(item_path, impacted_tests=impacted_tests) is expected


@patch("pytest_impacted.api.find_impacted_files_in_repo")
def test_get_impacted_tests_no_impacted_files(mock_find_impacted_files):
    mock_find_impacted_files.return_value = []
    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
    )
    assert result is None
    mock_find_impacted_files.assert_called_once_with(
        Path("."), git_mode=GitMode.UNSTAGED, base_branch="main", use_merge_base=True, on_fallback=ANY
    )


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_success_with_tests_dir(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """Test get_impacted_tests successful path with tests_dir."""
    # Setup mocks
    mock_find_impacted_files.return_value = ["file1.py", "file2.py"]
    mock_resolve_files_to_nodes.return_value = ["module1", "module2"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py", "test_file2.py"]

    # Create a mock strategy that returns our expected test modules
    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1", "test_module2"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
        strategy=mock_strategy,
    )

    assert result == ["test_file1.py", "test_file2.py"]
    # Verify tests_package was derived from tests_dir and passed through
    mock_resolve_files_to_nodes.assert_called_once_with(["file1.py", "file2.py"], ANY, root_dir=Path("."))
    assert mock_strategy.find_impacted_tests.call_args.kwargs["tests_package"] == "tests"


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
def test_get_impacted_tests_no_impacted_modules(
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """Test get_impacted_tests when no impacted modules are found."""
    mock_find_impacted_files.return_value = ["file1.py", "file2.py"]
    mock_resolve_files_to_nodes.return_value = []

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
    )

    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
def test_get_impacted_tests_no_impacted_test_modules(
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """Test get_impacted_tests when no impacted test modules are found."""
    mock_find_impacted_files.return_value = ["file1.py"]
    mock_resolve_files_to_nodes.return_value = ["module1"]

    # Create a mock strategy that returns no test modules
    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = []

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        strategy=mock_strategy,
    )

    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_no_impacted_test_files(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """Test get_impacted_tests when no impacted test files are found."""
    mock_find_impacted_files.return_value = ["file1.py"]
    mock_resolve_files_to_nodes.return_value = ["module1"]
    mock_resolve_modules_to_files.return_value = []

    # Create a mock strategy that returns test modules
    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        strategy=mock_strategy,
    )

    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_success_without_tests_dir(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """Test get_impacted_tests successful path without tests_dir."""
    mock_find_impacted_files.return_value = ["file1.py"]
    mock_resolve_files_to_nodes.return_value = ["module1"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py"]

    # Create a mock strategy that returns test modules
    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        strategy=mock_strategy,
    )

    assert result == ["test_file1.py"]
    mock_resolve_files_to_nodes.assert_called_once_with(["file1.py"], ANY, root_dir=Path("."))
    assert mock_strategy.find_impacted_tests.call_args.kwargs["tests_package"] is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_dep_file_only_change(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """When only dependency files changed, the strategy pipeline still runs.

    Even though impacted_modules is empty (no .py files changed), the orchestrator
    always delegates to strategies — DependencyFileImpactStrategy handles this case.
    """
    mock_find_impacted_files.return_value = ["uv.lock"]
    mock_resolve_files_to_nodes.return_value = []  # No .py files -> no modules

    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1", "test_module2"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py", "test_file2.py"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
        strategy=mock_strategy,
        watch_dep_files=True,
    )

    assert result == ["test_file1.py", "test_file2.py"]
    mock_strategy.find_impacted_tests.assert_called_once()


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
def test_get_impacted_tests_dep_file_with_watch_disabled(
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """When watch_dep_files=False, DependencyFileImpactStrategy is excluded from the default
    composite. With no .py modules changed, the remaining strategies (AST, Pytest) find
    nothing, so the result is None.
    """
    mock_find_impacted_files.return_value = ["uv.lock"]
    mock_resolve_files_to_nodes.return_value = []

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        watch_dep_files=False,
    )

    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
@patch("pytest_impacted.api.cached_build_dep_tree")
def test_get_impacted_tests_invalidate_all_patterns(
    mock_cached_build_dep_tree,
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """A changed file matching a user-supplied invalidation pattern marks every test module as impacted."""
    dep_tree = nx.DiGraph()
    dep_tree.add_nodes_from(["project_ns.core", "tests.test_core", "tests.test_other"])
    mock_cached_build_dep_tree.return_value = dep_tree
    mock_find_impacted_files.return_value = ["config/settings.json"]
    mock_resolve_files_to_nodes.return_value = []
    mock_resolve_modules_to_files.side_effect = lambda modules, **_: [m.replace(".", "/") + ".py" for m in modules]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
        invalidate_all_patterns=["*.json"],
    )

    assert result == ["tests/test_core.py", "tests/test_other.py"]


@pytest.mark.parametrize(("conftest_imports", "expected"), [(False, None), (True, ["tests/test_db.py"])])
@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.cached_build_dep_tree")
def test_get_impacted_tests_conftest_imports(
    mock_cached_build_dep_tree,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
    tmp_path,
    conftest_imports,
    expected,
):
    """The default pipeline follows a conftest's imports into its directory only when asked to."""
    for rel in ("project_ns/db.py", "tests/conftest.py", "tests/test_db.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    dep_tree = nx.DiGraph()
    for rel in ("project_ns/db.py", "tests/conftest.py", "tests/test_db.py"):
        dep_tree.add_node(rel.removesuffix(".py").replace("/", "."), path=str(tmp_path / rel))
    dep_tree.add_edge("project_ns.db", "tests.conftest")
    mock_cached_build_dep_tree.return_value = dep_tree
    mock_find_impacted_files.return_value = ["project_ns/db.py"]
    mock_resolve_files_to_nodes.return_value = ["project_ns.db"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=tmp_path,
        ns_module="project_ns",
        tests_dir="tests",
        conftest_imports=conftest_imports,
    )

    assert result == (expected and [str(tmp_path / rel) for rel in expected])


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_mixed_dep_and_py_changes(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """Both dep files and .py files changed — strategy should receive all changed files."""
    mock_find_impacted_files.return_value = ["src/module.py", "uv.lock"]
    mock_resolve_files_to_nodes.return_value = ["mypackage.module"]
    mock_resolve_modules_to_files.return_value = ["test_file1.py", "test_file2.py"]

    mock_strategy = MagicMock()
    mock_strategy.find_impacted_tests.return_value = ["test_module1", "test_module2"]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="project_ns",
        tests_dir="tests",
        strategy=mock_strategy,
    )

    assert result == ["test_file1.py", "test_file2.py"]
    # Strategy should receive all changed files including uv.lock
    call_args = mock_strategy.find_impacted_tests.call_args
    assert "uv.lock" in call_args.kwargs["changed_files"]
    assert "src/module.py" in call_args.kwargs["changed_files"]


# --- Lifecycle hooks (issue #43 Gap 2) --------------------------------------


class _LifecycleSpy(ImpactStrategy):
    """Records every lifecycle method call in the order it fired."""

    def __init__(self, *, find_raises=False, result=None, enrich_adds_edge=None):
        self.events: list[str] = []
        self._find_raises = find_raises
        self._result = result or ["test_module1"]
        self._enrich_adds_edge = enrich_adds_edge  # tuple(src, dst) or None
        self.dep_tree_seen_by_find = None
        self.enrich_kwargs_seen: dict | None = None

    def enrich_dep_tree(self, dep_tree, *, ns_module, tests_package=None, root_dir=None, session=None):
        self.events.append("enrich")
        self.enrich_kwargs_seen = {
            "ns_module": ns_module,
            "tests_package": tests_package,
            "root_dir": root_dir,
            "session": session,
        }
        if self._enrich_adds_edge is not None:
            dep_tree.add_edge(*self._enrich_adds_edge)

    def setup(self, *, ns_module, tests_package=None, root_dir=None, session=None, dep_tree):
        self.events.append("setup")

    def teardown(self):
        self.events.append("teardown")

    def find_impacted_tests(
        self,
        changed_files,
        impacted_modules,
        ns_module,
        tests_package=None,
        root_dir=None,
        session=None,
        *,
        dep_tree,
    ):
        self.events.append("find")
        self.dep_tree_seen_by_find = dep_tree
        if self._find_raises:
            raise RuntimeError("boom from find_impacted_tests")
        return self._result


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_calls_setup_find_teardown_in_order(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """api.get_impacted_tests must invoke setup → find → teardown on the strategy."""
    mock_find_impacted_files.return_value = ["src/mod.py"]
    mock_resolve_files_to_nodes.return_value = ["pkg.mod"]
    mock_resolve_modules_to_files.return_value = ["tests/test_mod.py"]

    spy = _LifecycleSpy()
    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="pkg",
        tests_dir="tests",
        strategy=spy,
    )
    assert spy.events == ["enrich", "setup", "find", "teardown"]


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
def test_get_impacted_tests_teardown_fires_even_when_find_raises(
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """The try/finally in api.py must guarantee teardown when find_impacted_tests raises."""
    mock_find_impacted_files.return_value = ["src/mod.py"]
    mock_resolve_files_to_nodes.return_value = ["pkg.mod"]

    spy = _LifecycleSpy(find_raises=True)
    with pytest.raises(RuntimeError, match="boom from find_impacted_tests"):
        get_impacted_tests(
            impacted_git_mode=GitMode.UNSTAGED,
            impacted_base_branch="main",
            root_dir=Path("."),
            ns_module="pkg",
            tests_dir="tests",
            strategy=spy,
        )
    # teardown must have fired despite the exception in find
    assert spy.events == ["enrich", "setup", "find", "teardown"]


@patch("pytest_impacted.api.cached_build_dep_tree")
@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_enrichment_is_visible_to_find_impacted_tests(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
    mock_cached_build_dep_tree,
):
    """Edges added during enrich_dep_tree must reach find_impacted_tests.

    This is the headline behavior of issue #43 Gap 1: an extension can
    inject a synthetic edge and the downstream find_impacted_tests sees
    the enriched graph.
    """
    mock_find_impacted_files.return_value = ["src/mod.py"]
    mock_resolve_files_to_nodes.return_value = ["pkg.mod"]
    mock_resolve_modules_to_files.return_value = ["tests/test_synthetic.py"]

    base_graph = nx.DiGraph()
    base_graph.add_node("pkg.mod")
    mock_cached_build_dep_tree.return_value = base_graph

    spy = _LifecycleSpy(enrich_adds_edge=("pkg.mod", "tests.test_synthetic"))
    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="pkg",
        tests_dir="tests",
        strategy=spy,
    )
    # The synthetic edge must be in the graph that find_impacted_tests received
    assert spy.dep_tree_seen_by_find is not None
    assert spy.dep_tree_seen_by_find.has_edge("pkg.mod", "tests.test_synthetic")


@patch("pytest_impacted.api.cached_build_dep_tree")
@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_does_not_pollute_cached_dep_tree(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
    mock_cached_build_dep_tree,
):
    """Mutations during enrich_dep_tree must not leak into the LRU-cached base graph.

    Regression guard for the design choice to `.copy()` the cached graph in
    api.py before handing it to the strategy pipeline. Without that copy,
    every subsequent pytest run in the same process would accumulate
    enrichment from previous runs.
    """
    mock_find_impacted_files.return_value = ["src/mod.py"]
    mock_resolve_files_to_nodes.return_value = ["pkg.mod"]
    mock_resolve_modules_to_files.return_value = []

    # This is the "LRU-cached base graph" returned by cached_build_dep_tree
    base_graph = nx.DiGraph()
    base_graph.add_node("pkg.mod")
    mock_cached_build_dep_tree.return_value = base_graph

    spy = _LifecycleSpy(enrich_adds_edge=("pkg.mod", "leak"))
    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="pkg",
        tests_dir="tests",
        strategy=spy,
    )
    # The base graph the cache returned must NOT contain the synthetic edge.
    assert not base_graph.has_edge("pkg.mod", "leak")
    assert "leak" not in base_graph.nodes


class _AliasEnricher:
    """Registers an alias for a node it adds, as an extension naming a generated module might."""

    seen = None

    def enrich_dep_tree(self, dep_tree, **kwargs):
        dep_tree.add_node("pkg.generated")
        dep_tree.graph["aliases"]["generated"] = "pkg.generated"

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, *, dep_tree, **kwargs):
        self.seen = dep_tree.graph["aliases"].get("generated")
        return []


@patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=["pkg/mod.py"])
def test_an_alias_added_during_enrichment_stays_out_of_the_cached_graph(_mock_find_impacted_files, tmp_path):
    """``DiGraph.copy()`` shares graph-level values: the run must get its own ``aliases`` too."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").touch()
    (tmp_path / "pkg/mod.py").touch()
    enricher = _AliasEnricher()

    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=tmp_path,
        ns_module="pkg",
        strategy=enricher,
    )

    assert enricher.seen == "pkg.generated"  # the run's own graph has it
    assert "generated" not in cached_build_dep_tree("pkg", root_dir=tmp_path).graph["aliases"]


@patch("pytest_impacted.api.find_impacted_files_in_repo")
@patch("pytest_impacted.api.resolve_files_to_nodes")
@patch("pytest_impacted.api.resolve_modules_to_files")
def test_get_impacted_tests_enrich_receives_full_context(
    mock_resolve_modules_to_files,
    mock_resolve_files_to_nodes,
    mock_find_impacted_files,
):
    """api.get_impacted_tests must pass ns_module/tests_package/root_dir/session
    to strategy.enrich_dep_tree so scan-based enrichers can walk the source tree.
    """
    mock_find_impacted_files.return_value = ["src/mod.py"]
    mock_resolve_files_to_nodes.return_value = ["pkg.mod"]
    mock_resolve_modules_to_files.return_value = ["tests/test_mod.py"]

    spy = _LifecycleSpy()
    root = Path("/tmp/fake-root")
    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=root,
        ns_module="pkg",
        tests_dir="tests",
        strategy=spy,
    )
    assert spy.enrich_kwargs_seen is not None
    assert spy.enrich_kwargs_seen["ns_module"] == "pkg"
    assert spy.enrich_kwargs_seen["tests_package"] == "tests"
    assert spy.enrich_kwargs_seen["root_dir"] == root
    # session is None in this test because we didn't pass one
    assert spy.enrich_kwargs_seen["session"] is None


@patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=["pkg/a.py"])
@patch("pytest_impacted.api.cached_build_dep_tree", return_value=nx.DiGraph())
def test_duck_typed_strategy_passed_directly(mock_tree, mock_files):
    """A strategy with only find_impacted_tests works through the API, lifecycle hooks and all."""

    calls = []

    class DuckTyped:
        def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
            calls.append(changed_files)
            return []

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=Path("."),
        ns_module="pkg",
        strategy=DuckTyped(),
    )

    assert calls == [["pkg/a.py"]]  # delegated to, not bypassed
    assert result is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
def test_changed_files_resolve_through_the_graph_the_run_uses(mock_find_impacted_files, tmp_path):
    """A conftest created after the graph was cached is no node of it: linked into the run's copy as
    an ``external`` node, it must not read as a production module outside the graph, which would
    select every test."""
    for rel in ("backend/app/__init__.py", "backend/app/db.py", "suite/test_a.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("import backend.app.db\n" if rel.startswith("suite") else "")
    run = {"impacted_git_mode": GitMode.UNSTAGED, "impacted_base_branch": "main", "root_dir": tmp_path}
    run |= {"ns_module": "backend/app", "tests_dir": "suite"}
    mock_find_impacted_files.return_value = ["backend/app/db.py"]
    assert get_impacted_tests(**run) == [str((tmp_path / "suite/test_a.py").resolve())]

    (tmp_path / "backend/conftest.py").write_text("")
    mock_find_impacted_files.return_value = ["backend/conftest.py"]

    assert get_impacted_tests(**run) is None


@patch("pytest_impacted.api.find_impacted_files_in_repo")
def test_changed_files_resolve_against_the_run_graph_before_enrichment(mock_find_impacted_files):
    """The run's own copy of the graph, as the strategies receive it, before any extension enriched it."""
    mock_find_impacted_files.return_value = ["file1.py"]
    calls = []
    strategy = MagicMock(spec=ImpactStrategy)
    strategy.enrich_dep_tree.side_effect = lambda dep_tree, **_: calls.append(("enrich", dep_tree))
    strategy.find_impacted_tests.return_value = []

    with patch("pytest_impacted.api.resolve_files_to_nodes") as resolve:
        resolve.side_effect = lambda files, dep_tree, **_: calls.append(("resolve", dep_tree)) or []
        get_impacted_tests(
            impacted_git_mode=GitMode.UNSTAGED,
            impacted_base_branch="main",
            root_dir=Path("."),
            ns_module="project_ns",
            strategy=strategy,
        )

    assert [step for step, _ in calls] == ["resolve", "enrich"]
    assert calls[0][1] is calls[1][1] is strategy.find_impacted_tests.call_args.kwargs["dep_tree"]


def test_a_run_copy_shares_no_graph_level_value_with_the_cached_graph():
    cached = nx.DiGraph()
    cached.graph["lists"] = {"a": [1]}

    copy = run_copy(cached)
    copy.graph["lists"]["a"].append(2)

    assert cached.graph["lists"] == {"a": [1]}
    assert copy.graph["aliases"] == {}  # every run graph has one, as enrichers expect


class _GeneratedTestEnricher:
    """Adds a test module no walk finds, with its ``path``, as the extension docs ask."""

    def __init__(self, path):
        self.path = path

    def enrich_dep_tree(self, dep_tree, **kwargs):
        dep_tree.add_node("generated.test_gen", path=self.path)
        dep_tree.add_edge("pkg.mod", "generated.test_gen")

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, *, dep_tree, **kwargs):
        return graph.resolve_impacted_tests(impacted_modules, dep_tree)


@patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=["pkg/mod.py"])
def test_an_impacted_test_module_maps_to_its_node_path(_mock_find_impacted_files, tmp_path):
    """A test node an extension added outside the walks keeps its file: no second discovery drops it."""
    for rel in ("pkg/__init__.py", "pkg/mod.py", "generated/test_gen.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    generated = str((tmp_path / "generated/test_gen.py").resolve())

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=tmp_path,
        ns_module="pkg",
        strategy=_GeneratedTestEnricher(generated),
    )

    assert result == [generated]


@patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=["pkg/gone.py"])
def test_a_deleted_module_joins_the_run_graph_only(_mock_find_impacted_files, tmp_path):
    """Linked on the run's copy: the cached graph never learns of a file one run saw deleted."""
    for rel, source in {"pkg/__init__.py": "", "pkg/mod.py": "def f():\n    import pkg.gone\n"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)
    seen = []

    class Recorder:
        def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
            seen.extend(impacted_modules)
            return []

    get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=tmp_path,
        ns_module="pkg",
        strategy=Recorder(),
    )

    assert seen == ["pkg.gone"]
    assert "pkg.gone" not in cached_build_dep_tree("pkg", root_dir=tmp_path)


@patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=["tests/test_base.py"])
def test_a_deleted_test_module_is_no_test_file(_mock_find_impacted_files, tmp_path):
    """Linked to the test that still imports it, but never handed to pytest: it is gone."""
    files = {
        "pkg/__init__.py": "",
        "tests/test_child.py": "def test_child():\n    from tests.test_base import Base\n",
    }
    for rel, source in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=tmp_path,
        ns_module="pkg",
        tests_dir="tests",
    )

    assert result == [str((tmp_path / "tests/test_child.py").resolve())]


@patch("pytest_impacted.api.resolve_modules_to_files")
@patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=["pkg/mod.py"])
def test_test_modules_with_a_path_need_no_discovery(_mock_find_impacted_files, mock_resolve_modules_to_files, tmp_path):
    """Discovery re-walks the project: skipped when every impacted test module is a node with a file."""
    for rel, source in {"pkg/__init__.py": "", "pkg/mod.py": "", "tests/test_mod.py": "import pkg.mod\n"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=tmp_path,
        ns_module="pkg",
        tests_dir="tests",
    )

    assert result == [str((tmp_path / "tests/test_mod.py").resolve())]
    mock_resolve_modules_to_files.assert_not_called()


@pytest.mark.parametrize("changed", ["tests/integration/test_flow.py", "tests/integration/flow_tests.py"])
def test_a_changed_test_file_outside_the_tests_walk_is_still_a_test(tmp_path, changed):
    """pytest collects ``tests/integration`` even when ``--impacted-tests-dir`` names ``tests/unit``;
    a linked file is judged by the same name rule as a walked one (``python_files`` may be anything)."""
    with patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=[changed]):
        _run_with_a_changed_test_file(tmp_path, changed)


def _run_with_a_changed_test_file(tmp_path, changed):
    for rel in ("pkg/__init__.py", "tests/unit/test_a.py", changed):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=tmp_path,
        ns_module="pkg",
        tests_dir="tests/unit",
    )

    assert result == [str((tmp_path / changed).resolve())]


def test_a_changed_test_file_with_a_dotted_stem_outside_the_tests_walk_is_a_test(tmp_path):
    with patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=["checks/test_flow.v2.py"]):
        _run_with_a_changed_test_file(tmp_path, "checks/test_flow.v2.py")


def _run_with_notices(tmp_path, changed, **kwargs):
    """``get_impacted_tests`` over *changed*: the selected files, relative, and the notices printed."""
    notices = []
    with (
        patch("pytest_impacted.api.find_impacted_files_in_repo", return_value=changed),
        patch("pytest_impacted.api.notify", side_effect=lambda message, session: notices.append(message)),
    ):
        result = get_impacted_tests(
            impacted_git_mode=GitMode.UNSTAGED, impacted_base_branch="main", root_dir=tmp_path, **kwargs
        )
    return sorted(Path(file).relative_to(tmp_path.resolve()).as_posix() for file in result or []), notices


UNIMPORTED = "Import analysis selects no tests"


@pytest.mark.parametrize(
    ("files", "changed", "tests_dir", "expected"),
    [
        pytest.param(
            {"pkg/__init__.py": "", "tests/unit/test_a.py": "", "tests/integration/test_flow.py": ""},
            ["tests/integration/test_flow.py"],
            "tests/unit",
            ["tests/integration/test_flow.py"],
            id="a_test_module_outside_the_tests_walk",
        ),
        pytest.param(
            {
                "pkg/__init__.py": "",
                "pkg/mod.py": "def f():\n    import pkg.gone\n",
                "tests/test_mod.py": "import pkg.mod\n",
            },
            ["pkg/gone.py"],
            "tests",
            ["tests/test_mod.py"],
            id="a_deleted_module_something_imports",
        ),
    ],
)
def test_a_changed_file_that_selects_tests_gets_no_unimported_notice(tmp_path, files, changed, tests_dir, expected):
    write_files(tmp_path, files)

    result, notices = _run_with_notices(tmp_path, changed, ns_module="pkg", tests_dir=tests_dir)

    assert result == expected
    assert not [notice for notice in notices if UNIMPORTED in notice]


@pytest.mark.parametrize(
    ("files", "changed"),
    [
        pytest.param(
            {"pkg/__init__.py": "", "pkg/cli.py": "def main():\n    import scripts.gone\n", "tests/unit/test_a.py": ""},
            "scripts/gone.py",
            id="a_deleted_module_only_untested_code_imports",
        ),
    ],
)
def test_a_changed_file_no_test_depends_on_is_named_in_the_notice(tmp_path, files, changed):
    """The notice says what import analysis did: no test depends on the file, itself included."""
    write_files(tmp_path, files)

    result, notices = _run_with_notices(tmp_path, [changed], ns_module="pkg", tests_dir="tests/unit")

    assert result == []
    assert [notice for notice in notices if UNIMPORTED in notice and changed in notice]


def test_placed_and_unplaced_test_modules_both_map_to_files(tmp_path):
    """A node with a ``path``, and an alias that is no node, which discovery places."""
    write_files(tmp_path, {"app/__init__.py": "", "app/tests/test_x.py": "", "app/tests/test_y.py": ""})

    class Mixed:
        def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
            return ["app.tests.test_x", "tests.test_y"]

    result, _ = _run_with_notices(
        tmp_path, ["app/tests/test_x.py"], ns_module="app", tests_dir="app/tests", strategy=Mixed()
    )

    assert result == ["app/tests/test_x.py", "app/tests/test_y.py"]


def test_deleting_an_application_module_a_root_conftest_reaches_selects_only_its_importers(tmp_path):
    """The deleted module is placed by what imports it: application code, so the conftest rule is opt-in."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core.py": "def thing():\n    from app.utils import add\n",
            "suite/conftest.py": "from app.core import thing\n",
            "suite/core/test_core.py": "from app.core import thing\n",
            "suite/other/test_other.py": "",
        },
    )

    result, _ = _run_with_notices(tmp_path, ["app/utils.py"], ns_module="app", tests_dir="suite")

    assert result == ["suite/core/test_core.py"]


def test_a_full_run_does_not_list_a_deleted_test_module(tmp_path):
    write_files(
        tmp_path,
        {"pkg/__init__.py": "", "tests/test_child.py": "def test_child():\n    from tests.test_base import Base\n"},
    )

    result, _ = _run_with_notices(tmp_path, ["uv.lock", "tests/test_base.py"], ns_module="pkg", tests_dir="tests")

    assert result == ["tests/test_child.py"]


# An edited ``__init__.py`` runs first whenever anything inside its package is imported:
# ``from app.core.x import f`` runs ``app/__init__.py`` and ``app/core/__init__.py``.


def _package(prefix: str) -> dict[str, str]:
    """``app`` under *prefix*, and tests outside it that each import one module from it by its full name."""
    app = f"{prefix}app"
    return {
        f"{app}/__init__.py": "",
        f"{app}/core/__init__.py": "import os\n",
        f"{app}/core/x.py": "def f():\n    pass\n",
        f"{app}/core/sub/__init__.py": "",
        f"{app}/core/sub/deep.py": "",
        f"{app}/core/ns/leaf.py": "",  # a namespace portion inside the package
        f"{app}/core_utils.py": "",  # siblings whose names start like the package's
        f"{app}/corex/__init__.py": "",
        f"{app}/corex/y.py": "",
        f"{app}/other.py": "",
        "tests/test_x.py": "from app.core.x import f\n",
        "tests/test_deep.py": "import app.core.sub.deep\n",
        "tests/test_leaf.py": "import app.core.ns.leaf\n",
        "tests/test_utils.py": "import app.core_utils\n",
        "tests/test_corex.py": "import app.corex.y\n",
        "tests/test_other.py": "import app.other\n",
        "tests/test_none.py": "import os\n",
    }


def _tests(*names: str) -> list[str]:
    return [f"tests/test_{name}.py" for name in sorted(names)]


@pytest.mark.parametrize("prefix", ["", "src/"], ids=["flat", "src_layout"])
@pytest.mark.parametrize(
    ("edited", "expected"),
    [
        pytest.param("app/core/__init__.py", _tests("deep", "leaf", "x"), id="a_subpackage_init"),
        pytest.param("app/core/sub/__init__.py", _tests("deep"), id="a_nested_subpackage_init"),
        pytest.param("app/corex/__init__.py", _tests("corex"), id="a_sibling_named_like_it"),
        pytest.param(
            "app/__init__.py", _tests("corex", "deep", "leaf", "other", "utils", "x"), id="the_package_root_init"
        ),
        pytest.param("app/core/x.py", _tests("x"), id="a_module_is_not_a_package"),
    ],
)
def test_an_edited_init_selects_the_tests_importing_anything_inside_its_package(tmp_path, prefix, edited, expected):
    write_files(tmp_path, _package(prefix))

    result, _ = _run_with_notices(tmp_path, [prefix + edited], ns_module=f"{prefix}app", tests_dir="tests")

    assert result == expected


@pytest.mark.parametrize("prefix", ["", "src/"], ids=["flat", "src_layout"])
def test_a_deleted_init_selects_the_tests_importing_anything_inside_its_package(tmp_path, prefix):
    """``app/core`` is a namespace package now: its modules still import, without the old ``__init__``."""
    write_files(tmp_path, _package(prefix))
    (tmp_path / f"{prefix}app/core/__init__.py").unlink()

    result, _ = _run_with_notices(
        tmp_path, [f"{prefix}app/core/__init__.py"], ns_module=f"{prefix}app", tests_dir="tests"
    )

    assert result == _tests("deep", "leaf", "x")


def test_an_added_init_selects_the_tests_importing_anything_inside_its_package(tmp_path):
    write_files(tmp_path, {**_package(""), "app/core/ns/__init__.py": ""})

    result, _ = _run_with_notices(tmp_path, ["app/core/ns/__init__.py"], ns_module="app", tests_dir="tests")

    assert result == _tests("leaf")


TESTS_PACKAGE = {
    "app/__init__.py": "",
    "tests/__init__.py": "",
    "tests/unit/__init__.py": "",
    "tests/unit/test_a.py": "",
    "tests/integration/test_b.py": "",  # a namespace portion: still ``tests.integration.test_b``
}


@pytest.mark.parametrize(
    ("tests_dir", "edited", "expected"),
    [
        pytest.param(
            "tests", "tests/__init__.py", ["tests/integration/test_b.py", "tests/unit/test_a.py"], id="the_tests_init"
        ),
        pytest.param("tests", "tests/unit/__init__.py", ["tests/unit/test_a.py"], id="a_nested_tests_init"),
        pytest.param("tests/unit", "tests/__init__.py", ["tests/unit/test_a.py"], id="an_init_above_the_tests_dir"),
    ],
)
def test_an_edited_init_in_the_tests_dir_selects_the_tests_inside_its_package(tmp_path, tests_dir, edited, expected):
    write_files(tmp_path, TESTS_PACKAGE)

    result, _ = _run_with_notices(tmp_path, [edited], ns_module="app", tests_dir=tests_dir)

    assert [file for file in result if not file.endswith("__init__.py")] == expected


ABOVE_THE_MODULE = {
    "backend/__init__.py": "import os\n",
    "backend/app/__init__.py": "",
    "backend/app/x.py": "",
    "suite/test_x.py": "import backend.app.x\n",
    "suite/test_other.py": "",
}


def test_an_edited_init_above_the_analysed_package_selects_the_tests_importing_it(tmp_path):
    """``import backend.app.x`` runs ``backend/__init__.py``, which no walk reaches: no notice either."""
    write_files(tmp_path, ABOVE_THE_MODULE)

    result, notices = _run_with_notices(tmp_path, ["backend/__init__.py"], ns_module="backend/app", tests_dir="suite")

    assert result == ["suite/test_x.py"]
    assert not [notice for notice in notices if UNIMPORTED in notice]


def test_an_edited_init_above_both_analysed_dirs_selects_the_tests_inside_it(tmp_path):
    files = {**ABOVE_THE_MODULE, "backend/tests/__init__.py": "", "backend/tests/test_y.py": "", "suite/test_x.py": ""}
    write_files(tmp_path, files)

    result, _ = _run_with_notices(tmp_path, ["backend/__init__.py"], ns_module="backend/app", tests_dir="backend/tests")

    assert [file for file in result if not file.endswith("__init__.py")] == ["backend/tests/test_y.py"]


def test_an_edited_init_whose_package_no_test_uses_is_named_in_the_notice(tmp_path):
    write_files(tmp_path, {**ABOVE_THE_MODULE, "scripts/tools/__init__.py": "", "scripts/tools/run.py": ""})

    result, notices = _run_with_notices(
        tmp_path, ["scripts/tools/__init__.py"], ns_module="backend/app", tests_dir="suite"
    )

    assert result == []
    assert [notice for notice in notices if UNIMPORTED in notice and "scripts/tools/__init__.py" in notice]


CONFTEST_REACHES_THE_PACKAGE = {
    "app/__init__.py": "",
    "app/core/__init__.py": "",
    "app/core/x.py": "def f():\n    pass\n",
    "suite/conftest.py": "from app.core.x import f\n",
    "suite/test_x.py": "import app.core.x\n",
    "suite/test_a.py": "",
}


@pytest.mark.parametrize(
    ("conftest_imports", "expected"),
    [
        pytest.param(False, ["suite/test_x.py"], id="default"),
        pytest.param(True, ["suite/test_a.py", "suite/test_x.py"], id="opted_in"),
    ],
)
def test_an_edited_init_is_application_code_to_a_conftest_importing_its_package(tmp_path, conftest_imports, expected):
    write_files(tmp_path, CONFTEST_REACHES_THE_PACKAGE)

    with patch("pytest_impacted.strategies.notify") as notify:
        result, _ = _run_with_notices(
            tmp_path, ["app/core/__init__.py"], ns_module="app", tests_dir="suite", conftest_imports=conftest_imports
        )

    assert result == expected
    named = [call for call in notify.call_args_list if "['suite/conftest.py']" in call.args[0]]
    assert bool(named) is not conftest_imports


def test_an_edited_init_of_a_package_holding_a_pytest_plugin_selects_every_test(tmp_path):
    """``pytest_plugins = ["app.testing.fixtures"]`` runs ``app/testing/__init__.py`` for the whole session."""
    files = {
        "app/__init__.py": "",
        "app/core.py": "",
        "app/testing/__init__.py": "",
        "app/testing/fixtures.py": "",
        "suite/conftest.py": 'pytest_plugins = ["app.testing.fixtures"]\n',
        "suite/test_core.py": "import app.core\n",
        "suite/test_a.py": "",
    }
    write_files(tmp_path, files)

    result, _ = _run_with_notices(tmp_path, ["app/testing/__init__.py"], ns_module="app", tests_dir="suite")

    assert result == ["suite/test_a.py", "suite/test_core.py"]


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_an_edited_init_selects_the_tests_importing_a_module_symlinked_into_its_package(tmp_path):
    """``app/plugins -> ../shared/plugins``: ``import app.plugins.p`` runs ``app/__init__.py``, wherever the file is."""
    write_files(
        tmp_path,
        {
            "app/__init__.py": "",
            "shared/plugins/p.py": "",
            "tests/test_p.py": "import app.plugins.p\n",
            "tests/test_q.py": "",
        },
    )
    (tmp_path / "app/plugins").symlink_to(tmp_path / "shared/plugins", target_is_directory=True)

    result, _ = _run_with_notices(tmp_path, ["app/__init__.py"], ns_module="app", tests_dir="tests")

    assert result == ["tests/test_p.py"]


def test_the_warning_for_an_init_edit_selecting_nothing_names_the_changed_module_only(tmp_path):
    """A package's modules are impacted through its ``__init__``, but the message names what changed."""
    write_files(
        tmp_path, {"app/__init__.py": "", "app/core/__init__.py": "", "app/core/x.py": "", "tests/test_a.py": ""}
    )

    with patch("pytest_impacted.api.warn") as warn:
        result, _ = _run_with_notices(tmp_path, ["app/core/__init__.py"], ns_module="app", tests_dir="tests")

    assert result == []
    assert "['app.core']" in warn.call_args.args[0]
