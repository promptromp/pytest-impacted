"""Unit tests for the parsing module."""

import tempfile
from unittest.mock import patch

import pytest

from pytest_impacted import parsing


def test_should_silently_ignore_oserror():
    """Test the should_silently_ignore_oserror function."""
    # Test with empty file
    with tempfile.NamedTemporaryFile() as temp_file:
        assert parsing.should_silently_ignore_oserror(temp_file.name) is True

    # Test with non-empty file
    with tempfile.NamedTemporaryFile() as temp_file:
        temp_file.write(b"some content")
        temp_file.flush()
        assert parsing.should_silently_ignore_oserror(temp_file.name) is False


def test_parse_module_imports():
    """Test the parse_module_imports function."""
    # Create a mock module with some imports
    mock_source = """
        import os
        import sys
        from pathlib import Path
        from typing import List, Dict
    """

    mock_module = type("MockModule", (), {"__file__": "/mock/path.py"})

    with patch("inspect.getsource", return_value=mock_source):
        imports = parsing.parse_module_imports(mock_module)
        assert set(imports) == {"os", "sys", "pathlib", "typing"}


def test_parse_module_imports_empty_source():
    """Test parse_module_imports with empty source."""
    mock_module = type("MockModule", (), {"__file__": "/mock/path.py"})

    with patch("inspect.getsource", return_value=None):
        imports = parsing.parse_module_imports(mock_module)
        assert imports == []


def test_parse_module_imports_oserror():
    """Test parse_module_imports handling of OSError."""
    mock_module = type("MockModule", (), {"__file__": "/mock/path.py"})

    # Test with empty file (should return empty list)
    with (
        patch("inspect.getsource", side_effect=OSError()),
        patch("os.stat", return_value=type("MockStat", (), {"st_size": 0})()),
    ):
        imports = parsing.parse_module_imports(mock_module)
        assert imports == []

    # Test with non-empty file (should raise)
    with (
        patch("inspect.getsource", side_effect=OSError()),
        patch("os.stat", return_value=type("MockStat", (), {"st_size": 100})()),
    ):
        with pytest.raises(OSError):
            parsing.parse_module_imports(mock_module)


def test_parse_module_imports_from_statements():
    """Test parse_module_imports with various from-import statement scenarios."""
    # Test importing a module
    mock_source = """
        from pathlib import Path
        from typing import List, Dict
        from os import path
        from sys import modules
    """

    mock_module = type("MockModule", (), {"__file__": "/mock/path.py"})

    with patch("inspect.getsource", return_value=mock_source):
        imports = parsing.parse_module_imports(mock_module)
        assert set(imports) == {"pathlib", "typing", "os.path", "sys"}

    # Test importing non-module items
    mock_source = """
        from datetime import datetime
        from collections import defaultdict
        from unittest.mock import patch
    """

    with patch("inspect.getsource", return_value=mock_source):
        imports = parsing.parse_module_imports(mock_module)
        assert set(imports) == {"datetime", "collections", "unittest.mock"}

    # Test mixed imports
    mock_source = """
        import os
        from pathlib import Path
        from typing import List, Dict
        from unittest.mock import patch
    """

    with patch("inspect.getsource", return_value=mock_source):
        imports = parsing.parse_module_imports(mock_module)
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


def test_parse_module_imports_nested_in_try_except():
    """Test parse_module_imports finds imports inside try/except blocks."""
    mock_source = """
import os

try:
    import ujson as json
except ImportError:
    import json
"""

    mock_module = type("MockModule", (), {"__file__": "/mock/path.py"})

    with patch("inspect.getsource", return_value=mock_source):
        imports = parsing.parse_module_imports(mock_module)
        assert "os" in imports
        assert "ujson" in imports
        assert "json" in imports


def test_parse_module_imports_nested_in_if_block():
    """Test parse_module_imports finds imports inside if-guards."""
    mock_source = """
import sys

if sys.version_info >= (3, 11):
    from tomllib import loads
else:
    from tomli import loads
"""

    mock_module = type("MockModule", (), {"__file__": "/mock/path.py"})

    with patch("inspect.getsource", return_value=mock_source):
        imports = parsing.parse_module_imports(mock_module)
        assert "sys" in imports
        assert "tomllib" in imports
        assert "tomli" in imports


def test_parse_module_imports_with_relative_imports():
    """Test parse_module_imports with relative imports to verify proper package resolution."""
    # Simulate module inside a package: my_package.a
    mock_source = """
        from .models.b import Something
        from . import utils
    """

    # Create a mock module that appears to be at my_package.a
    mock_module = type(
        "MockModule", (), {"__file__": "/mock/my_package/a.py", "__name__": "my_package.a", "__package__": "my_package"}
    )

    with patch("inspect.getsource", return_value=mock_source):
        imports = parsing.parse_module_imports(mock_module)

        # Relative imports should be resolved to full module paths using the package context
        # from .models.b should resolve to my_package.models.b
        assert "my_package.models.b" in imports
        # from . import utils should resolve to my_package
        assert "my_package" in imports

        # These unresolved paths should NOT be in the imports
        assert "models.b" not in imports
        assert "" not in imports


def test_parse_module_imports_with_complex_relative_imports():
    """Test parse_module_imports with various levels of relative imports."""
    # Test more complex relative import scenarios

    # Simulate module inside a nested package: my_package.subpackage.module
    mock_source = """
        from . import sibling_module
        from .sibling import SomeClass
        from ..parent_level import something
        from ...root_level import another
    """

    # Create a mock module at my_package.subpackage.module
    mock_module = type(
        "MockModule",
        (),
        {
            "__file__": "/mock/my_package/subpackage/module.py",
            "__name__": "my_package.subpackage.module",
            "__package__": "my_package.subpackage",
        },
    )

    with patch("inspect.getsource", return_value=mock_source):
        imports = parsing.parse_module_imports(mock_module)

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
