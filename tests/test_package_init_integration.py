"""End-to-end tests for the analysed package's own ``__init__.py``, via pytester and a real git repo.

``import app`` and ``from app import name`` run ``app/__init__.py``, so an edit to it — or
to a module it imports and re-exports — must select the tests that import from the package.
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


@pytest.mark.parametrize("package_dir", ["app", "src/app"])
def test_an_init_edit_does_not_select_a_test_importing_only_a_submodule(make_git_project, package_dir):
    """``from app.core import Thing`` runs ``app/__init__.py`` too, but is not linked to it (documented)."""
    ini = f"[pytest]\npythonpath = {package_dir.removesuffix('app') or '.'}\nimpacted_module = {package_dir}\n"
    project = make_git_project(
        {
            f"{package_dir}/__init__.py": "from app.core import Thing\n",
            f"{package_dir}/core.py": "class Thing:\n    pass\n",
            "suite/test_core.py": "from app.core import Thing\n\ndef test_core():\n    assert Thing\n",
        },
        ini + "impacted_tests_dir = suite\n",
    )
    edit_file(project, f"{package_dir}/__init__.py")

    result = project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged")

    result.assert_outcomes(skipped=1)
