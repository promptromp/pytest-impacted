"""Tests for path utility functions in strategies module."""

from pathlib import Path

import pytest

from pytest_impacted.parsing import normalize_path


class TestNormalizePath:
    """Test the normalize_path utility function."""

    def test_normalize_string_path(self):
        """Test normalizing a string path."""
        result = normalize_path("/some/path/file.py")
        assert isinstance(result, Path)
        assert str(result) == "/some/path/file.py"

    def testnormalize_pathlib_path(self):
        """Test normalizing a pathlib.Path object."""
        original = Path("/some/path/file.py")
        result = normalize_path(original)
        assert isinstance(result, Path)
        assert result == original
        assert result is original  # Should return the same object

    def test_normalize_py_path_local(self, tmp_path):
        """pytest's legacy ``py.path.local`` (``config.rootdir`` on old pytest) implements ``__fspath__``."""
        py = pytest.importorskip("py")
        result = normalize_path(py.path.local(str(tmp_path)))
        assert result == tmp_path

    def test_normalize_fspath_object(self):
        """Test normalizing an object with __fspath__ method."""

        class MockFSPath:
            """Mock object implementing filesystem path protocol."""

            def __init__(self, path_str: str):
                self._path = path_str

            def __fspath__(self) -> str:
                return self._path

        mock_path = MockFSPath("/some/path/file.py")
        result = normalize_path(mock_path)
        assert isinstance(result, Path)
        assert str(result) == "/some/path/file.py"

    def test_normalize_invalid_object_raises_error(self):
        """Test that invalid objects raise ValueError."""

        with pytest.raises(ValueError, match="Cannot normalize path-like object"):
            normalize_path(object())

    def test_normalize_prioritizes_fspath_over_str(self):
        """Test that __fspath__ is prioritized over __str__ method."""

        class PathWithBoth:
            """Object with both __fspath__ and __str__ methods."""

            def __init__(self, fspath_val: str, str_val: str):
                self._fspath_val = fspath_val
                self._str_val = str_val

            def __fspath__(self) -> str:
                return self._fspath_val

            def __str__(self) -> str:
                return self._str_val

        path_obj = PathWithBoth("/from/fspath", "/from/str")
        result = normalize_path(path_obj)
        assert str(result) == "/from/fspath"  # Should use __fspath__, not __str__

    def test_normalize_relative_path(self):
        """Test normalizing relative paths."""
        result = normalize_path("relative/path/file.py")
        assert isinstance(result, Path)
        assert str(result) == "relative/path/file.py"
        assert not result.is_absolute()

    def test_normalize_empty_string(self):
        """Test normalizing empty string."""
        result = normalize_path("")
        assert isinstance(result, Path)
        assert str(result) == "."  # Empty string becomes current directory
