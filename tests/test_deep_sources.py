"""Sources nested beyond what a parser's recursion takes: generated code with a long ``a + b + ...``, deep nesting.

Neither backend may crash the run — the Rust extension once killed the whole pytest process, no traceback — and
neither may find fewer imports than the other could: ruff refuses some sources CPython compiles, and astroid
gives up long before CPython does.
"""

import subprocess
import sys
import textwrap

import pytest

from pytest_impacted import _rust, graph

from .git_helpers import write_files


needs_each = pytest.mark.skipif(not _rust.RUST_PARSES_EACH, reason="pytest-impacted-rs predates parse_imports_each")


#: Names a loader too, or the imports-by-name pass's pre-filter would never parse the file.
HEADER = "import os\nimport importlib\nimportlib.import_module('app.core')\n"


def chain(terms: int, joiner: str = " + ") -> str:
    """A module importing ``os`` whose one expression is a left-deep chain of *terms*."""
    return HEADER + "x = " + joiner.join(["a"] * terms) + "\n"


def calls(terms: int) -> str:
    return HEADER + "x = f" + "()" * terms + "\n"


CRASHERS = [
    pytest.param(chain(60_000), id="a 60,000-term + chain"),
    pytest.param(chain(60_000, "."), id="a 60,000-link attribute chain"),
    pytest.param(calls(60_000), id="60,000 chained calls"),
    pytest.param(chain(12_000), id="a 12,000-term + chain"),  # what crashed the type-only walk on Linux
]

#: Each extension function the graph calls, with the arguments past the modules: none must crash the process.
FUNCTIONS = [
    ("rust_parse_all_imports", ""),
    ("rust_parse_all_imports_split", ""),
    ("rust_parse_imports_each", ", True"),
    ("rust_parse_all_dynamic_imports", ", TABLE"),
]


@pytest.mark.skipif(not _rust.RUST_PARSES_EACH, reason="pytest-impacted-rs is not installed, or predates the fix")
@pytest.mark.parametrize(("function", "rest"), FUNCTIONS, ids=[function for function, _ in FUNCTIONS])
@pytest.mark.parametrize("source", CRASHERS)
def test_no_extension_function_crashes_on_a_deep_source(tmp_path, function, rest, source):
    """In a subprocess: a stack overflow kills the process. A batch of several files runs on rayon's threads, where
    the overflow happened (a one-file batch runs on the caller's larger stack, which hid it from earlier tests)."""
    paths = []
    for index in range(4):
        (tmp_path / f"deep{index}.py").write_text(source)
        paths.append(str(tmp_path / f"deep{index}.py"))
    script = textwrap.dedent(f"""
        from pytest_impacted import _rust
        from pytest_impacted.dynamic_imports import TABLE
        modules = [(path, f"app.deep{{index}}", False) for index, path in enumerate({paths!r})]
        print(repr(_rust.{function}(modules{rest})))
    """)

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False)

    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr[-2000:]}"


@needs_each
def test_the_extension_hands_back_what_it_will_not_parse(tmp_path):
    """``None`` for the Python backend to read: a source ruff rejects, one not UTF-8, one too large, one unreadable."""
    files = {
        "plain.py": b"import os\n",
        "rejected.py": b"import os\nx = " + b"a if c else " * 203 + b"a\n",  # ruff's nesting limit is 202
        "keywords.py": b"import os\nf(a=1, a=2)\n",  # the compiler's error, not CPython's parser's
        "latin1.py": b"# -*- coding: latin-1 -*-\nimport os  # caf\xe9\n",
        "large.py": b"import os\n" + b"# padding padding padding padding padding padding\n" * 90_000,  # over 4 MiB
    }
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    modules = [(str(tmp_path / name), name.removesuffix(".py"), False) for name in [*files, "missing.py"]]

    each = _rust.rust_parse_imports_each(modules, False)

    assert each == [(["os"], []), None, None, None, None, None]


FILES = {
    "app/__init__.py": "",
    "app/core.py": "def f():\n    return 1\n",
}


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("x = " + "a if c else " * 203 + "a\n", id="203 nested conditionals (ruff refuses them)"),
        pytest.param("x = " + " + ".join(["a"] * 600) + "\n", id="a 600-term + chain (astroid gives up)"),
        pytest.param("x = f" + ".a" * 600 + "\n", id="a 600-link attribute chain (astroid gives up)"),
    ],
)
@pytest.mark.parametrize("ignore_type_checking_imports", [False, True], ids=["", "type-only ignored"])
@pytest.mark.usefixtures("import_backend")
def test_a_source_one_parser_gives_up_on_keeps_its_imports(tmp_path, source, ignore_type_checking_imports):
    """CPython compiles each of these: its imports are real, so neither backend may lose them, nor crash."""
    write_files(tmp_path, {**FILES, "tests/test_deep.py": f"from app.core import f\n{source}"})

    dep_tree = graph.build_dep_tree(
        "app", tests_package="tests", root_dir=tmp_path, ignore_type_checking_imports=ignore_type_checking_imports
    )

    assert dep_tree.has_edge("app.core", "tests.test_deep")


@pytest.mark.parametrize("ignore_type_checking_imports", [False, True], ids=["", "type-only ignored"])
def test_a_deep_module_through_the_whole_run(tmp_path, ignore_type_checking_imports):
    """End to end in a subprocess, with whichever backend is installed: no crash, and the plain module still links."""
    write_files(
        tmp_path,
        {
            **FILES,
            "app/generated.py": chain(60_000),
            "tests/test_core.py": "from app.core import f\n",
            "tests/test_generated.py": "import app.generated\n",
        },
    )
    script = textwrap.dedent(f"""
        from pytest_impacted import graph
        tree = graph.build_dep_tree("app", tests_package="tests", root_dir={str(tmp_path)!r},
                                    ignore_type_checking_imports={ignore_type_checking_imports})
        assert tree.has_edge("app.core", "tests.test_core"), sorted(tree.edges)
        assert tree.has_edge("app.generated", "tests.test_generated"), sorted(tree.edges)
    """)

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300, check=False)

    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr[-2000:]}"


@pytest.mark.usefixtures("import_backend")
def test_a_source_in_a_declared_encoding_keeps_its_imports(tmp_path):
    """PEP 263: a latin-1 file declaring so is valid Python. The extension reads UTF-8 only, and hands it back."""
    write_files(tmp_path, FILES)
    (tmp_path / "tests").mkdir()
    legacy = b"# -*- coding: latin-1 -*-\nfrom app.core import f  # caf\xe9\n"
    (tmp_path / "tests" / "test_legacy.py").write_bytes(legacy)

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path)

    assert dep_tree.has_edge("app.core", "tests.test_legacy")


@pytest.mark.parametrize("import_backend", ["rust", "python"], indirect=True)
def test_a_type_only_import_in_a_source_astroid_gives_up_on(tmp_path, import_backend):
    """A documented parity exception: astroid gives up near 500 nested terms, and the fallback keeps every import
    a runtime one; the extension tells type-only imports apart until DEEPEST (2,000). Only under
    --impacted-ignore-type-checking-imports, and the Python backend errs on the safe side: an edge kept."""
    source = (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.core import f\n\n"
        "def g(h: 'f'):\n    pass\n\nx = " + " + ".join(["a"] * 800) + "\n"
    )
    write_files(tmp_path, {**FILES, "app/user.py": source, "tests/test_user.py": "import app.user\n"})

    dep_tree = graph.build_dep_tree("app", tests_package="tests", root_dir=tmp_path, ignore_type_checking_imports=True)

    assert dep_tree.has_edge("app.core", "app.user") is (import_backend == "python")


@pytest.mark.skipif(not _rust.RUST_AVAILABLE, reason="pytest-impacted-rs is not installed")
@pytest.mark.parametrize("function", ["rust_parse_all_imports", "rust_parse_all_imports_split"])
def test_the_older_functions_still_read_a_large_file(tmp_path, function):
    """An older pytest-impacted calls these, and cannot read a file handed back: they read it, however large."""
    (tmp_path / "generated.py").write_bytes(b"import os\n" + b"x = 1\n" * 800_000)  # 4.8 MB, over the cap

    found = getattr(_rust, function)([(str(tmp_path / "generated.py"), "app.generated", False)])

    imports = found["app.generated"]
    assert (imports if function == "rust_parse_all_imports" else imports[0]) == ["os"]
