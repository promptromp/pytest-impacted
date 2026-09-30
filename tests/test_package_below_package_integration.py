"""End-to-end tests for a package below a directory that is itself a package, via pytester and a real git repo.

A src-layout project that keeps an ``__init__.py`` in ``src/`` still puts ``src/`` on
``sys.path`` (an editable install, or ``pythonpath = src``) and imports ``app.core``; from the
rootdir the same file is ``src.app.core``. Either spelling must reach it.
"""

import pytest
from click.testing import CliRunner

from pytest_impacted.cli import impacted_tests_cli

from .git_helpers import edit_file


def files(module: str) -> dict[str, str]:
    """The project of the bug report: a test importing the edited module, one reaching it through another."""
    return {
        "src/__init__.py": "",
        "src/app/__init__.py": "",
        "src/app/models.py": "VALUE = 1\n",
        "src/app/service.py": f"from {module}.models import VALUE\n",
        "suite/test_models.py": f"from {module}.models import VALUE\n\ndef test_models():\n    assert VALUE\n",
        "suite/test_service.py": f"from {module}.service import VALUE\n\ndef test_service():\n    assert VALUE\n",
        "suite/test_other.py": "def test_other():\n    assert True\n",
    }


def ini(module: str) -> str:
    pythonpath = "src" if module == "app" else "."
    return f"[pytest]\npythonpath = {pythonpath}\nimpacted_module = src/app\nimpacted_tests_dir = suite\n"


def run(project, *args):
    return project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


IMPORTED_AS = pytest.mark.parametrize("module", ["app", "src.app"], ids=["src_on_sys_path", "the_rootdir_on_sys_path"])


@IMPORTED_AS
def test_an_edit_selects_the_tests_importing_the_module_directly_or_not(make_git_project, module):
    project = make_git_project(files(module), ini(module))
    edit_file(project, "src/app/models.py")

    result = run(project)

    result.assert_outcomes(passed=2, skipped=1)
    result.stdout.fnmatch_lines_random(
        ["*test_models.py::test_models PASSED*", "*test_service.py::test_service PASSED*"]
    )


@IMPORTED_AS
def test_the_cli_prints_the_same_tests(make_git_project, monkeypatch, tmp_path, module):
    project = make_git_project(files(module), "[pytest]\n")
    edit_file(project, "src/app/models.py")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        impacted_tests_cli, ["--root-dir", str(project.path), "--module", "src/app", "--tests-dir", "suite"]
    )

    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.splitlines()) == [
        str((project.path / "suite" / name).resolve()) for name in ("test_models.py", "test_service.py")
    ]


def test_an_edited_init_selects_the_tests_importing_anything_inside_the_package(make_git_project):
    project = make_git_project(files("app"), ini("app"))
    edit_file(project, "src/app/__init__.py")

    result = run(project)

    result.assert_outcomes(passed=2, skipped=1)
    result.stdout.fnmatch_lines(["*test_other.py::test_other SKIPPED*"])


def test_a_deleted_module_selects_the_tests_importing_it(make_git_project):
    project = make_git_project(
        {
            **files("app"),
            "src/app/legacy.py": "OLD = 1\n",
            "suite/test_legacy.py": "def test_legacy():\n    try:\n        from app.legacy import OLD\n"
            "    except ImportError:\n        OLD = 0\n    assert OLD == 0\n",
        },
        ini("app"),
    )
    (project.path / "src/app/legacy.py").unlink()

    result = run(project)

    result.assert_outcomes(passed=1, skipped=3)
    result.stdout.fnmatch_lines(["*test_legacy.py::test_legacy PASSED*"])


def test_a_dash_p_plugin_named_from_the_directory_on_sys_path_is_session_wide(make_git_project):
    """``-p app.plugin`` names ``src/app/plugin.py`` by its alias; editing it selects every test."""
    project = make_git_project(
        {
            **files("app"),
            "src/app/plugin.py": "import pytest\n\n@pytest.fixture\ndef answer():\n    return 42\n",
        },
        ini("app") + "addopts = -p app.plugin\n",
    )
    edit_file(project, "src/app/plugin.py")

    result = run(project)

    result.assert_outcomes(passed=3)


def test_a_module_beside_the_package_is_followed_from_the_directory_on_sys_path(make_git_project):
    """``src/settings.py`` is outside ``--impacted-module``; ``import settings`` finds it under ``src/``."""
    project = make_git_project(
        {
            **files("app"),
            "src/settings.py": "DEBUG = False\n",
            "src/app/service.py": "import settings\nfrom app.models import VALUE\n",
        },
        ini("app"),
    )
    edit_file(project, "src/settings.py")

    result = run(project)

    result.assert_outcomes(passed=1, skipped=2)
    result.stdout.fnmatch_lines(["*test_service.py::test_service PASSED*"])


@pytest.mark.parametrize(
    ("args", "passed"),
    [pytest.param([], 1, id="default"), pytest.param(["--impacted-conftest-imports"], 3, id="opted_in")],
)
def test_a_library_beside_the_package_that_the_application_imports_is_application_code(make_git_project, args, passed):
    """``src/shared.py`` is found now, and the app imports it: a conftest importing it too selects the
    tests beneath it only on request, as for any application code."""
    project = make_git_project(
        {
            **files("app"),
            "src/shared.py": "def helper():\n    return 1\n",
            "src/app/service.py": "import shared\nfrom app.models import VALUE\n",
            "suite/conftest.py": "import pytest\nimport shared\n\n@pytest.fixture\ndef helper():\n"
            "    return shared.helper()\n",
        },
        ini("app"),
    )
    edit_file(project, "src/shared.py")

    result = run(project, *args)

    result.assert_outcomes(passed=passed, skipped=3 - passed)
    if not args:
        result.stdout.fnmatch_lines(["*Changed application code is imported by *suite/conftest.py*"])
