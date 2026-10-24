"""End-to-end tests for ``--impacted-map GLOB=PATH``: a changed data file counts as a change to PATH.

Import analysis cannot see that ``test_bench.py`` reads ``reports/*.json``. With
``--impacted-invalidate-all`` the only answer was every test; a mapping names the file
the data belongs to, and what an edit to that file would select runs.
"""

import subprocess

import pytest
from click.testing import CliRunner
from pytest import ExitCode

from pytest_impacted.cli import impacted_tests_cli

from .git_helpers import edit_file as edit


FILES = {
    "app/__init__.py": "",
    "app/loader.py": "def load():\n    return 1\n",
    "app/other.py": "def other():\n    return 2\n",
    "reports/run.json": "{}\n",
    "suite/test_bench.py": "def test_bench():\n    assert True\n",
    "suite/test_loader.py": "from app.loader import load\n\ndef test_loader():\n    assert load() == 1\n",
    "suite/test_other.py": "from app.other import other\n\ndef test_other():\n    assert other() == 2\n",
    "suite/slow/conftest.py": "import pytest\n\n@pytest.fixture\ndef data():\n    return 1\n",
    "suite/slow/test_slow.py": "def test_slow(data):\n    assert data\n",
}
INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"
EVERY = ["test_bench.py", "test_loader.py", "test_other.py", "test_slow.py"]


def run(project, *args):
    return project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


@pytest.fixture
def project(make_git_project):
    return make_git_project(FILES, INI)


def outcomes(result, passed):
    """The selection, under this branch's deselection or a release's skips."""
    unselected = len(EVERY) - len(passed)
    parsed = result.parseoutcomes()
    assert parsed.get("passed", 0) == len(passed), result.stdout.str()
    assert parsed.get("deselected", 0) + parsed.get("skipped", 0) == unselected, result.stdout.str()
    for name in passed:
        result.stdout.fnmatch_lines([f"*/{name}::* PASSED*"])


@pytest.mark.parametrize(
    ("target", "passed"),
    [
        pytest.param("suite/test_bench.py", ["test_bench.py"], id="a_test_file"),
        pytest.param("app/loader.py", ["test_loader.py"], id="a_module_its_importers"),
        pytest.param("suite/slow/conftest.py", ["test_slow.py"], id="a_conftest_the_tests_beneath"),
        pytest.param("suite/slow", ["test_slow.py"], id="a_directory_the_tests_under"),
    ],
)
def test_a_changed_file_counts_as_a_change_to_its_target(project, target, passed):
    edit(project, "reports/run.json")

    outcomes(run(project, f"--impacted-map=reports/*.json={target}"), passed)


def test_a_file_no_glob_matches_selects_nothing_more(project):
    edit(project, "reports/run.json")

    outcomes(run(project, "--impacted-map=data/*.csv=suite/test_bench.py"), [])


def test_mappings_are_repeatable_and_may_share_a_glob(project):
    edit(project, "reports/run.json")

    result = run(
        project, "--impacted-map=reports/*.json=suite/test_bench.py", "--impacted-map=reports/*.json=app/loader.py"
    )

    outcomes(result, ["test_bench.py", "test_loader.py"])


def test_the_ini_setting_maps_too(make_git_project):
    project = make_git_project(FILES, INI + "impacted_map = reports/*.json=suite/test_bench.py\n")
    edit(project, "reports/run.json")

    outcomes(run(project), ["test_bench.py"])


def test_it_says_what_it_mapped(project):
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=suite/test_bench.py")

    result.stdout.fnmatch_lines(["*Mapped file changes detected: suite/test_bench.py (reports/run.json)*"])


def test_a_target_that_is_nowhere_is_said_so(project):
    """A misspelt target would otherwise select nothing, silently."""
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=suite/test_bnech.py")

    outcomes(result, [])
    result.stdout.fnmatch_lines(["*suite/test_bnech.py*matches no file*"])


@pytest.mark.parametrize("entry", ["reports/*.json", "=suite/test_bench.py", "reports/*.json="])
def test_an_entry_without_a_glob_and_a_path_is_a_usage_error(project, entry):
    result = run(project, f"--impacted-map={entry}")

    assert result.ret == ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--impacted-map*GLOB=PATH*"])


def test_the_cli_maps_too(project):
    edit(project, "reports/run.json")

    result = CliRunner().invoke(
        impacted_tests_cli,
        [
            "--root-dir", str(project.path), "--module", "app", "--tests-dir", "suite",
            "--map", "reports/*.json=suite/test_bench.py",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str((project.path / "suite/test_bench.py").resolve())]


def test_a_target_no_walk_reaches_is_selected_as_an_edit_to_it_is(make_git_project):
    """A test file outside --impacted-tests-dir joins the graph when changed; mapped, it must too."""
    project = make_git_project({**FILES, "other/test_x.py": "def test_x():\n    assert True\n"}, INI)
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=other/test_x.py")

    result.stdout.fnmatch_lines(["*other/test_x.py::test_x PASSED*"])


def test_conftest_imports_applies_to_a_mapped_module(make_git_project):
    fixture = "import pytest\nfrom app.loader import load\n\n@pytest.fixture\ndef loaded():\n    return load()\n"
    files = {
        **FILES,
        "suite/uses/conftest.py": fixture,
        "suite/uses/test_uses.py": "def test_uses(loaded):\n    pass\n",
    }
    project = make_git_project(files, INI)
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=app/loader.py", "--impacted-conftest-imports")

    result.stdout.fnmatch_lines_random(["*test_loader.py::test_loader PASSED*", "*test_uses.py::test_uses PASSED*"])


def test_a_mapped_package_init_changes_every_module_in_its_package(make_git_project):
    files = {
        **FILES,
        "app/db/__init__.py": "",
        "app/db/query.py": "def q():\n    return 1\n",
        "suite/test_q.py": "import app.db.query\n\ndef test_q():\n    assert app.db.query.q()\n",
    }
    project = make_git_project(files, INI)
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=app/db/__init__.py")

    result.stdout.fnmatch_lines(["*test_q.py::test_q PASSED*"])


def test_a_mapped_dependency_file_selects_every_test(make_git_project):
    """The rules for non-Python files see a mapped path too: pyproject.toml impacts every test."""
    project = make_git_project({**FILES, "pyproject.toml": "[project]\nname = 'x'\n"}, INI)
    edit(project, "reports/run.json")

    outcomes(run(project, "--impacted-map=reports/*.json=pyproject.toml"), EVERY)


@pytest.mark.parametrize("path", ["/", "/abs/test_x.py"])
def test_a_path_that_is_not_relative_is_a_usage_error(project, path):
    result = run(project, f"--impacted-map=reports/*.json={path}")

    assert result.ret == ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--impacted-map*relative*"])


def test_a_directory_stands_for_its_python_files_only(make_git_project):
    """A data file kept beside the tests is not edited by the mapping: here it would select every test."""
    project = make_git_project({**FILES, "suite/slow/cases.json": "{}\n"}, INI)
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=suite/slow", "--impacted-invalidate-all=suite/*/*.json")

    outcomes(result, ["test_slow.py"])


@pytest.mark.parametrize("path", ["../outside/test_x.py", "suite/../../x.py"])
def test_a_path_leaving_the_rootdir_is_a_usage_error(project, path):
    result = run(project, f"--impacted-map=reports/*.json={path}")

    assert result.ret == ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--impacted-map*rootdir*"])


def test_a_path_is_normalised(project):
    """``./app/../suite/test_bench.py`` is ``suite/test_bench.py``: one file, under one spelling."""
    edit(project, "reports/run.json")

    outcomes(run(project, "--impacted-map=reports/*.json=./app/../suite/test_bench.py"), ["test_bench.py"])


def test_a_path_spelled_in_another_case_is_said_to_match_nothing(project):
    """macOS and Windows find ``Suite/test_bench.py``; the graph, keyed by its spelling, does not."""
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=Suite/test_bench.py")

    outcomes(result, [])
    result.stdout.fnmatch_lines(["*Suite/test_bench.py*matches no file*"])


def test_a_directory_holding_no_module_is_said_so(make_git_project):
    project = make_git_project({**FILES, "suite/data/cases.json": "{}\n"}, INI)
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=suite/data")

    outcomes(result, [])
    result.stdout.fnmatch_lines(["*suite/data*matches no file*"])


def test_a_directory_stands_for_the_files_git_knows_there(make_git_project):
    """Not every Python file under it: a virtualenv git ignores is no part of the project."""
    venv = {".venv/lib/site-packages/lib.py": "import pytest\npytest_plugins = ['x']\n", ".gitignore": ".venv/\n"}
    project = make_git_project({**FILES, **{f"suite/slow/{k}": v for k, v in venv.items()}}, INI)
    edit(project, "reports/run.json")

    # Counted as changed, the virtualenv's file would match this glob and select every test.
    result = run(project, "--impacted-map=reports/*.json=suite/slow", "--impacted-invalidate-all=*/site-packages/*.py")

    outcomes(result, ["test_slow.py"])


def test_mappings_chain_as_edits_would(project):
    """A mapped path another mapping's glob matches is mapped on, as an edit to it would be."""
    edit(project, "reports/run.json")

    result = run(
        project, "--impacted-map=reports/*.json=app/__init__.py", "--impacted-map=app/__init__.py=suite/test_bench.py"
    )

    result.stdout.fnmatch_lines(["*test_bench.py::test_bench PASSED*"])


def test_the_notice_groups_by_path(make_git_project):
    files = {**FILES, **{f"reports/r{i}.json": "{}\n" for i in range(6)}}
    project = make_git_project(files, INI)
    for i in range(6):
        edit(project, f"reports/r{i}.json")

    result = run(project, "--impacted-map=reports/*.json=suite/test_bench.py")

    result.stdout.fnmatch_lines(
        ["*Mapped file changes detected: suite/test_bench.py (6 files: reports/r0.json, *, and 3 more)*"]
    )


def test_a_directory_no_walk_reaches_is_selected_as_edits_to_it_are(make_git_project):
    project = make_git_project({**FILES, "other/test_x.py": "def test_x():\n    assert True\n"}, INI)
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=other")

    result.stdout.fnmatch_lines(["*other/test_x.py::test_x PASSED*"])


def test_a_directory_s_files_chain_on(project):
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=app", "--impacted-map=app/other.py=suite/test_bench.py")

    result.stdout.fnmatch_lines_random(["*test_bench.py::test_bench PASSED*", "*test_other.py::test_other PASSED*"])


def test_an_entry_with_two_separators_is_a_usage_error(project):
    result = run(project, "--impacted-map=a=b=suite/test_bench.py")

    assert result.ret == ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--impacted-map*one '='*"])


def test_an_untracked_file_in_a_mapped_directory_counts(project):
    """git knows a new file before it is added. In branch mode, where it is no change of its
    own, only the mapping brings it in."""

    def git(*args):
        return subprocess.run(["git", *args], cwd=project.path, check=True, capture_output=True, text=True).stdout

    base = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    git("checkout", "-q", "-b", "feature")
    edit(project, "reports/run.json")
    git("commit", "-qam", "data")
    (project.path / "suite/fresh").mkdir()  # no conftest, whose own mapping would select it anyway
    (project.path / "suite/fresh/test_new.py").write_text("def test_new():\n    assert True\n")

    result = project.runpytest(
        "--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=branch", f"--impacted-base-branch={base}", "-v",
        "--impacted-map=reports/*.json=suite/fresh",
    )  # fmt: skip

    result.stdout.fnmatch_lines(["*test_new.py::test_new PASSED*"])


def test_the_cli_reads_the_mapping_from_the_configuration(make_git_project):
    project = make_git_project(FILES, INI + "impacted_map = reports/*.json=suite/test_bench.py\n")
    edit(project, "reports/run.json")

    result = CliRunner().invoke(impacted_tests_cli, ["--root-dir", str(project.path)])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str((project.path / "suite/test_bench.py").resolve())]


def test_a_mapped_symlink_stands_for_itself_as_an_edit_of_it_would(make_git_project, tmp_path):
    """git tracks the link, and an edit to it is reported under the link's path."""
    project = make_git_project({**FILES, "shared/real_test.py": "def test_real():\n    assert True\n"}, INI)
    (project.path / "suite/test_link.py").symlink_to("../shared/real_test.py")
    subprocess.run(["git", "add", "suite/test_link.py"], cwd=project.path, check=True)
    subprocess.run(["git", "commit", "-qm", "link"], cwd=project.path, check=True)
    edit(project, "reports/run.json")

    result = run(project, "--impacted-map=reports/*.json=suite/test_link.py")

    result.stdout.fnmatch_lines(["*test_link.py::test_real PASSED*"])


@pytest.mark.parametrize("glob", [".", "./"])
def test_a_glob_that_matches_nothing_by_construction_is_a_usage_error(project, glob):
    result = run(project, f"--impacted-map={glob}=suite/test_bench.py")

    assert result.ret == ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--impacted-map*GLOB*"])


def test_a_tracked_file_deleted_from_the_worktree_still_counts(project):
    """git still knows it, and a deleted module still reaches whatever imports it. In branch
    mode, where the local deletion is no change of its own, only the mapping brings it in."""

    def git(*args):
        return subprocess.run(["git", *args], cwd=project.path, check=True, capture_output=True, text=True).stdout

    base = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    git("checkout", "-q", "-b", "feature")
    edit(project, "reports/run.json")
    git("commit", "-qam", "data")
    (project.path / "app/loader.py").unlink()

    result = project.runpytest(
        "--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=branch", f"--impacted-base-branch={base}", "-v",
        "--impacted-map=reports/*.json=app/loader.py",
    )  # fmt: skip

    result.stdout.fnmatch_lines(["*ERROR collecting suite/test_loader.py*"])


def test_a_path_matching_nothing_is_warned_of_in_the_header(project):
    """Every run, not only when its glob matches a change: a misspelt path is a configuration error."""
    result = run(project, "--impacted-map=reports/*.json=suite/test_Bench.py")

    result.stdout.fnmatch_lines(["*WARNING*--impacted-map path suite/test_Bench.py matches no file git knows*"])


def test_mapped_files_come_in_a_stable_order(project):
    edit(project, "reports/run.json")
    result = [run(project, "--impacted-map=reports/*.json=suite").stdout.str() for _ in range(2)]

    notices = [next(line for line in out.splitlines() if line.startswith("Mapped file")) for out in result]
    assert notices[0] == notices[1]
