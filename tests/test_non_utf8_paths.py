"""A file whose name is not UTF-8 never reaches the Rust extension, whose strings are: the Python backend reads it.

On Linux such a name (a Latin-1 ``café.py``) reaches Python with a surrogate escape (``caf\\udce9.py``), and
PyO3 raises UnicodeEncodeError on it, which failed the whole run. macOS refuses such names, so the end-to-end
case runs where the filesystem allows them.
"""

import logging
import os

import pytest

from pytest_impacted import _rust, backends, graph
from pytest_impacted.dynamic_imports import DynamicImports, parse_dynamic_imports

from .git_helpers import INI, assert_selected, edit_file


ODD = "caf\udce9"  # how Python spells the file name b"caf\xe9" on Linux


@pytest.mark.parametrize(
    ("texts", "taken"),
    [
        (("app/models.py", "app.models"), True),
        (("app/café.py", "app.café"), True),  # non-ASCII, but UTF-8
        ((f"app/{ODD}.py", "app.models"), False),
        (("app/models.py", f"app.{ODD}"), False),
        ((), True),
    ],
    ids=["ASCII", "UTF-8", "an odd path", "an odd name", "nothing"],
)
def test_the_extension_takes_only_utf8(texts, taken):
    assert _rust.takes(*texts) is taken


def pyo3_like(parse):
    """A stand-in for an extension function, failing as PyO3 does on a string that is not UTF-8."""

    def call(modules, *rest):
        for module in modules:
            for text in module:
                if isinstance(text, str):
                    text.encode("utf-8")  # UnicodeEncodeError, as PyO3's String extraction raises
        call.handed.extend(module[1] for module in modules)
        return parse(modules, *rest)

    call.handed = []
    return call


@pytest.fixture
def files(tmp_path, caplog):
    """``{module name: path}``: a module importing ``os``, and one whose file name is not UTF-8 (never read).

    The Python backend logs that it cannot read the second; the record would carry its name, which pytest-xdist
    cannot send back from a worker (execnet takes UTF-8 alone), so it is kept out of the report.
    """
    caplog.set_level(logging.CRITICAL, logger="pytest_impacted")
    (tmp_path / "models.py").write_text("import os\n")
    return {"app.models": str(tmp_path / "models.py"), f"app.{ODD}": str(tmp_path / f"{ODD}.py")}


@pytest.mark.parametrize("each", [False, True], ids=["dict functions", "parse_imports_each"])
@pytest.mark.parametrize("ignore_type_checking_imports", [False, True])
def test_import_parsing_leaves_an_odd_file_to_python(monkeypatch, files, ignore_type_checking_imports, each):
    def by_python(modules):
        found = backends.PYTHON.imports([backends.Source(*module) for module in modules], find_type_only=True)
        return {name: tuple(split) for (_, name, _), split in zip(modules, found, strict=True)}

    plain = pyo3_like(lambda modules: {name: found for name, (found, _) in by_python(modules).items()})
    split = pyo3_like(by_python)
    in_order = pyo3_like(lambda modules, find_type_only: list(by_python(modules).values()))
    monkeypatch.setattr(_rust, "RUST_AVAILABLE", True)
    monkeypatch.setattr(_rust, "RUST_SPLITS_TYPE_ONLY", True)
    monkeypatch.setattr(_rust, "RUST_PARSES_EACH", each)
    monkeypatch.setattr(_rust, "rust_parse_all_imports", plain)
    monkeypatch.setattr(_rust, "rust_parse_all_imports_split", split)
    monkeypatch.setattr(_rust, "rust_parse_imports_each", in_order)

    runtime, _ = graph._parse_all_module_imports(
        files, backends.backend_for(ignore_type_checking_imports), ignore_type_checking_imports
    )

    assert runtime == {"app.models": ["os"], f"app.{ODD}": []}
    called = in_order if each else split if ignore_type_checking_imports else plain
    assert called.handed == ["app.models"]


def test_finding_imports_by_name_leaves_an_odd_file_to_python(monkeypatch, files):
    def by_python(modules, table):
        return [tuple(parse_dynamic_imports(*module)) for module in modules]

    parse = pyo3_like(by_python)
    monkeypatch.setattr(_rust, "RUST_PARSES_DYNAMIC_IMPORTS", True)
    monkeypatch.setattr(_rust, "rust_parse_all_dynamic_imports", parse)

    found = graph._parse_all_dynamic_imports(files, backends.backend_for())

    assert found == {"app.models": DynamicImports([], [], []), f"app.{ODD}": DynamicImports([], [], [])}
    assert parse.handed == ["app.models"]


def test_the_real_extension_is_never_handed_an_odd_file(monkeypatch, files):
    """The same with the extension itself, whose PyO3 conversion is what raised: it parses the other file."""
    if not (_rust.RUST_AVAILABLE and _rust.RUST_SPLITS_TYPE_ONLY and _rust.RUST_PARSES_DYNAMIC_IMPORTS):
        pytest.skip("pytest-impacted-rs is not installed, or predates finding imports by name")
    each = ("rust_parse_imports_each",) if _rust.RUST_PARSES_EACH else ()
    with pytest.raises(UnicodeEncodeError):
        _rust.rust_parse_all_imports([(files[f"app.{ODD}"], f"app.{ODD}", False)])  # what failed the run
    handed: dict[str, list[str]] = {}

    def recording(function):
        real = getattr(_rust, function)

        def call(modules, *rest):
            handed.setdefault(function, []).extend(name for _, name, _ in modules)
            return real(modules, *rest)

        return call

    functions = ("rust_parse_all_imports", "rust_parse_all_imports_split", "rust_parse_all_dynamic_imports", *each)
    for function in functions:
        monkeypatch.setattr(_rust, function, recording(function))

    for ignore in (False, True):
        assert graph._parse_all_module_imports(files, backends.backend_for(ignore), ignore)[0]["app.models"] == ["os"]
    assert set(graph._parse_all_dynamic_imports(files, backends.backend_for())) == set(files)

    # The import pass goes through parse_imports_each when the extension has it, once per option.
    imports = (
        {"rust_parse_imports_each": ["app.models"] * 2}
        if each
        else {
            "rust_parse_all_imports": ["app.models"],
            "rust_parse_all_imports_split": ["app.models"],
        }
    )
    assert handed == {**imports, "rust_parse_all_dynamic_imports": ["app.models"]}


@pytest.mark.parametrize("args", [(), ("--impacted-ignore-type-checking-imports",)], ids=["", "type-only ignored"])
def test_a_package_holding_a_file_named_in_latin_1_runs(pytester, make_git_project, args):
    """The whole run, on a filesystem that takes such a name: no failure, the file analysed, the selection right."""
    (pytester.path / "app").mkdir()
    try:
        fd = os.open(os.fsencode(pytester.path / "app") + b"/caf\xe9.py", os.O_CREAT | os.O_WRONLY)
    except (OSError, UnicodeDecodeError) as error:  # macOS: Illegal byte sequence; Windows decodes bytes as UTF-8
        pytest.skip(f"the filesystem refuses a file name that is not UTF-8: {error}")
    os.write(fd, b"import importlib\nimportlib.import_module('app.target')\n")
    os.close(fd)
    project = make_git_project(
        {
            "app/__init__.py": "",
            "app/target.py": "VALUE = 1\n",
            "suite/test_target.py": "from app.target import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
            "suite/test_other.py": "def test_other():\n    pass\n",
        },
        INI,
    )
    edit_file(project, "app/target.py")

    result = project.runpytest("--impacted", "--impacted-git-mode=unstaged", "-v", "-p", "no:cacheprovider", *args)

    assert_selected(result, passed=["test_target.py"], deselected=["test_other.py"])
    dep_tree = graph.build_dep_tree("app", tests_package="suite", root_dir=pytester.path)
    assert dep_tree.has_edge("app.target", f"app.{ODD}")  # it is analysed: its import by name is an edge
