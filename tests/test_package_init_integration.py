"""End-to-end tests for package ``__init__.py`` files, via pytester and a real git repo.

``import app`` and ``from app import name`` run ``app/__init__.py``, so an edit to it — or
to a module it imports and re-exports — must select the tests that import from the package.
So does ``from app.core.x import f``, which runs ``app/__init__.py`` and ``app/core/__init__.py``
first: an edit to either, adding one or deleting one, selects the tests importing anything inside.
"""

import pytest

from .git_helpers import edit_file


TEST_APP = "from app import create_app, Thing\n\ndef test_app():\n    assert isinstance(create_app(), Thing)\n"


def files(package_dir: str) -> dict[str, str]:
    return {
        f"{package_dir}/__init__.py": "from app.core import Thing\n\ndef create_app():\n    return Thing()\n",
        f"{package_dir}/core.py": "class Thing:\n    pass\n",
        "suite/test_app.py": TEST_APP,
        "suite/test_other.py": "def test_other():\n    assert True\n",
    }


@pytest.mark.parametrize("package_dir", ["app", "src/app"])
@pytest.mark.parametrize("edited", ["__init__.py", "core.py"], ids=["the_init", "a_module_it_re_exports"])
def test_an_edit_reaching_the_package_root_selects_tests_importing_from_it(make_git_project, package_dir, edited):
    ini = f"[pytest]\npythonpath = {package_dir.removesuffix('app') or '.'}\nimpacted_module = {package_dir}\n"
    project = make_git_project(files(package_dir), ini + "impacted_tests_dir = suite\n")
    edit_file(project, f"{package_dir}/{edited}")

    result = project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v")

    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*test_app.py::test_app PASSED*"])


def ini(package_dir: str) -> str:
    pythonpath = package_dir.removesuffix("app") or "."
    return f"[pytest]\npythonpath = {pythonpath}\nimpacted_module = {package_dir}\nimpacted_tests_dir = suite\n"


def run(project, *args):
    return project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


def assert_selected(result, *, passed=(), skipped=()):
    result.assert_outcomes(passed=len(passed), skipped=len(skipped))
    result.stdout.fnmatch_lines_random(
        [f"*{name}::* PASSED*" for name in passed] + [f"*{name}::* SKIPPED*" for name in skipped]
    )


SUBPACKAGE = {
    "{app}/__init__.py": "",
    "{app}/core/__init__.py": "READY = True\n",
    # No import of ``app.core`` itself: only running it links the two.
    "{app}/core/x.py": "import sys\n\ndef f():\n    return sys.modules['app.core'].READY\n",
    "{app}/other.py": "def g():\n    return 1\n",
    "suite/test_x.py": "from app.core.x import f\n\ndef test_x():\n    assert f()\n",
    "suite/test_other.py": "from app.other import g\n\ndef test_other():\n    assert g()\n",
}


@pytest.mark.parametrize("package_dir", ["app", "src/app"])
@pytest.mark.parametrize(
    ("edited", "passed", "skipped"),
    [
        pytest.param("__init__.py", ["test_x.py", "test_other.py"], [], id="the_root_init"),
        pytest.param("core/__init__.py", ["test_x.py"], ["test_other.py"], id="a_subpackage_init"),
    ],
)
def test_an_init_edit_selects_the_tests_importing_anything_inside_its_package(
    make_git_project, package_dir, edited, passed, skipped
):
    """``from app.core.x import f`` runs ``app/__init__.py`` and ``app/core/__init__.py`` first."""
    project = make_git_project({rel.format(app=package_dir): src for rel, src in SUBPACKAGE.items()}, ini(package_dir))
    edit_file(project, f"{package_dir}/{edited}")

    assert_selected(run(project), passed=passed, skipped=skipped)


@pytest.mark.parametrize("package_dir", ["app", "src/app"])
def test_deleting_an_init_selects_the_tests_importing_anything_inside_its_package(make_git_project, package_dir):
    """``app/core`` becomes a namespace package without ``READY``: the test that needs it runs, and fails."""
    project = make_git_project({rel.format(app=package_dir): src for rel, src in SUBPACKAGE.items()}, ini(package_dir))
    (project.path / f"{package_dir}/core/__init__.py").unlink()

    result = run(project)

    result.assert_outcomes(failed=1, skipped=1)
    result.stdout.fnmatch_lines(["*test_x.py::test_x FAILED*"])


TESTS_PACKAGE = {
    "app/__init__.py": "",
    "suite/__init__.py": "",
    "suite/unit/__init__.py": "",
    "suite/unit/test_a.py": "def test_a():\n    assert True\n",
    "suite/integration/test_b.py": "def test_b():\n    assert True\n",
}


@pytest.mark.parametrize(
    ("edited", "passed", "skipped"),
    [
        pytest.param("suite/__init__.py", ["test_a.py", "test_b.py"], [], id="the_tests_init"),
        pytest.param("suite/unit/__init__.py", ["test_a.py"], ["test_b.py"], id="a_nested_tests_init"),
    ],
)
def test_an_init_edit_in_the_tests_dir_selects_the_tests_inside_its_package(make_git_project, edited, passed, skipped):
    """pytest imports ``suite/unit/test_a.py`` as ``suite.unit.test_a``, running both ``__init__`` files."""
    project = make_git_project(TESTS_PACKAGE, ini("app"))
    edit_file(project, edited)

    assert_selected(run(project), passed=passed, skipped=skipped)


def test_an_init_edit_above_the_analysed_package_selects_the_tests_importing_it(make_git_project):
    project = make_git_project(
        {
            "backend/__init__.py": "",
            "backend/app/__init__.py": "",
            "backend/app/x.py": "def f():\n    return 1\n",
            "suite/test_x.py": "from backend.app.x import f\n\ndef test_x():\n    assert f()\n",
            "suite/test_other.py": "def test_other():\n    assert True\n",
        },
        "[pytest]\npythonpath = .\nimpacted_module = backend/app\nimpacted_tests_dir = suite\n",
    )
    edit_file(project, "backend/__init__.py")

    result = run(project)

    assert_selected(result, passed=["test_x.py"], skipped=["test_other.py"])
    assert "no test depends on" not in result.stdout.str()
