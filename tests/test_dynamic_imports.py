"""The imports a file makes by name: literal arguments of the known APIs that import them (see dynamic_imports)."""

import warnings

import pytest

from pytest_impacted.dynamic_imports import LOADERS, DynamicImports, Names, parse_dynamic_imports


def parse(tmp_path, source, module="app.loader", is_package=False):
    path = tmp_path / ("__init__.py" if is_package else "loader.py")
    path.write_text(source)
    return parse_dynamic_imports(str(path), module, is_package)


NOTHING = DynamicImports([], [])


@pytest.mark.parametrize(
    "source",
    [
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
    ],
)
def test_a_module_named_to_a_loader_is_imported(tmp_path, source):
    assert parse(tmp_path, source) == DynamicImports(["app.target"], [])


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
def test_a_relative_name_resolves_against_its_anchor(tmp_path, source, module, is_package, expected):
    source = "from importlib import import_module\n" + source

    assert parse(tmp_path, source, module, is_package) == DynamicImports([expected], [])


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("__import__('app', fromlist=['target'])", ["app", "app.target"]),
        ("__import__('app', globals(), locals(), ('target', 'other'))", ["app", "app.other", "app.target"]),
        ("__import__('app', fromlist=['*'])", ["app"]),
        ("__import__('app', fromlist=['target', name])", ["app", "app.target"]),  # the literal items
    ],
)
def test_import_s_fromlist_names_submodules_too(tmp_path, source, expected):
    """As ``from app import target`` does: ``target`` may be a submodule, which only the project can tell."""
    assert parse(tmp_path, source) == DynamicImports(expected, [])


@pytest.mark.parametrize(
    "source",
    [
        # unittest.mock's patchers, under every spelling
        "from unittest import mock\nmock.patch('app.target.VALUE')",
        "from unittest.mock import patch\npatch('app.target.VALUE')",
        "from unittest.mock import patch as replace\nreplace('app.target.VALUE')",
        "import unittest.mock\nunittest.mock.patch('app.target.VALUE')",
        "import mock\nmock.patch('app.target.VALUE')",  # the backport
        "from unittest.mock import patch\n@patch('app.target.VALUE')\ndef test(value):\n    pass",
        "from unittest.mock import patch\ndef test():\n    with patch('app.target.VALUE', 2):\n        pass",
        "from unittest.mock import patch\npatch(target='app.target.VALUE')",
        "from unittest.mock import patch\npatch.dict('app.target.VALUE', {})",
        "from unittest.mock import patch\npatch.multiple('app.target.VALUE', x=1)",
        # pytest-mock's fixture, whatever it is called
        "def test(mocker):\n    mocker.patch('app.target.VALUE')",
        "def test(mocker):\n    mocker.patch.dict('app.target.VALUE', {})",
        "def test(module_mocker):\n    module_mocker.patch('app.target.VALUE')",
        # pytest's monkeypatch, with a string target
        "def test(monkeypatch):\n    monkeypatch.setattr('app.target.VALUE', 2)",
        "def test(monkeypatch):\n    monkeypatch.delattr('app.target.VALUE')",
        "def test(monkeypatch):\n    with monkeypatch.context() as m:\n        m.setattr('app.target.VALUE', 2)",
        "import pytest\nwith pytest.MonkeyPatch.context() as mp:\n    mp.setattr('app.target.VALUE', 2)",
        "import pytest\npytest.MonkeyPatch().setattr('app.target.VALUE', 2)",
        # pkgutil.resolve_name without a colon: where the module ends, only the project can tell
        "import pkgutil\npkgutil.resolve_name('app.target.VALUE')",
    ],
)
def test_an_object_path_named_to_a_loader_is_imported(tmp_path, source):
    assert parse(tmp_path, source) == DynamicImports([], ["app.target.VALUE"])


def test_an_object_path_of_one_part_is_a_module(tmp_path):
    """``resolve_name("app")`` imports ``app``: with no dot, nothing in it can be an attribute."""
    assert parse(tmp_path, "import pkgutil\npkgutil.resolve_name('app')") == DynamicImports(["app"], [])


def test_every_call_counts_once(tmp_path):
    source = (
        "import importlib\nfrom unittest.mock import patch\n"
        "importlib.import_module('app.b')\nimportlib.import_module('app.a')\nimportlib.import_module('app.a')\n"
        "patch('app.c.x')\npatch('app.c.x')\n"
    )

    assert parse(tmp_path, source) == DynamicImports(["app.a", "app.b"], ["app.c.x"])


@pytest.mark.parametrize(
    "source",
    [
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
        # a loader's name outside a call
        "'''Patch it with patch(\"app.target.VALUE\").'''",
        "# importlib.import_module('app.target')\n",
        "PATCHES = ['app.target.VALUE']",
    ],
)
def test_no_import_is_inferred(tmp_path, source):
    assert parse(tmp_path, source) == NOTHING


@pytest.mark.parametrize("loader", LOADERS, ids=lambda loader: loader.callee)
@pytest.mark.parametrize("by", ["position", "keyword"])
def test_every_loader_is_read(tmp_path, loader, by):
    """Each entry of the table, called by its own name with its argument in place: adding one needs no code."""
    arguments = ["None"] * loader.name.position + ["'app.target.VALUE'"]
    call = (
        f"{loader.callee}({', '.join(arguments)})"
        if by == "position"
        else f"{loader.callee}({loader.name.keyword}='app.target.VALUE')"
    )
    module, _, _ = loader.callee.rpartition(".")
    source = (f"import {module}\n" if module and module != "patch" else "from unittest.mock import patch\n") + call

    found = parse(tmp_path, source)

    expected = (
        DynamicImports(["app.target.VALUE"], [])
        if loader.names is Names.MODULE
        else DynamicImports([], ["app.target.VALUE"])
    )
    assert found == expected


def test_the_module_is_never_run(tmp_path):
    source = "raise SystemExit('ran')\nimport importlib\nimportlib.import_module('app.target')\n"

    assert parse(tmp_path, source) == DynamicImports(["app.target"], [])


def test_a_file_that_cannot_be_read_or_parsed_imports_nothing(tmp_path):
    assert parse_dynamic_imports(str(tmp_path / "missing.py"), "app.missing") == NOTHING
    assert parse(tmp_path, "import importlib\nimportlib.import_module('app.target'\n") == NOTHING


def test_a_warning_turned_error_hides_no_import(tmp_path):
    """Under ``-W error`` an invalid escape is a SyntaxError: that must not drop the import (as for pytest_plugins)."""
    source = 'import re, importlib\nPATTERN = re.compile("\\d+")\nimportlib.import_module("app.target")\n'

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert parse(tmp_path, source) == DynamicImports(["app.target"], [])


@pytest.mark.parametrize(
    "source",
    [
        "x = " + "[" * 5000 + "]" * 5000 + "\nimport importlib\n",
        "import importlib\nx = " + " + ".join(["importlib.import_module('a.b')"] * 3000) + "\n",
        "import importlib\nimportlib" + ".attribute" * 3000 + ".import_module('app.target')\n",
    ],
)
def test_pathological_source_imports_nothing_rather_than_fail(tmp_path, source):
    found = parse(tmp_path, source)

    assert isinstance(found, DynamicImports)
