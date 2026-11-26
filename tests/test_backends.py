"""The backend layer (``pytest_impacted.backends``): one interface, the choice of backend, and the fallback rule."""

import pytest

from pytest_impacted import _rust, backends, graph
from pytest_impacted.backends import PYTHON, RUST, Source, WithFallback, backend_for
from pytest_impacted.dynamic_imports import DynamicImports

from .git_helpers import INI, assert_selected, edit_file, run, write_files


class Recording:
    """A backend that reads the files whose name it is given, and leaves the others out (``None``)."""

    name = "recording"

    def __init__(self, reads=None):
        self.reads, self.asked = reads, []

    def imports(self, sources, *, find_type_only):
        self.asked.append([s.name for s in sources])
        return [([f"read.{s.name}"], []) if self.reads is None or s.name in self.reads else None for s in sources]

    def imports_by_name(self, sources):
        self.asked.append([s.name for s in sources])
        return [
            DynamicImports([s.name], [], []) if self.reads is None or s.name in self.reads else None for s in sources
        ]


SOURCES = [Source(f"/{name}.py", name, False) for name in ("a", "b", "c")]


def test_the_fallback_reads_only_what_the_first_backend_leaves_out_in_order():
    first, rest = Recording(reads={"b"}), Recording()

    found = WithFallback(first, rest).imports(SOURCES, find_type_only=False)

    assert found == [(["read.a"], []), (["read.b"], []), (["read.c"], [])]
    assert first.asked == [["a", "b", "c"]]
    assert rest.asked == [["a", "c"]]


def test_the_fallback_is_not_asked_when_nothing_is_left():
    rest = Recording()

    WithFallback(Recording(), rest).imports_by_name(SOURCES)

    assert rest.asked == []


def test_a_fallback_is_named_as_its_first_backend():
    assert WithFallback(RUST, PYTHON).name == RUST.name


@pytest.mark.parametrize(
    ("available", "splits", "ignore", "requested", "expected"),
    [
        pytest.param(True, True, False, "auto", RUST.name, id="the extension, Python behind it"),
        pytest.param(True, True, True, "auto", RUST.name, id="new enough for the type-only option"),
        pytest.param(True, False, True, "auto", PYTHON.name, id="too old for the type-only option"),
        pytest.param(False, False, False, "auto", PYTHON.name, id="no extension"),
        pytest.param(True, True, False, "python", PYTHON.name, id="python asked for"),
    ],
)
def test_backend_for(monkeypatch, available, splits, ignore, requested, expected):
    monkeypatch.setattr(_rust, "RUST_AVAILABLE", available)
    monkeypatch.setattr(_rust, "RUST_SPLITS_TYPE_ONLY", splits)

    chosen = backend_for(ignore, requested)

    assert chosen.name == expected
    assert isinstance(chosen, WithFallback) is (expected == RUST.name)


def test_backend_for_refuses_what_is_no_backend():
    with pytest.raises(ValueError, match="auto, python"):
        backend_for(False, "rust")


def test_the_python_backend_reads_every_file():
    found = PYTHON.imports([Source("/no/such/file.py", "app.gone", False)], find_type_only=True)

    assert found == [([], [])]  # unreadable: no imports, logged, and still an answer, never left out


def test_asking_for_the_python_backend_keeps_the_extension_out(tmp_path, monkeypatch):
    """``--impacted-backend=python``: not one file reaches the extension, and the graph is the one it builds."""
    if not _rust.RUST_AVAILABLE:
        pytest.skip("pytest-impacted-rs is not installed")
    for function in ("rust_parse_imports_each", "rust_parse_all_imports", "rust_parse_all_dynamic_imports"):
        monkeypatch.setattr(_rust, function, lambda *args: pytest.fail("the extension was asked"))
    files = {"app/__init__.py": "", "app/a.py": "import importlib\nimportlib.import_module('app.b')\n", "app/b.py": ""}
    write_files(tmp_path, {**files, "tests/test_a.py": "import app.a\n"})

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path, backend="python")

    assert dep_tree.has_edge("app.a", "tests.test_a")
    assert dep_tree.has_edge("app.b", "app.a")  # an import by name, found by the Python backend


@pytest.mark.skipif(not _rust.RUST_PARSES_EACH, reason="pytest-impacted-rs is not installed, or predates 0.41")
def test_both_backends_read_a_project_alike(tmp_path):
    files = {
        "app/__init__.py": "from .core import thing\n",
        "app/core.py": "import os\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.models import M\n"
        "def f(m: 'M'): ...\n",
        "app/models.py": "import importlib\nimportlib.import_module('app.core')\n",
    }
    write_files(tmp_path, files)
    sources = [
        Source(str(tmp_path / rel), rel[:-3].replace("/", ".").removesuffix(".__init__"), rel.endswith("__init__.py"))
        for rel in files
    ]

    for find_type_only in (False, True):
        assert RUST.imports(sources, find_type_only=find_type_only) == PYTHON.imports(
            sources, find_type_only=find_type_only
        )
    assert RUST.imports_by_name(sources) == PYTHON.imports_by_name(sources)


def test_the_choices_are_the_options():
    """``--impacted-backend`` offers what ``backend_for`` accepts: one tuple, in ``options``."""
    from pytest_impacted.options import OPTIONS  # noqa: PLC0415

    assert next(option for option in OPTIONS if option.setting == "impacted_backend").choices == backends.CHOICES


@pytest.mark.parametrize("where", ["flag", "ini"])
def test_the_option_reaches_the_analysis(make_git_project, monkeypatch, where):
    """End to end: with ``impacted_backend = python`` no file reaches the extension, and the selection stands."""
    if not _rust.RUST_AVAILABLE:
        pytest.skip("pytest-impacted-rs is not installed")
    for function in ("rust_parse_imports_each", "rust_parse_all_imports", "rust_parse_all_dynamic_imports"):
        monkeypatch.setattr(_rust, function, lambda *args: (_ for _ in ()).throw(AssertionError("the extension")))
    project = make_git_project(FILES, INI + ("impacted_backend = python\n" if where == "ini" else ""))
    edit_file(project, "app/a.py")

    result = run(project, *(["--impacted-backend=python"] if where == "flag" else []))

    assert_selected(result, passed=["test_a.py"], deselected=["test_b.py"])
    result.stdout.fnmatch_lines(["*backend=python (ast)*"])


FILES = {
    "app/__init__.py": "",
    "app/a.py": "def a():\n    return 1\n",
    "app/b.py": "def b():\n    return 2\n",
    "suite/test_a.py": "from app.a import a\n\ndef test_a():\n    assert a() == 1\n",
    "suite/test_b.py": "from app.b import b\n\ndef test_b():\n    assert b() == 2\n",
}


def test_the_rust_backend_leaves_every_file_out_without_an_extension_that_can(monkeypatch):
    """Used directly, it answers ``None`` for each file rather than calling a function the install lacks."""
    for flag in ("RUST_AVAILABLE", "RUST_SPLITS_TYPE_ONLY", "RUST_PARSES_EACH", "RUST_PARSES_DYNAMIC_IMPORTS"):
        monkeypatch.setattr(_rust, flag, False)
    for function in ("rust_parse_all_imports", "rust_parse_all_imports_split", "rust_parse_imports_each"):
        monkeypatch.setattr(_rust, function, None)  # as _rust leaves them when the extension is not installed

    assert RUST.imports(SOURCES, find_type_only=False) == [None, None, None]
    assert RUST.imports_by_name(SOURCES) == [None, None, None]


def test_a_backend_of_the_caller_s_has_python_behind_it(tmp_path):
    """A backend may leave a file out (``None``); handed to ``build_dep_tree``, the file is still read."""
    write_files(
        tmp_path,
        {"app/__init__.py": "", "app/a.py": "import app.b\n", "app/b.py": "", "tests/test_a.py": "import app.a\n"},
    )
    leaves_all_out = Recording(reads=set())

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path, backend=leaves_all_out)

    assert leaves_all_out.asked
    assert dep_tree.has_edge("app.b", "app.a")
    assert dep_tree.has_edge("app.a", "tests.test_a")


def test_none_asks_for_the_default_and_an_unknown_name_fails_before_git(tmp_path, monkeypatch):
    from pytest_impacted import api  # noqa: PLC0415

    assert backends.resolve_backend(None).name == backend_for().name
    asked_git = []
    monkeypatch.setattr(api, "find_impacted_files_in_repo", lambda *a, **k: asked_git.append(a) or [])
    with pytest.raises(ValueError, match="auto, python"):
        api.get_impacted_tests(
            impacted_git_mode="unstaged",
            impacted_base_branch="main",
            root_dir=tmp_path,
            ns_module="app",
            backend="ruff",
        )
    assert asked_git == []


def test_an_empty_backend_setting_is_the_default(make_git_project):
    """As the CLI reads it: ``impacted_backend =`` with no value is ``auto``, not a usage error."""
    project = make_git_project(FILES, INI + "impacted_backend =\n")
    edit_file(project, "app/a.py")

    assert_selected(run(project), passed=["test_a.py"], deselected=["test_b.py"])


@pytest.mark.parametrize(("requested", "warned"), [("auto", True), ("python", False)])
def test_asking_for_python_says_nothing_of_an_outdated_extension(pytester, monkeypatch, requested, warned):
    """The extension is not used then: there is nothing to upgrade."""
    from pytest_impacted.plugin import _header_warnings  # noqa: PLC0415

    monkeypatch.setattr(_rust, "outdated_for", lambda ignore: "the installed pytest-impacted-rs is old")
    pytester.makeini("[pytest]\nimpacted_module = app\n")
    config = pytester.parseconfig("--impacted", f"--impacted-backend={requested}")

    assert any("is old" in line for line in _header_warnings(config)) is warned


def test_an_empty_choice_setting_is_its_default_in_the_header_too(pytester):
    """The header names what the run uses: ``impacted_backend =`` reads ``auto``, not an empty value."""
    from pytest_impacted.plugin import pytest_report_header  # noqa: PLC0415

    pytester.makeini("[pytest]\nimpacted_module = app\nimpacted_backend =\nimpacted_git_mode =\n")
    header = pytest_report_header(pytester.parseconfig("--impacted"))[0]

    assert "impacted_backend=auto" in header and "impacted_git_mode=unstaged" in header


def test_a_backend_of_the_caller_s_need_not_name_itself():
    class Nameless:
        imports = Recording.imports
        imports_by_name = Recording.imports_by_name

    assert WithFallback(Nameless(), PYTHON).name == "Nameless"
    assert backends.resolve_backend(PYTHON) is PYTHON
