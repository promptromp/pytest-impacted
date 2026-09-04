"""Unit tests for the parsing module."""

import sys

import pytest

from pytest_impacted import parsing


def test_parse_file_imports(tmp_path):
    """Test parse_file_imports with basic import statements."""
    source = """\
import os
import sys
from pathlib import Path
from typing import List, Dict
"""

    path = tmp_path / "mod.py"
    path.write_text(source)
    imports = parsing.parse_file_imports(str(path), "mypkg.mymod")
    assert set(imports) == {"os", "sys", "pathlib", "pathlib.Path", "typing", "typing.List", "typing.Dict"}


def test_parse_file_imports_empty_source(tmp_path):
    """Test parse_file_imports with an empty file."""
    path = tmp_path / "mod.py"
    path.write_text("")
    imports = parsing.parse_file_imports(str(path), "mypkg.mymod")
    assert imports == []


def test_parse_file_imports_nonexistent_file(tmp_path):
    """Test parse_file_imports with a file that doesn't exist."""
    imports = parsing.parse_file_imports("/nonexistent/path.py", "mypkg.mymod")
    assert imports == []


def test_parse_file_imports_zero_byte_file(tmp_path):
    """Test parse_file_imports gracefully handles zero-byte files."""
    path = tmp_path / "mod.py"
    path.write_bytes(b"")  # file exists but stays at 0 bytes
    imports = parsing.parse_file_imports(str(path), "mypkg.mymod")
    assert imports == []


def test_parse_file_imports_from_statements(tmp_path):
    """Test parse_file_imports with various from-import statement scenarios."""
    # Whether ``path`` is a submodule or a symbol is undecidable without importing,
    # so both the package and the qualified name are reported.
    source = """\
from pathlib import Path
from typing import List, Dict
from os import path
from sys import modules
"""
    path = tmp_path / "mod.py"
    path.write_text(source)
    imports = parsing.parse_file_imports(str(path), "mypkg.mymod")
    # Both the package and the imported name are reported: without importing
    # the package there is no way to know whether ``path`` is a submodule or
    # a symbol, and the graph builder filters to discovered modules anyway.
    assert set(imports) == {
        "pathlib",
        "pathlib.Path",
        "typing",
        "typing.List",
        "typing.Dict",
        "os",
        "os.path",
        "sys",
        "sys.modules",
    }

    # Symbols are reported as candidates too; the graph builder filters them out.
    source = """\
from datetime import datetime
from collections import defaultdict
from unittest.mock import patch
"""
    path = tmp_path / "mod.py"
    path.write_text(source)
    imports = parsing.parse_file_imports(str(path), "mypkg.mymod")
    assert set(imports) == {
        "datetime",
        "datetime.datetime",
        "collections",
        "collections.defaultdict",
        "unittest.mock",
        "unittest.mock.patch",
    }

    # Test mixed imports
    source = """\
import os
from pathlib import Path
from typing import List, Dict
from unittest.mock import patch
"""
    path = tmp_path / "mod.py"
    path.write_text(source)
    imports = parsing.parse_file_imports(str(path), "mypkg.mymod")
    assert set(imports) == {
        "os",
        "pathlib",
        "pathlib.Path",
        "typing",
        "typing.List",
        "typing.Dict",
        "unittest.mock",
        "unittest.mock.patch",
    }


@pytest.mark.parametrize(
    "module_name,expected",
    [
        # Test module naming patterns
        ("test_something", True),
        ("something_test", True),
        ("package.tests.module", True),
        ("package.tests.module.test_something", True),
        ("tests.test_something", True),
        ("tests.test_something.test_something_else", True),
        ("tests.something_test", True),
        ("tests.something_something", True),
        # Non-test module names
        ("regular_module", False),
        ("package.module", False),
        # Edge cases
        ("test", True),
        ("tests", True),
        ("test_", True),
        ("_test", True),
    ],
)
def test_is_test_module(module_name, expected):
    """Test the is_test_module function with various module naming patterns.

    Args:
    module_name: The module name to test
    expected: The expected result (True if it should be considered a test module)
    """
    assert parsing.is_test_module(module_name) is expected


def test_parse_file_imports_nested_in_try_except(tmp_path):
    """Test parse_file_imports finds imports inside try/except blocks."""
    source = """\
import os

try:
    import ujson as json
except ImportError:
    import json
"""

    path = tmp_path / "mod.py"
    path.write_text(source)
    imports = parsing.parse_file_imports(str(path), "mypkg.mymod")
    assert "os" in imports
    assert "ujson" in imports
    assert "json" in imports


def test_parse_file_imports_nested_in_if_block(tmp_path):
    """Test parse_file_imports finds imports inside if-guards."""
    source = """\
import sys

if sys.version_info >= (3, 11):
    from tomllib import loads
else:
    from tomli import loads
"""

    path = tmp_path / "mod.py"
    path.write_text(source)
    imports = parsing.parse_file_imports(str(path), "mypkg.mymod")
    assert "sys" in imports
    assert "tomllib" in imports
    assert "tomli" in imports


def test_parse_file_imports_with_relative_imports(tmp_path):
    """Test parse_file_imports with relative imports to verify proper package resolution."""
    source = """\
from .models.b import Something
from . import utils
"""

    path = tmp_path / "mod.py"
    path.write_text(source)
    # Module is my_package.a, so relative imports resolve against my_package
    imports = parsing.parse_file_imports(str(path), "my_package.a")

    # from .models.b should resolve to my_package.models.b
    assert "my_package.models.b" in imports
    # from . import utils should resolve to my_package and my_package.utils
    assert "my_package" in imports
    assert "my_package.utils" in imports

    # These unresolved paths should NOT be in the imports
    assert "models.b" not in imports
    assert "" not in imports


def test_parse_file_imports_with_complex_relative_imports(tmp_path):
    """Test parse_file_imports with various levels of relative imports."""
    source = """\
from . import sibling_module
from .sibling import SomeClass
from ..parent_level import something
from ...root_level import another
"""

    path = tmp_path / "mod.py"
    path.write_text(source)
    # Module is my_package.subpackage.module
    imports = parsing.parse_file_imports(str(path), "my_package.subpackage.module")

    # from . import sibling_module -> my_package.subpackage
    assert "my_package.subpackage" in imports
    # from .sibling -> my_package.subpackage.sibling
    assert "my_package.subpackage.sibling" in imports
    # from ..parent_level -> my_package.parent_level
    assert "my_package.parent_level" in imports
    # from ...root_level -> root_level (goes up to root)
    assert "root_level" in imports

    # These should NOT be in imports
    assert "sibling" not in imports
    assert "parent_level" not in imports
    assert "" not in imports


def test_parse_file_imports_syntax_error(tmp_path):
    """Test parse_file_imports gracefully handles files with syntax errors."""
    source = """\
import os
def broken(
"""

    path = tmp_path / "mod.py"
    path.write_text(source)
    imports = parsing.parse_file_imports(str(path), "mypkg.broken")
    assert imports == []


def test_parse_file_imports_never_executes_package_code(tmp_path, monkeypatch):
    """Resolving ``from pkg import name`` must not import ``pkg``: its ``__init__`` may have side effects."""
    pkg_name = "sideeffect_pkg_for_parsing_test"
    pkg = tmp_path / pkg_name
    pkg.mkdir()
    sentinel = tmp_path / "EXECUTED"
    (pkg / "__init__.py").write_text(f"open({str(sentinel)!r}, 'w').close()\n")
    (pkg / "a.py").write_text("x = 1\n")
    test_file = tmp_path / "test_a.py"
    test_file.write_text(f"from {pkg_name} import a\nfrom {pkg_name}.a import x\n")
    # A cached parent package would let find_spec skip __init__; make sure the guard is real.
    monkeypatch.delitem(sys.modules, pkg_name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))

    imports = parsing.parse_file_imports(str(test_file), "test_a")

    assert not sentinel.exists(), "package __init__ ran during static analysis"
    assert set(imports) == {pkg_name, f"{pkg_name}.a", f"{pkg_name}.a.x"}


def test_parse_file_imports_strips_utf8_bom(tmp_path):
    """Editors on Windows often save a BOM; ruff ignores it, astroid must not choke on it."""
    path = tmp_path / "mod.py"
    path.write_bytes(b"\xef\xbb\xbfimport foo\nfrom bar import baz\n")

    assert parsing.parse_file_imports(str(path), "mod") == ["bar", "bar.baz", "foo"]


def test_parse_file_imports_star_import_names_only_the_package(tmp_path):
    path = tmp_path / "mod.py"
    path.write_text("from pkg import *\nfrom . import *\n")

    assert parsing.parse_file_imports(str(path), "top.mid.mod") == ["pkg", "top.mid"]


PARITY_SOURCE = """\
import os, sys as system
from pathlib import Path
from os import path, sep
from . import sibling
from .models.b import Thing
from ..up import x
from pkg import *
try:
    import ujson as json
except ImportError:
    import json
if sys.platform == "win32":
    from ctypes import windll
match system.platform:
    case "linux":
        from posixpath import join
    case _:
        from ntpath import join
while False:
    import while_body
else:
    import while_else
for _ in ():
    import for_body
with open(os.devnull) as fh:
    import with_body
def f():
    from functools import lru_cache
class C:
    from collections import OrderedDict
"""


@pytest.mark.parametrize(
    ("module_name", "is_package"),
    [
        ("my_package.sub.mod", False),
        ("my_package.sub", True),
        ("top_level", False),
    ],
)
def test_parse_file_imports_matches_rust_backend(tmp_path, module_name, is_package):
    """Both backends must agree, or the fast extra silently changes which tests run."""
    rust = pytest.importorskip("pytest_impacted_rs")
    path = tmp_path / "mod.py"
    path.write_text(PARITY_SOURCE)

    python = parsing.parse_file_imports(str(path), module_name, is_package=is_package)
    rust_result = rust.parse_file_imports(str(path), module_name, is_package)

    assert python == rust_result
    assert "posixpath.join" in python, "match-case bodies must be scanned"
    assert "pkg.*" not in python


def test_parse_file_imports_is_package_resolves_against_the_package_itself(tmp_path):
    """A package's ``__init__.py`` resolves relative imports against itself, not its parent."""
    path = tmp_path / "__init__.py"
    path.write_text("from . import sub\nfrom .sub import Thing\n")

    as_package = parsing.parse_file_imports(str(path), "pkg", is_package=True)
    as_module = parsing.parse_file_imports(str(path), "pkg", is_package=False)

    assert as_package == ["pkg", "pkg.sub", "pkg.sub.Thing"]
    assert as_module != as_package, "is_package must change how relative imports resolve"


def test_parse_file_imports_matches_rust_backend_with_bom(tmp_path):
    """The BOM fix is Python-side; the backends must still agree on a BOM-prefixed file."""
    rust = pytest.importorskip("pytest_impacted_rs")
    path = tmp_path / "mod.py"
    path.write_bytes(b"\xef\xbb\xbf" + PARITY_SOURCE.encode("utf-8"))

    assert parsing.parse_file_imports(str(path), "my_package.sub.mod") == rust.parse_file_imports(
        str(path), "my_package.sub.mod", False
    )
