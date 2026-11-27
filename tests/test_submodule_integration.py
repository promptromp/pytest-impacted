"""A change inside a git submodule: git reports the submodule's path, a directory, read as its ``__init__.py`` changed.

Every module under it counts as changed, and a module importing a name gone from it is reached; git is never run
inside the submodule. The app imports inside functions, so a test file still collects with the submodule empty.
"""

import os
import subprocess

import pytest

from .git_helpers import INI, assert_selected, run, write_files


LIBRARY = {
    "pkg/__init__.py": "",
    "pkg/core.py": "VALUE = 1\n",
    "pkg/util.py": "def u():\n    return 1\n",
}
APP = {
    "app/__init__.py": "",
    "app/a.py": "def f():\n    from vendor.pkg.core import VALUE\n",
    "app/b.py": "def f():\n    from vendor.pkg import util\n",
    "app/c.py": "def f():\n    import vendor.pkg.util\n",
    "app/d.py": "D = 1\n",
    **{f"suite/test_{name}.py": f"import app.{name}\n\ndef test_{name}():\n    pass\n" for name in "abcd"},
}
UNDER_VENDOR = ["test_a.py", "test_b.py", "test_c.py"]


def git(cwd, *args):
    subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def project(make_git_project, tmp_path_factory):
    """The app and its tests, with a submodule ``vendor`` holding the package ``pkg`` the app imports."""
    library = tmp_path_factory.mktemp("library")
    write_files(library, LIBRARY)
    pytester = make_git_project(APP, INI)  # sets the isolated git environment the library's commits need too
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "library"]):
        git(library, *args)
    git(pytester.path, "submodule", "add", "-q", str(library), "vendor")
    git(pytester.path, "commit", "-q", "-m", "add the submodule")
    return pytester


def bump(project, edit) -> str:
    """Commit *edit* inside the submodule on a feature branch and record it; return the base branch."""
    base = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project.path, check=True, capture_output=True, text=True
    ).stdout.strip()
    git(project.path, "checkout", "-q", "-b", "feature")
    edit(project.path / "vendor")
    git(project.path / "vendor", "commit", "-q", "-a", "-m", "inside")
    git(project.path, "commit", "-q", "-a", "-m", "bump the submodule")
    return base


def run_branch(project, base):
    return project.runpytest(
        "--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=branch", f"--impacted-base-branch={base}", "-v"
    )


def test_an_uncommitted_change_inside_a_submodule_selects_the_tests_of_every_module_under_it(project):
    """Coarse but safe: which file changed inside it is not asked, so ``core`` changing runs ``util``'s tests too."""
    (project.path / "vendor/pkg/core.py").write_text("VALUE = 2\n")

    assert_selected(run(project), passed=UNDER_VENDOR, deselected=["test_d.py"])


def test_a_module_gone_from_a_submodule_reaches_the_modules_importing_it(project):
    """``import vendor.pkg.util`` names nothing left under the submodule; it runs ``vendor/pkg/__init__.py``."""
    base = bump(project, lambda vendor: os.remove(vendor / "pkg/util.py"))

    assert_selected(run_branch(project, base), passed=UNDER_VENDOR, deselected=["test_d.py"])


def test_a_submodule_not_checked_out_is_said_and_the_modules_importing_it_are_reached(project):
    """actions/checkout leaves submodules out unless asked: an empty directory, whose modules cannot be analysed."""
    base = bump(project, lambda vendor: (vendor / "pkg/core.py").write_text("VALUE = 2\n"))
    git(project.path, "submodule", "deinit", "-q", "-f", "vendor")

    result = run_branch(project, base)

    assert_selected(result, passed=UNDER_VENDOR, deselected=["test_d.py"])
    result.stdout.fnmatch_lines(["*'vendor'* changed but holds no files*submodule not checked out*"])
