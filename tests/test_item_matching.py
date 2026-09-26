"""End-to-end: tests are selected by the file they were collected from, not their source location.

pytest reports ``item.location`` as where the test *function* lives. For an inherited
test method that is the base class's module, and for a pytest-bdd scenario it is inside
the ``pytest_bdd`` package. Matching on it skipped those tests whenever their own file was
impacted — silently, since skipped is not failed.
"""

import os
import subprocess

import pytest

from pytest_impacted.strategies import clear_dep_tree_cache

from .conftest import isolated_git_env


FILES = {
    "app/__init__.py": "",
    "app/calc.py": "def add(a, b):\n    return a + b\n",
    # Not "tests": pytester runs in-process and shares sys.modules with this repo's own package.
    "suite/checks.py": (
        "from app.calc import add\n\n\nclass Checks:\n    def test_add(self):\n        assert add(1, 1) == 2\n"
    ),
    "suite/test_child.py": "from suite.checks import Checks\n\n\nclass TestChild(Checks):\n    pass\n",
    "suite/test_other.py": "def test_other():\n    assert True\n",
}
INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"


@pytest.fixture
def project(pytester):
    clear_dep_tree_cache()
    env = {**os.environ, **isolated_git_env(pytester.path / "git-home")}
    for rel, source in {**FILES, ".gitignore": "__pycache__/\n"}.items():
        path = pytester.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    pytester.makeini(INI)
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=pytester.path, env=env, check=True, capture_output=True)
    yield pytester
    clear_dep_tree_cache()


def run(pytester):
    return pytester.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-rs")


def test_an_inherited_test_runs_when_its_collecting_module_is_impacted(project):
    """``TestChild.test_add`` is defined in suite/checks.py but collected from suite/test_child.py."""
    path = project.path / "suite/test_child.py"
    path.write_text(path.read_text() + "# edited\n")

    result = run(project)

    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*test_child.py*"])


def test_an_inherited_test_is_skipped_when_only_its_base_module_is_unaffected(project):
    """The control: an unrelated change leaves the inherited test out."""
    path = project.path / "suite/test_other.py"
    path.write_text(path.read_text() + "# edited\n")

    run(project).assert_outcomes(passed=1, skipped=1)
