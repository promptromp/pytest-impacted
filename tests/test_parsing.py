"""Unit tests for the parsing module."""

import tempfile
import pytest
from unittest.mock import patch
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
