"""Unit tests for the parsing module."""

import tempfile

import pytest

from pytest_impacted import parsing


def test_parse_file_imports():
    """Test parse_file_imports with basic import statements."""
    source = """\
import os
import sys
from pathlib import Path
from typing import List, Dict
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.mymod")
        assert set(imports) == {"os", "sys", "pathlib", "typing"}


def test_parse_file_imports_empty_source():
    """Test parse_file_imports with an empty file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("")
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.mymod")
        assert imports == []


def test_parse_file_imports_nonexistent_file():
    """Test parse_file_imports with a file that doesn't exist."""
    imports = parsing.parse_file_imports("/nonexistent/path.py", "mypkg.mymod")
    assert imports == []


def test_parse_file_imports_zero_byte_file():
    """Test parse_file_imports gracefully handles zero-byte files."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        # Write nothing — file stays at 0 bytes
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.mymod")
        assert imports == []


def test_parse_file_imports_from_statements():
    """Test parse_file_imports with various from-import statement scenarios."""
    # Test importing a sub-module vs a symbol
    source = """\
from pathlib import Path
from typing import List, Dict
from os import path
from sys import modules
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.mymod")
        assert set(imports) == {"pathlib", "typing", "os.path", "sys"}

    # Test importing non-module items
    source = """\
from datetime import datetime
from collections import defaultdict
from unittest.mock import patch
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.mymod")
        assert set(imports) == {"datetime", "collections", "unittest.mock"}

    # Test mixed imports
    source = """\
import os
from pathlib import Path
from typing import List, Dict
from unittest.mock import patch
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.mymod")
        assert set(imports) == {"os", "pathlib", "typing", "unittest.mock"}


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


@pytest.mark.parametrize(
    "module_path,package,expected",
    [
        (".parsing", "pytest_impacted", True),
        ("tests.test_parsing", "tests", True),
        ("pytest_impacted.nonexistent", "pytest_impacted", False),
        ("pytest_impacted.nonexistent.module", "pytest_impacted", False),
        ("os", None, True),
        ("sys", None, True),
        ("nonexistent.module", None, False),
        # ValueError cases: empty string, leading dots without package
        ("", None, False),
        ("..invalid", None, False),
    ],
)
def test_is_module_path(module_path, package, expected):
    """Test is_module_path with various import scenarios."""
    assert parsing.is_module_path(module_path, package=package) is expected


def test_parse_file_imports_nested_in_try_except():
    """Test parse_file_imports finds imports inside try/except blocks."""
    source = """\
import os

try:
    import ujson as json
except ImportError:
    import json
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.mymod")
        assert "os" in imports
        assert "ujson" in imports
        assert "json" in imports


def test_parse_file_imports_nested_in_if_block():
    """Test parse_file_imports finds imports inside if-guards."""
    source = """\
import sys

if sys.version_info >= (3, 11):
    from tomllib import loads
else:
    from tomli import loads
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.mymod")
        assert "sys" in imports
        assert "tomllib" in imports
        assert "tomli" in imports


def test_parse_file_imports_with_relative_imports():
    """Test parse_file_imports with relative imports to verify proper package resolution."""
    source = """\
from .models.b import Something
from . import utils
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        # Module is my_package.a, so relative imports resolve against my_package
        imports = parsing.parse_file_imports(f.name, "my_package.a")

        # from .models.b should resolve to my_package.models.b
        assert "my_package.models.b" in imports
        # from . import utils should resolve to my_package
        assert "my_package" in imports

        # These unresolved paths should NOT be in the imports
        assert "models.b" not in imports
        assert "" not in imports


def test_parse_file_imports_with_complex_relative_imports():
    """Test parse_file_imports with various levels of relative imports."""
    source = """\
from . import sibling_module
from .sibling import SomeClass
from ..parent_level import something
from ...root_level import another
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        # Module is my_package.subpackage.module
        imports = parsing.parse_file_imports(f.name, "my_package.subpackage.module")

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


def test_parse_file_imports_syntax_error():
    """Test parse_file_imports gracefully handles files with syntax errors."""
    source = """\
import os
def broken(
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        imports = parsing.parse_file_imports(f.name, "mypkg.broken")
        assert imports == []
