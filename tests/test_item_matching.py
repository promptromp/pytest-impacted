"""Which collected items an impacted test file selects.

pytest gives every item two files: ``item.path``, the file it was collected from, and
``item.location``, the file its test function is defined in. They differ for an
inherited test (the base class's module) and a pytest-bdd scenario (inside the
``pytest_bdd`` package). Either being impacted must select the item: matching only
the location skipped every pytest-bdd scenario, and matching only the path skipped
inherited tests whose base changed without a graph edge to the child.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pytest_impacted.plugin import _impacted_items

from .git_helpers import assert_selected, edit_file


# --- end to end ------------------------------------------------------------------

# Not "tests": pytester runs in-process and shares sys.modules with this repo's own package.
PACKAGED = {
    "app/__init__.py": "",
    "app/calc.py": "def add(a, b):\n    return a + b\n",
    "suite/checks.py": """\
        from app.calc import add


        class Checks:
            def test_add(self):
                assert add(1, 1) == 2
        """,
    "suite/test_child.py": "from suite.checks import Checks\n\n\nclass TestChild(Checks):\n    pass\n",
    "suite/test_other.py": "def test_other():\n    assert True\n",
}

# A rootless tests dir (no __init__.py): `from checks import Checks` resolves through
# sys.path at run time, so the import graph has no edge from the child to its base.
ROOTLESS = {
    "app/__init__.py": "",
    "app/calc.py": "def add(a, b):\n    return a + b\n",
    "suite/tests/checks.py": "class Checks:\n    def test_base(self):\n        assert True\n",
    "suite/tests/test_child.py": "from checks import Checks\n\n\nclass TestChild(Checks):\n    pass\n",
    "suite/tests/test_other.py": "def test_other():\n    assert True\n",
}
INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"


def run(project, *args):
    return project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


@pytest.mark.parametrize(
    ("files", "edited", "runs"),
    [
        pytest.param(
            PACKAGED,
            "suite/test_child.py",
            "*test_child.py::TestChild::test_add PASSED*",
            id="inherited_test_collected_from_the_edited_file",
        ),
        pytest.param(
            PACKAGED,
            "suite/test_other.py",
            "*test_other.py::test_other PASSED*",
            id="control_an_unrelated_edit",
        ),
        pytest.param(
            ROOTLESS,
            "suite/tests/checks.py",
            "*test_child.py::TestChild::test_base PASSED*",
            id="inherited_test_whose_base_changed_without_a_graph_edge",
        ),
    ],
)
def test_one_test_runs_and_the_other_is_skipped(make_git_project, files, edited, runs):
    project = make_git_project(files, INI)
    edit_file(project, edited)

    result = run(project)

    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines([runs])


def test_items_from_non_python_files_always_run(make_git_project):
    """A ``--doctest-glob`` text file cannot be judged by import analysis: it runs, never silently deselected."""
    project = make_git_project({**PACKAGED, "suite/guide.txt": ">>> 1 + 1\n2\n"}, INI)
    edit_file(project, "suite/test_other.py")

    result = run(project, "--doctest-glob=*.txt")

    result.stdout.fnmatch_lines(["*guide.txt::guide.txt PASSED*"])
    result.assert_outcomes(passed=2, deselected=1)


# --- the selection rule ------------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    for rel in ("tests/test_a.py", "tests/base.py", "tests/test_b.py", "notes/guide.txt"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    return tmp_path


@dataclass(eq=False)  # identity equality and hashing, like pytest's own nodes
class FakeItem:
    """Just the two attributes the rule reads: ``path`` (absolute) and ``location`` (root-relative)."""

    path: Path
    location: tuple[str, int, str]


def fake_item(root: Path, path: str, location: str) -> FakeItem:
    return FakeItem(path=root / path, location=(location, 1, "test_x"))


@pytest.mark.parametrize(
    ("path", "location", "impacted", "selected"),
    [
        pytest.param("tests/test_a.py", "tests/test_a.py", ["tests/test_a.py"], True, id="plain_test_impacted"),
        pytest.param("tests/test_b.py", "tests/test_b.py", ["tests/test_a.py"], False, id="plain_test_elsewhere"),
        pytest.param("tests/test_a.py", "tests/base.py", ["tests/test_a.py"], True, id="collected_from_impacted"),
        pytest.param("tests/test_a.py", "tests/base.py", ["tests/base.py"], True, id="defined_in_impacted"),
        pytest.param("tests/test_a.py", "tests/base.py", ["tests/test_b.py"], False, id="neither_impacted"),
        pytest.param("notes/guide.txt", "notes/guide.txt", [], True, id="non_python_file_always_runs"),
    ],
)
def test_impacted_items(root, path, location, impacted, selected):
    item = fake_item(root, path, location)

    assert (item in _impacted_items([item], [str(root / f) for f in impacted], root)) is selected


def test_relative_impacted_entries_are_anchored_at_the_root_not_the_cwd(root, monkeypatch):
    """Running pytest from a subdirectory must not change what a relative entry names."""
    monkeypatch.chdir(root / "tests")
    item = fake_item(root, "tests/test_a.py", "tests/test_a.py")

    assert item in _impacted_items([item], ["tests/test_a.py"], root)


def test_a_location_matched_by_suffix_is_still_selected(root):
    """Anything 0.31.0's suffix match selected stays selected, e.g. a location
    outside a symlinked rootdir, which resolves to a different physical file."""
    item = fake_item(root, "tests/test_b.py", "tests/test_a.py")

    assert item in _impacted_items([item], ["/elsewhere/checkout/tests/test_a.py"], root)


def test_distinct_items_in_the_same_file_are_judged_independently(root):
    """Real pytest items are distinct objects even in one file; each must be decided."""
    items = [fake_item(root, "tests/test_a.py", "tests/test_a.py") for _ in range(3)]

    assert _impacted_items(items, [str(root / "tests/test_a.py")], root) == set(items)


def test_non_python_items_run_even_when_nothing_is_impacted(make_git_project):
    """The "nothing impacted" path must not skip what import analysis cannot judge."""
    project = make_git_project({**PACKAGED, "suite/guide.txt": ">>> 1 + 1\n2\n", "README.md": "x\n"}, INI)
    edit_file(project, "README.md")

    result = run(project, "--doctest-glob=*.txt")

    result.stdout.fnmatch_lines(["*guide.txt::guide.txt PASSED*"])
    result.assert_outcomes(passed=1, deselected=2)


def test_doctests_in_a_changed_source_module_run(make_git_project):
    """``--doctest-modules`` collects from source modules, which are never impacted *test* files."""
    source = 'def add(a, b):\n    """\n    >>> add(1, 1)\n    2\n    """\n    return a + b\n'
    project = make_git_project({**PACKAGED, "app/calc.py": source}, INI)
    edit_file(project, "app/calc.py")

    result = run(project, "--doctest-modules", "app", "suite")

    result.stdout.fnmatch_lines(["*app/calc.py::app.calc.add PASSED*"])


# --- tests outside the analysed directories -----------------------------------------

TWO_TEST_DIRS = {
    "app/__init__.py": "",
    "app/a.py": "def a():\n    return 1\n",
    "app/b.py": "def b():\n    return 2\n",
    "suite/test_a.py": "from app.a import a\n\ndef test_a():\n    assert a() == 1\n",
    "suite/test_b.py": "from app.b import b\n\ndef test_b():\n    assert b() == 2\n",
    "integration/test_flow.py": "from app.a import a\n\ndef test_flow():\n    assert a() == 1\n",
}


@pytest.mark.parametrize(
    ("ini", "edited", "passed", "deselected"),
    [
        pytest.param(INI, "app/b.py", ["test_b.py", "test_flow.py"], ["test_a.py"], id="a second tests dir"),
        pytest.param(INI, "uv.lock", ["test_a.py", "test_b.py", "test_flow.py"], [], id="every test, all of them"),
        pytest.param(
            "[pytest]\npythonpath = .\nimpacted_module = app\n",
            "app/b.py",
            ["test_a.py", "test_b.py", "test_flow.py"],
            [],
            id="no tests dir at all",
        ),
    ],
)
@pytest.mark.parametrize("xdist", [[], ["-n", "2"]], ids=["", "xdist"])
def test_a_test_no_analysis_judges_runs_and_is_counted(make_git_project, ini, edited, passed, deselected, xdist):
    """Outside --impacted-module and --impacted-tests-dir a test file is no graph node: deselected on every run
    before, even when a change impacted "every test". It runs, as doctests do, and the summary says why."""
    project = make_git_project({**TWO_TEST_DIRS, "uv.lock": ""}, ini)
    edit_file(project, edited)

    result = run(project, *xdist)

    if xdist:  # whose summary leaves the deselected out
        result.assert_outcomes(passed=len(passed))
    else:
        assert_selected(result, passed=passed, deselected=deselected)
    unjudged = 1 if "impacted_tests_dir" in ini else 3
    expected = "test" if unjudged == 1 else "tests"
    result.stdout.fnmatch_lines([f"pytest-impacted: {unjudged} {expected} outside --impacted-module and *"])


def test_the_count_is_of_the_unjudged_tests_that_ran(make_git_project):
    """Counted after -k and -m: one -k leaves out is no test that ran."""
    project = make_git_project({**TWO_TEST_DIRS, "integration/test_other.py": "def test_other():\n    pass\n"}, INI)
    edit_file(project, "app/a.py")

    result = run(project, "-k", "flow or test_a")

    assert_selected(result, passed=["test_a.py", "test_flow.py"], deselected=["test_b.py", "test_other.py"])
    result.stdout.fnmatch_lines(["pytest-impacted: 1 test outside --impacted-module and *"])


@pytest.mark.skipif(not hasattr(os, "symlink") or sys.platform == "win32", reason="needs symlinks")
def test_a_test_reached_through_a_symlink_inside_the_package_is_judged(make_git_project):
    """The walk follows the link, so the test is judged; resolved, its path lies outside the package."""
    project = make_git_project(
        {
            "app/__init__.py": "",
            "app/a.py": "def a():\n    return 1\n",
            "common/shared/__init__.py": "",
            "common/shared/test_shared.py": "def test_shared():\n    pass\n",
        },
        "[pytest]\npythonpath = .\nimpacted_module = app\n",
    )
    os.symlink(project.path / "common" / "shared", project.path / "app" / "shared", target_is_directory=True)
    edit_file(project, "app/a.py")

    result = run(project, "app")

    assert "outside --impacted-module" not in result.stdout.str()


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_a_test_reached_through_a_link_out_of_the_project_runs(make_git_project, tmp_path):
    """pytest collects through it, no walk follows a link out of the project: no analysis judges it, so it runs."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "test_far.py").write_text("def test_far():\n    pass\n")
    project = make_git_project(TWO_TEST_DIRS, INI)
    os.symlink(outside, project.path / "suite" / "far", target_is_directory=True)
    edit_file(project, "app/b.py")

    result = run(project, "suite")

    result.assert_outcomes(passed=2, deselected=1)
    result.stdout.fnmatch_lines(["*test_far.py::test_far PASSED*", "pytest-impacted: 1 test outside *"])
