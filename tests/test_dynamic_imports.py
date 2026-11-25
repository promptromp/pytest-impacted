"""Literal dynamic imports are dependencies without executing application code."""

import pytest

from pytest_impacted import _rust
from pytest_impacted.parsing import parse_file_imports

from .git_helpers import assert_selected, edit_file


@pytest.mark.parametrize(
    "loader",
    [
        "import importlib\ndef load():\n    return importlib.import_module('app.target').VALUE\n",
        "import importlib as lib\ndef load():\n    return lib.import_module('app.target').VALUE\n",
        (
            "from importlib import import_module as load_module\n"
            "def load():\n    return load_module('.target', package='app').VALUE\n"
        ),
        "def load():\n    return __import__('app.target', fromlist=['VALUE']).VALUE\n",
    ],
)
@pytest.mark.parametrize("broken", [False, True])
def test_target_change_selects_a_static_consumer_of_a_dynamic_loader(make_git_project, loader, broken):
    project = make_git_project(
        {
            "app/__init__.py": "",
            "app/target.py": "VALUE = 1\n",
            "app/loader.py": loader,
            "suite/test_consumer.py": "from app.loader import load\ndef test_value():\n    assert load() == 1\n",
            "suite/test_other.py": "def test_other():\n    assert True\n",
        },
        "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n",
    )
    if broken:
        (project.path / "app/target.py").write_text("VALUE = 2\n")
    else:
        edit_file(project, "app/target.py")

    result = project.runpytest("--impacted", "--impacted-git-mode=unstaged", "-v", "-p", "no:cacheprovider")

    if broken:
        result.assert_outcomes(failed=1, deselected=1)
        result.stdout.fnmatch_lines(["*test_consumer.py::test_value FAILED*"])
    else:
        assert_selected(result, passed=["test_consumer.py"], deselected=["test_other.py"])


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import importlib\nvalue = importlib.import_module('app.target')", "app.target"),
        ("import importlib as lib\nvalue = lib.import_module('app.target')", "app.target"),
        ("from importlib import import_module as load\nvalue = load('app.target')", "app.target"),
        ("from importlib import import_module\nvalue = import_module('.target', 'app')", "app.target"),
        ("from importlib import import_module\nvalue = import_module('..target', package='app.child')", "app.target"),
        ("from importlib import import_module\nvalue = import_module('.target', __package__)", "app.target"),
        ("value = __import__('app.target')", "app.target"),
        ("value = __import__('app.target', level=0)", "app.target"),
        ("value = __import__('app.target', level=False)", "app.target"),
        (
            (
                "from importlib import import_module as load\n"
                "def first():\n    return load('app.target')\ndef other():\n    from other import load"
            ),
            "app.target",
        ),
        (
            "raise AssertionError('must not execute')\nimport importlib\nvalue = importlib.import_module('app.target')",
            "app.target",
        ),
        ("import builtins as b\nvalue = b.__import__('app.target')", "app.target"),
        ("from builtins import __import__ as load\nvalue = load('app.target')", "app.target"),
        ("value = __import__('app', fromlist=['target'])", "app.target"),
    ],
)
@pytest.mark.parametrize("backend", ["python", "rust"])
def test_literal_dynamic_import_candidates(tmp_path, source, expected, backend):
    path = tmp_path / "loader.py"
    path.write_text(source)

    parse = parse_file_imports if backend == "python" else pytest.importorskip("pytest_impacted_rs").parse_file_imports
    result = parse(str(path), "app.loader", False)
    assert expected in result
    assert result == parse_file_imports(str(path), "app.loader")


def test_older_extension_uses_python_for_dynamic_imports(monkeypatch):
    monkeypatch.setattr(_rust, "RUST_AVAILABLE", True)
    monkeypatch.setattr(_rust, "RUST_LITERAL_DYNAMIC_IMPORTS", False)

    assert not _rust.uses_rust()
    assert "literal dynamic imports" in _rust.outdated_for(False)


@pytest.mark.parametrize("backend", ["python", "rust"])
@pytest.mark.parametrize(
    "source",
    [
        "from importlib import import_module\nvalue = import_module(target)",
        "from importlib import import_module\nvalue = import_module('..target', 'app')",
        "value = __import__('target', level=1)",
        "value = __import__('target', level=0.0)",
    ],
)
def test_unresolved_calls_do_not_invent_absolute_candidates(tmp_path, backend, source):
    path = tmp_path / "loader.py"
    path.write_text(source)
    parse = parse_file_imports if backend == "python" else pytest.importorskip("pytest_impacted_rs").parse_file_imports

    result = parse(str(path), "app.loader", False)

    assert "target" not in result
    assert "app.target" not in result
    assert result == parse_file_imports(str(path), "app.loader")
