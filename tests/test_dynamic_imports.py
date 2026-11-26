"""The imports a file makes by name: literal arguments of the known APIs that import them (see dynamic_imports).

Every case runs through both backends: the stdlib ``ast`` pass, and the Rust extension reading the same table.
"""

import warnings

import pytest

from pytest_impacted import _rust, backends, graph
from pytest_impacted.dynamic_imports import LOADERS, TABLE, DynamicImports, Names, parse_dynamic_imports


@pytest.fixture
def engine(by_name_backend):
    """A backend's reading of imports by name, as the graph asks for it: ``(file path, module name) ->
    DynamicImports``. The Rust one leaves to the stdlib pass what it cannot read, as in the graph."""

    def parse(file_path, module_name):
        return graph._parse_all_dynamic_imports({module_name: file_path}, backends.backend_for())[module_name]

    return parse


def rust_reads(file_path, module_name="app.loader", is_package=False):
    """What the extension itself makes of a file: ``None`` for one it leaves to the stdlib pass."""
    if not _rust.RUST_PARSES_DYNAMIC_IMPORTS:
        pytest.skip("pytest-impacted-rs is not installed, or predates finding imports by name")
    return _rust.rust_parse_all_dynamic_imports([(file_path, module_name, is_package)], TABLE)[0]


@pytest.fixture
def parse(tmp_path, engine):
    """Factory: what a file of *source* imports by name, as module *module*."""

    def parse(source, module="app.loader", is_package=False):
        path = tmp_path / ("__init__.py" if is_package else "loader.py")
        path.write_text(source)
        return engine(str(path), module)

    return parse


def found(modules=(), objects=(), attributes=()):
    return DynamicImports(list(modules), list(objects), list(attributes))


NOTHING = found()


MODULES_NAMED = [
    # importlib.import_module, under every spelling of the callee
    "import importlib\nimportlib.import_module('app.target')",
    "import importlib as lib\nlib.import_module('app.target')",
    "import importlib.util\nimportlib.import_module('app.target')",
    "from importlib import import_module\nimport_module('app.target')",
    "from importlib import import_module as load\nload('app.target')",
    "def load():\n    from importlib import import_module\n    return import_module('app.target')",
    "import importlib\nimportlib.import_module(name='app.target')",
    "import importlib\nimportlib.import_module('app.' 'target')",  # one literal, implicitly concatenated
    "import importlib\nimportlib.import_module('app.target', *rest)",  # unpacked after the name
    "import importlib\nimportlib.import_module('app.target', **options)",
    # a relative name, anchored as importlib.util.resolve_name anchors it
    "from importlib import import_module\nimport_module('.target', 'app')",
    "from importlib import import_module\nimport_module('..target', package='app.child')",
    "from importlib import import_module\nimport_module('.target', __package__)",
    "from importlib import import_module\nimport_module('.target', __spec__.parent)",
    # __import__, the builtin and its other names, when absolute
    "__import__('app.target')",
    "__import__('app.target', level=0)",
    "__import__('app.target', level=False)",
    "__import__('app.target', None, None, [], 0)",
    "import builtins\nbuiltins.__import__('app.target')",
    "from builtins import __import__ as load\nload('app.target')",
    "import importlib\nimportlib.__import__('app.target')",
    # runpy and pytest
    "import runpy\nrunpy.run_module('app.target')",
    "import runpy\nrunpy.run_module(mod_name='app.target', run_name='__main__')",
    "import pytest\npytest.importorskip('app.target')",
    "from pytest import importorskip\nimportorskip(modname='app.target', minversion='1.0')",
    # an object path with a colon names its module exactly
    "import pkgutil\npkgutil.resolve_name('app.target:VALUE')",
    "from pkgutil import resolve_name\nresolve_name('app.target:Model.field')",
    "import pkgutil\npkgutil.resolve_name('app.target:')",
]


ATTRIBUTES_NAMED = [
    # unittest.mock's patch, under every spelling
    "from unittest import mock\nmock.patch('app.target.VALUE')",
    "from unittest.mock import patch\npatch('app.target.VALUE')",
    "from unittest.mock import patch as replace\nreplace('app.target.VALUE')",
    "import unittest.mock\nunittest.mock.patch('app.target.VALUE')",
    "import mock\nmock.patch('app.target.VALUE')",  # the backport
    "from unittest.mock import patch\n@patch('app.target.VALUE')\ndef test(value):\n    pass",
    "from unittest.mock import patch\ndef test():\n    with patch('app.target.VALUE', 2):\n        pass",
    "from unittest.mock import patch\npatch(target='app.target.VALUE')",
    # pytest-mock's fixture, whatever it is called
    "def test(mocker):\n    mocker.patch('app.target.VALUE')",
    "def test(module_mocker):\n    module_mocker.patch('app.target.VALUE')",
    # pytest's monkeypatch, with a string target
    "def test(monkeypatch):\n    monkeypatch.setattr('app.target.VALUE', 2)",
    "def test(monkeypatch):\n    monkeypatch.delattr('app.target.VALUE')",
    "def test(monkeypatch):\n    with monkeypatch.context() as m:\n        m.setattr('app.target.VALUE', 2)",
    "import pytest\nwith pytest.MonkeyPatch.context() as mp:\n    mp.setattr('app.target.VALUE', 2)",
    "import pytest\npytest.MonkeyPatch().setattr('app.target.VALUE', 2)",
]


OBJECTS_NAMED = [
    "from unittest.mock import patch\npatch.dict('app.target.VALUE', {})",
    "from unittest.mock import patch\npatch.multiple('app.target.VALUE', x=1)",
    "def test(mocker):\n    mocker.patch.dict('app.target.VALUE', {})",
    # without a colon: where the module ends, only the project can tell
    "import pkgutil\npkgutil.resolve_name('app.target.VALUE')",
]


NO_IMPORT = [
    # a computed name: no import is known
    "import importlib\nname = 'app.target'\nimportlib.import_module(name)",
    "import importlib\nimportlib.import_module(f'app.{name}')",
    "import importlib\nimportlib.import_module('app.' + name)",
    "import importlib\nimportlib.import_module(*names)",
    "import importlib\nimportlib.import_module(*names, 'app.target')",
    "from unittest.mock import patch\npatch(TARGET)",
    # no dotted name
    "import importlib\nimportlib.import_module('')",
    "import importlib\nimportlib.import_module('app-x.target')",
    "import importlib\nimportlib.import_module('app.target.')",
    "def routes(router):\n    router.patch('/items/{item_id}')",
    "import requests\nrequests.patch('https://example.com/app.target')",
    "import pkgutil\npkgutil.resolve_name('app.target:not an attribute')",
    "import pkgutil\npkgutil.resolve_name(':VALUE')",
    # a relative name with no anchor, a computed one, or one beyond the top-level package
    "from importlib import import_module\nimport_module('.target')",
    "from importlib import import_module\nimport_module('.target', anchor)",
    "from importlib import import_module\nimport_module('..target', 'app')",
    # __import__ relative to its caller, or at a level only known at runtime
    "__import__('target', globals(), locals(), [], 1)",
    "__import__('target', level=1)",
    "__import__('app.target', level=depth)",
    "__import__('app.target', level=0.0)",
    "__import__('target', *rest)",  # a level the unpacking may pass
    "__import__('target', **options)",
    "__import__('.target')",
    # calls that import nothing, or are no loader's
    "import importlib.util\nimportlib.util.resolve_name('.target', 'app')",
    "import importlib.util\nimportlib.util.find_spec('app.target')",
    "from unittest.mock import patch\npatch.object(target, 'app.target')",
    "setattr(obj, 'app.target', 1)",
    "delattr(obj, 'app.target')",
    "import logging\nlogging.getLogger('app.target')",
    "def import_module(name):\n    pass\nimport_module('app.target')",  # not importlib's
    "from loaders import import_module\nimport_module('app.target')",
    "import pytest\npytest.param('app.target')",
    # an attribute path of one part names nothing: another API's method of the same name
    "def configure(config):\n    config.setattr('settings', 1)",
    "def routes(client):\n    client.patch('health')",
    # a loader whose origin is not followed: re-exported by another module, star-imported, assigned
    "from .compat import import_module\nimport_module('app.target')",
    "from importlib import *\nimport_module('app.target')",
    "import importlib\nload = importlib.import_module\nload('app.target')",
    # a loader's name outside a call
    "'''Patch it with patch(\"app.target.VALUE\").'''",
    "# importlib.import_module('app.target')\n",
    "PATCHES = ['app.target.VALUE']",
]


@pytest.mark.parametrize("source", MODULES_NAMED)
def test_a_module_named_to_a_loader_is_imported(parse, source):
    assert parse(source) == found(modules=["app.target"])


@pytest.mark.parametrize(
    ("source", "module", "is_package", "expected"),
    [
        ("import_module('.target', __name__)", "app.loader", False, "app.loader.target"),  # relative to itself
        ("import_module('.target', __name__)", "app", True, "app.target"),  # a package's own name is its package
        ("import_module('.target', __package__)", "app", True, "app.target"),
        ("import_module('..target', __package__)", "app.sub.loader", False, "app.target"),
        ("import_module('.', 'app')", "app.loader", False, "app"),
    ],
)
def test_a_relative_name_resolves_against_its_anchor(parse, source, module, is_package, expected):
    source = "from importlib import import_module\n" + source

    assert parse(source, module, is_package) == found(modules=[expected])


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("__import__('app', fromlist=['target'])", ["app", "app.target"]),
        ("__import__('app', globals(), locals(), ('target', 'other'))", ["app", "app.other", "app.target"]),
        ("__import__('app', fromlist=['*'])", ["app"]),
        ("__import__('app', fromlist=['target', name])", ["app", "app.target"]),  # the literal items
    ],
)
def test_import_s_fromlist_names_submodules_too(parse, source, expected):
    """As ``from app import target`` does: ``target`` may be a submodule, which only the project can tell."""
    assert parse(source) == found(modules=expected)


@pytest.mark.parametrize("source", ATTRIBUTES_NAMED)
def test_an_attribute_path_named_to_a_loader_is_imported(parse, source):
    """``patch`` and ``monkeypatch`` split off the last part as the attribute to replace, on what the rest names."""
    assert parse(source) == found(attributes=["app.target.VALUE"])


@pytest.mark.parametrize("source", OBJECTS_NAMED)
def test_an_object_path_named_to_a_loader_is_imported(parse, source):
    """These import their argument whole: the module is its longest importable prefix."""
    assert parse(source) == found(objects=["app.target.VALUE"])


@pytest.mark.parametrize(
    "source",
    [
        "import pkgutil\npkgutil.resolve_name('plugins')",
        "from unittest.mock import patch\npatch.multiple('plugins', DEBUG=True)",
    ],
)
def test_an_object_path_of_one_part_is_a_module(parse, source):
    """``resolve_name("plugins")`` imports ``plugins``: with no dot, nothing in it is an attribute."""
    assert parse(source) == found(modules=["plugins"])


def test_every_call_counts_once(parse):
    source = (
        "import importlib\nfrom unittest.mock import patch\n"
        "importlib.import_module('app.b')\nimportlib.import_module('app.a')\nimportlib.import_module('app.a')\n"
        "patch('app.c.x')\npatch('app.c.x')\n"
    )

    assert parse(source) == found(modules=["app.a", "app.b"], attributes=["app.c.x"])


@pytest.mark.parametrize("source", NO_IMPORT)
def test_no_import_is_inferred(parse, source):
    assert parse(source) == NOTHING


@pytest.mark.parametrize("loader", LOADERS, ids=lambda loader: loader.callee)
@pytest.mark.parametrize("by", ["position", "keyword"])
def test_every_loader_is_read(parse, loader, by):
    """Each entry of the table, called by its own name with its argument in place: adding one needs no code."""
    arguments = ["None"] * loader.name.position + ["'app.target.VALUE'"]
    call = (
        f"{loader.callee}({', '.join(arguments)})"
        if by == "position"
        else f"{loader.callee}({loader.name.keyword}='app.target.VALUE')"
    )
    head = loader.callee.partition(".")[0]
    imports = (
        "from unittest.mock import patch\n" if head == "patch" else f"import {head}\n" if "." in loader.callee else ""
    )
    source = imports + call

    kind = {Names.MODULE: "modules", Names.OBJECT: "objects", Names.ATTRIBUTE: "attributes"}[loader.names]
    assert parse(source) == found(**{kind: ["app.target.VALUE"]})


def test_the_module_is_never_run(parse):
    source = "raise SystemExit('ran')\nimport importlib\nimportlib.import_module('app.target')\n"

    assert parse(source) == found(modules=["app.target"])


def test_a_file_that_cannot_be_read_or_parsed_imports_nothing(tmp_path, engine, parse):
    assert engine(str(tmp_path / "missing.py"), "app.missing") == NOTHING
    (tmp_path / "latin1.py").write_bytes(b"import importlib\nimportlib.import_module('app.target')  # caf\xe9\n")
    assert engine(str(tmp_path / "latin1.py"), "app.latin1") == NOTHING  # not UTF-8: unreadable to both
    assert parse("import importlib\nimportlib.import_module('app.target'\n") == NOTHING


def test_a_warning_turned_error_hides_no_import(parse):
    """Under ``-W error`` an invalid escape is a SyntaxError: that must not drop the import (as for pytest_plugins)."""
    source = 'import re, importlib\nPATTERN = re.compile("\\d+")\nimportlib.import_module("app.target")\n'

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert parse(source) == found(modules=["app.target"])


@pytest.mark.parametrize(
    ("source", "imported"),
    [
        ("x = " + "[" * 5000 + "]" * 5000 + "\nimport importlib\n", NOTHING),
        ("import importlib\nx = " + " + ".join(["importlib.import_module('a.b')"] * 3000), found(modules=["a.b"])),
        ("import importlib\nimportlib" + ".attribute" * 3000 + ".import_module('a.b')", NOTHING),
        ("import importlib\nimportlib.import_module('a.b')\nx = " + "**".join(["2"] * 3000), found(modules=["a.b"])),
    ],
    ids=["deep nesting", "a long concatenation", "a long attribute chain", "a right-nested chain"],
)
def test_pathological_source_never_fails_the_parse(tmp_path, parse, source, imported):
    """Deep enough to stop a recursive walk, or CPython's parser (a MemoryError for the right-nested chain): the
    file imports what can be read of it, or nothing, and with the extension exactly what the stdlib pass finds."""
    result = parse(source)

    assert result == parse_dynamic_imports(str(tmp_path / "loader.py"), "app.loader")
    assert result in (imported, NOTHING)


@pytest.mark.parametrize(
    ("source", "imported"),
    [
        ("\ufeffimport importlib\nimportlib.import_module('app.target')\n", found(modules=["app.target"])),  # a BOM
        ("import importlib\nimportlib.import_module('app.\\x74arget')\n", found(modules=["app.target"])),  # an escape
        ("import importlib\nimportlib.import_module('ünï.cödé')\n", found(modules=["ünï.cödé"])),  # any script
        ("import importlib\nimportlib.import_module('app.✓')\n", found(modules=["app.✓"])),  # non-ASCII counts
        ("import importlib\nimportlib.import_module('app.1st')\n", NOTHING),
        ("import importlib\nimportlib.import_module('app.\\ud800x')\n", NOTHING),  # a lone surrogate
        ("import importlib\nimportlib.import_module('app.\\ufffdx')\n", NOTHING),  # what ruff makes of one
        ("import importlib\nx = f\"{importlib.import_module('app.target')}\"\n", found(modules=["app.target"])),
        (
            "import importlib\ndef f(x=importlib.import_module('app.target')):\n    pass\n",
            found(modules=["app.target"]),
        ),
        (
            "from unittest import mock\nclass C(metaclass=mock.patch('app.target.M')):\n    pass\n",
            found(attributes=["app.target.M"]),
        ),
        ("import importlib\nlambda: [importlib.import_module(n) for n in ('app.target',)]\n", NOTHING),
    ],
    ids=[
        "BOM",
        "escape",
        "non-ASCII",
        "non-ASCII symbol",
        "leading digit",
        "lone surrogate",
        "replacement character",
        "f-string",
        "default",
        "metaclass",
        "loop",
    ],
)
def test_both_backends_read_the_same_source_alike(parse, source, imported):
    """Where the backends' trees differ (a BOM, escapes, f-strings) or an identifier's rules might (other scripts:
    any non-ASCII character counts, so no Unicode version decides)."""
    assert parse(source) == imported


def test_the_extension_refuses_a_table_it_cannot_read():
    if not _rust.RUST_PARSES_DYNAMIC_IMPORTS:
        pytest.skip("pytest-impacted-rs is not installed, or predates finding imports by name")

    with pytest.raises(ValueError, match="unknown kind of name"):
        _rust.rust_parse_all_dynamic_imports([], [("patch", 0, "target", "something", None, None, None)])


@pytest.mark.parametrize(
    "refusal",
    [
        ValueError("unknown kind of name in the loader table"),
        TypeError("a row of another shape"),
        OverflowError("can't convert negative int to unsigned"),  # a position out of its range
    ],
    ids=["a kind it predates", "a row it predates", "a number out of its range"],
)
def test_a_table_the_extension_refuses_is_the_stdlib_pass_s(tmp_path, monkeypatch, refusal):
    """An older extension may meet a newer table (the ``fast`` extra is unpinned): it refuses it, and the stdlib
    pass finds the imports instead, never a failed run."""

    def refuse(modules, table):
        raise refusal

    monkeypatch.setattr(_rust, "RUST_PARSES_DYNAMIC_IMPORTS", True)
    monkeypatch.setattr(_rust, "rust_parse_all_dynamic_imports", refuse)
    (tmp_path / "loader.py").write_text("import importlib\nimportlib.import_module('app.target')\n")

    found_ = graph._parse_all_dynamic_imports({"app.loader": str(tmp_path / "loader.py")}, backends.backend_for())

    assert found_ == {"app.loader": found(modules=["app.target"])}


def test_the_table_is_the_loaders():
    """What the extension reads is the table itself, row by row: the backends cannot hold two tables."""
    assert [row[0] for row in TABLE] == [loader.callee for loader in LOADERS]
    assert {row[3] for row in TABLE} == {names.name.lower() for names in Names}


@pytest.mark.parametrize("source", [*MODULES_NAMED, *ATTRIBUTES_NAMED, *OBJECTS_NAMED, *NO_IMPORT])
def test_the_extension_reads_every_ordinary_source_itself(tmp_path, source):
    """Not vacuous: the cases above are the extension's own reading, not the stdlib pass's it falls back to."""
    (tmp_path / "loader.py").write_text(source)

    assert rust_reads(str(tmp_path / "loader.py")) is not None


@pytest.mark.parametrize(
    ("source", "by_the_stdlib"),
    [
        # the compiler's errors, not ast's: ruff rejects them, CPython's parser does not
        ("import importlib\nimportlib.import_module('app.target')\nf(a=1, a=2)\n", found(modules=["app.target"])),
        (
            "import importlib\nclass C(metaclass=a, metaclass=b):\n    importlib.import_module('app.target')\n",
            found(modules=["app.target"]),
        ),
        # too deep for the extension's walk; CPython 3.11 gives up on it too, later versions parse it
        ("import importlib\nx = " + " + ".join(["1"] * 3000) + "\nimportlib.import_module('app.target')\n", None),
    ],
    ids=["a repeated keyword", "a repeated metaclass", "a deep expression"],
)
def test_the_extension_leaves_to_the_stdlib_pass_what_it_cannot_read(tmp_path, source, by_the_stdlib):
    """The extension says so (``None``), and the graph has the stdlib pass read the file, so the extension never
    finds less: exactly what the stdlib pass finds, whatever the running Python makes of the file."""
    path = str(tmp_path / "loader.py")
    (tmp_path / "loader.py").write_text(source)
    stdlib = parse_dynamic_imports(path, "app.loader")

    assert rust_reads(path) is None
    assert graph._parse_all_dynamic_imports({"app.loader": path}, backends.backend_for())["app.loader"] == stdlib
    assert by_the_stdlib is None or stdlib == by_the_stdlib


def test_the_extension_reads_what_the_running_python_cannot(tmp_path):
    """A known parity exception, the safe way round: the extension parses source the stdlib cannot (a NUL byte
    here; newer syntax on an older interpreter, as for import statements), and finds its imports by name."""
    (tmp_path / "loader.py").write_text("import importlib\nimportlib.import_module('app.target')\nx = '\x00'\n")

    assert rust_reads(str(tmp_path / "loader.py")) == (["app.target"], [], [])
    assert parse_dynamic_imports(str(tmp_path / "loader.py"), "app.loader") == NOTHING
