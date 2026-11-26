"""Several pytest sessions in one process (``pytest.main`` called again: an IDE runner, a watch tool, pytester): each
analyses the project as it is then, not as the first found it."""

import os
import subprocess
from pathlib import Path

from pytest_impacted.api import get_impacted_tests

from .git_helpers import INI, assert_selected, edit_file, run


FILES = {
    "app/__init__.py": "",
    "app/a.py": "def a():\n    return 1\n",
    "app/b.py": "def b():\n    return 2\n",
    "suite/test_a.py": "from app.a import a\n\ndef test_a():\n    assert a() == 1\n",
    "suite/test_b.py": "from app.b import b\n\ndef test_b():\n    assert b() == 2\n",
}


def commit(project, message):
    subprocess.run(["git", "commit", "-qam", message], cwd=project.path, check=True, capture_output=True)


def test_a_second_session_sees_an_import_committed_after_the_first(make_git_project):
    project = make_git_project(FILES, INI)
    edit_file(project, "app/a.py")
    assert_selected(run(project), passed=["test_a.py"], deselected=["test_b.py"])

    # test_b now imports app.a too, committed: no change of its own, so only the graph can link it.
    (project.path / "app/a.py").write_text(FILES["app/a.py"])
    (project.path / "suite/test_b.py").write_text(
        "from app.a import a\nfrom app.b import b\n\ndef test_b():\n    assert a() + b() == 3\n"
    )
    commit(project, "test_b uses a")
    edit_file(project, "app/a.py")

    assert_selected(run(project), passed=["test_a.py", "test_b.py"])


def test_a_second_session_sees_a_module_added_after_the_first(make_git_project):
    project = make_git_project(FILES, INI)
    edit_file(project, "app/a.py")
    assert_selected(run(project), passed=["test_a.py"], deselected=["test_b.py"])

    # A new module, imported by test_b, all committed: the second build must link it.
    (project.path / "app/a.py").write_text(FILES["app/a.py"])
    (project.path / "app/c.py").write_text("def c():\n    return 3\n")
    (project.path / "suite/test_b.py").write_text(
        "from app.b import b\nfrom app.c import c\n\ndef test_b():\n    assert b() + c() == 5\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=project.path, check=True, capture_output=True)
    commit(project, "add c")
    edit_file(project, "app/c.py")

    assert_selected(run(project), passed=["test_b.py"], deselected=["test_a.py"])


NEW_TEST = "from app.{module} import {module}\n\ndef test_c():\n    assert {module}()\n"


def add_test_c(project, module):
    (project.path / "app/a.py").write_text(FILES["app/a.py"])
    (project.path / "suite/test_c.py").write_text(NEW_TEST.format(module=module))
    subprocess.run(["git", "add", "-A"], cwd=project.path, check=True, capture_output=True)
    commit(project, "add test_c")
    edit_file(project, "app/a.py")


def test_a_test_module_added_after_the_first_session_is_judged(make_git_project):
    """Known to the second session's discovery, so deselected when nothing it imports changed (unknown, it would run
    unjudged)."""
    project = make_git_project(FILES, INI)
    edit_file(project, "app/a.py")
    assert_selected(run(project), passed=["test_a.py"], deselected=["test_b.py"])

    add_test_c(project, "b")

    assert_selected(run(project), passed=["test_a.py"], deselected=["test_b.py", "test_c.py"])


def test_a_second_analysis_sees_a_test_module_added_after_the_first(make_git_project):
    """Called directly, as a watch tool does, where no collection runs a test file the analysis does not know."""
    project = make_git_project(FILES, INI)
    root = Path(os.path.realpath(project.path))

    def analyse():
        found = get_impacted_tests("unstaged", "main", project.path, "app", "suite") or []
        return sorted(Path(test).relative_to(root).as_posix() for test in found)

    edit_file(project, "app/a.py")
    assert analyse() == ["suite/test_a.py"]

    add_test_c(project, "a")

    assert analyse() == ["suite/test_a.py", "suite/test_c.py"]
