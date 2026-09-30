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
    """``-p app.plugin`` is ``src/app/plugin.py`` with ``src/`` on ``sys.path``; editing it selects every test."""
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


@pytest.mark.parametrize("args", [[], ["--impacted-conftest-imports"]], ids=["default", "opted_in"])
def test_a_library_found_only_under_the_directory_above_is_followed_like_test_code(make_git_project, args):
    """``src/shared.py`` is found by guessing that ``src/`` is on ``sys.path``. The app imports it, but a
    guess never makes a module application code, whose conftest rule is opt-in: the conftest importing
    it selects the tests beneath it either way."""
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

    run(project, *args).assert_outcomes(passed=3)


def test_a_name_that_only_happens_to_match_a_file_above_the_package_places_nothing(make_git_project):
    """``import logging`` in the app is the standard library's, though ``backend/`` holds a ``logging.py``
    that imports a test helper. It is never looked up there, so the helper stays test code: the conftest
    importing it still selects every test beneath it by default."""
    project = make_git_project(
        {
            "backend/__init__.py": "",
            "backend/app/__init__.py": "",
            "backend/app/svc.py": "import logging\n\ndef run():\n    return logging.INFO\n",
            "backend/logging.py": "from testing.factories import make\n",
            "testing/factories.py": "def make():\n    return 1\n",
            "suite/conftest.py": "import pytest\nfrom testing.factories import make\n\n@pytest.fixture\n"
            "def thing():\n    return make()\n",
            "suite/test_a.py": "def test_a(thing):\n    assert thing\n",
            "suite/test_svc.py": "from backend.app.svc import run\n\ndef test_svc():\n    assert run()\n",
        },
        "[pytest]\npythonpath = .\nimpacted_module = backend/app\nimpacted_tests_dir = suite\n",
    )
    edit_file(project, "testing/factories.py")

    run(project).assert_outcomes(passed=2)


def test_deleting_a_module_the_application_imports_is_followed_through_a_conftest(make_git_project):
    """``app.gone`` resolves only with ``src/`` on ``sys.path``, a guess: the deleted module is placed as test
    code, so the conftest importing the app selects every test beneath it. More than without
    ``src/__init__.py``, where the same deletion is application code; never fewer than a sure link."""
    project = make_git_project(
        {
            **files("app"),
            "src/app/gone.py": "X = 1\n",
            "src/app/service.py": "from app.models import VALUE\n"
            "try:\n    import app.gone\nexcept ImportError:\n    pass\n",
            "suite/conftest.py": "import pytest\nfrom app import service\n",
        },
        ini("app"),
    )
    (project.path / "src/app/gone.py").unlink()

    run(project).assert_outcomes(passed=3)
