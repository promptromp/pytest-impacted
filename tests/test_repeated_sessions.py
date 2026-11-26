"""Several pytest sessions in one process (``pytest.main`` called again: an IDE runner, a watch tool, pytester): each
analyses the project as it is then, not as the first found it."""

import subprocess

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

    # A new module, imported by test_b, all committed: the walks must find it on the second build.
    (project.path / "app/a.py").write_text(FILES["app/a.py"])
    (project.path / "app/c.py").write_text("def c():\n    return 3\n")
    (project.path / "suite/test_b.py").write_text(
        "from app.b import b\nfrom app.c import c\n\ndef test_b():\n    assert b() + c() == 5\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=project.path, check=True, capture_output=True)
    commit(project, "add c")
    edit_file(project, "app/c.py")

    assert_selected(run(project), passed=["test_b.py"], deselected=["test_a.py"])
