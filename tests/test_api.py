"""Unit-tests for the api module."""

from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import networkx as nx
import pytest

from pytest_impacted.api import get_impacted_tests, matches_impacted_tests
from pytest_impacted.git import GitMode
from pytest_impacted.strategies import ImpactStrategy, cached_build_dep_tree


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
@patch("pytest_impacted.api.resolve_modules_to_files")
@patch("pytest_impacted.api.cached_build_dep_tree")
def test_get_impacted_tests_conftest_imports(
    mock_cached_build_dep_tree,
    mock_resolve_modules_to_files,
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
    mock_resolve_modules_to_files.side_effect = lambda modules, **_: [m.replace(".", "/") + ".py" for m in modules]

    result = get_impacted_tests(
        impacted_git_mode=GitMode.UNSTAGED,
        impacted_base_branch="main",
        root_dir=tmp_path,
        ns_module="project_ns",
        tests_dir="tests",
        conftest_imports=conftest_imports,
    )

    assert result == expected


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
    """A conftest created after the graph was cached is not a node, so it must not become an
    impacted module: one outside the graph reads as a production module and selects every test."""
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
