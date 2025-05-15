"""Tests for the traversal module."""

import importlib
import types
from pathlib import Path

import pytest
import pkgutil

from pytest_impacted.traversal import (
    import_submodules,
    iter_namespace,
    package_name_to_path,
    resolve_files_to_modules,
    resolve_modules_to_files,
)


def test_package_name_to_path():
    """Test the package_name_to_path helper function."""
    assert package_name_to_path("simple") == "simple"
    assert package_name_to_path("nested.package") == "nested/package"
    assert package_name_to_path("deeply.nested.package") == "deeply/nested/package"


def test_iter_namespace_with_string():
    """Test iter_namespace with string input."""
    # Test with a known package
    modules = list(iter_namespace("pytest_impacted"))
    assert len(modules) > 0
    # pkgutil.iter_modules returns ModuleInfo objects, not ModuleType
    assert all(hasattr(m, "name") for m in modules)

    # Verify the path conversion is working by checking the module names
    # All module names should start with the original package name
    assert all(m.name.startswith("pytest_impacted.") for m in modules)


def test_iter_namespace_with_module():
    """Test iter_namespace with module input."""
    # Test with a known package
    package = importlib.import_module("pytest_impacted")
    modules = list(iter_namespace(package))
    assert len(modules) > 0
    # pkgutil.iter_modules returns ModuleInfo objects, not ModuleType
    assert all(hasattr(m, "name") for m in modules)


def test_import_submodules():
    """Test import_submodules function."""
    # Test with a known package
    modules = import_submodules("pytest_impacted")
    assert isinstance(modules, dict)
    assert len(modules) > 0
    assert all(isinstance(m, types.ModuleType) for m in modules.values())
    assert "pytest_impacted.traversal" in modules


def test_resolve_files_to_modules():
    """Test resolve_files_to_modules function."""
    package_path = Path(importlib.import_module("pytest_impacted").__path__[0])
    test_file = str(package_path / "traversal.py")
    # Simulate the replacement logic
    module_name = (
        test_file.replace(str(package_path), "")
        .replace("/", ".")
        .replace(".py", "")
        .lstrip(".")
    )
    submodules = import_submodules("pytest_impacted")
    modules = resolve_files_to_modules([test_file], "pytest_impacted")
    # The function will only return the module name if it matches a key in submodules
    if module_name in submodules:
        assert len(modules) == 1
        assert modules[0] == module_name
    else:
        assert modules == []


def test_resolve_modules_to_files():
    """Test resolve_modules_to_files function."""
    # Test with a known module
    files = resolve_modules_to_files(["pytest_impacted.traversal"])
    assert len(files) == 1
    assert files[0].endswith("traversal.py")


def test_resolve_files_to_modules_with_invalid_file():
    """Test resolve_files_to_modules with an invalid file."""
    # Test with a non-existent file
    modules = resolve_files_to_modules(["nonexistent.py"], "pytest_impacted")
    assert len(modules) == 0


def test_resolve_modules_to_files_with_invalid_module():
    """Test resolve_modules_to_files with an invalid module."""
    # Test with a non-existent module
    with pytest.raises(ModuleNotFoundError):
        resolve_modules_to_files(["nonexistent.module"])


def test_iter_namespace_with_nested_package():
    """Test iter_namespace with a nested package name."""
    # Create a temporary nested package structure for testing
    with pytest.MonkeyPatch.context() as m:
        # Mock pkgutil.iter_modules to return a known result
        def mock_iter_modules(path, prefix):
            assert path == ["nested/package"]  # Verify path conversion
            return [pkgutil.ModuleInfo(None, "nested.package.submodule", False)]

        m.setattr(pkgutil, "iter_modules", mock_iter_modules)

        modules = list(iter_namespace("nested.package"))
        assert len(modules) == 1
        assert modules[0].name == "nested.package.submodule"
