"""Tests for the traversal module."""

import importlib
import pkgutil
import types
from pathlib import Path

import pytest

from pytest_impacted.traversal import (
    import_submodules,
    iter_namespace,
    package_name_to_path,
    path_to_package_name,
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
    modules = resolve_files_to_modules([test_file], "pytest_impacted")
    assert len(modules) == 1
    assert modules[0] == "pytest_impacted.traversal"


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


def test_path_to_package_name():
    """Test the path_to_package_name function."""
    # Use a path whose name is an importable module (e.g., 'os')
    import os

    path = Path(os.__file__)
    # Remove extension for importable name
    module_name = path.stem
    assert path_to_package_name(path.with_name(module_name)) == "os"
    # Test with string path
    assert path_to_package_name(str(path.with_name(module_name))) == "os"
    # Test with a non-importable name (should raise ModuleNotFoundError)
    fake_path = Path("/tmp/notamodule")
    with pytest.raises(ModuleNotFoundError):
        path_to_package_name(fake_path)


def test_iter_namespace_invalid_input():
    """Test iter_namespace with invalid input types."""
    with pytest.raises(ValueError, match="Invalid namespace package"):
        list(iter_namespace(123))  # type: ignore


def test_import_submodules_with_missing_module():
    """Test import_submodules with a module that doesn't exist."""
    with pytest.MonkeyPatch.context() as m:
        # Mock iter_namespace to return a module that will fail to import
        def mock_iter_namespace(package):
            return [pkgutil.ModuleInfo(None, "nonexistent.module", False)]

        m.setattr("pytest_impacted.traversal.iter_namespace", mock_iter_namespace)

        # Should not raise an exception, but log a warning
        modules = import_submodules("pytest_impacted")
        assert "nonexistent.module" not in modules


def test_import_submodules_with_syntax_error():
    """Test import_submodules gracefully handles SyntaxError from broken modules."""
    from pytest_impacted import traversal

    traversal.import_submodules.cache_clear()
    with pytest.MonkeyPatch.context() as m:

        def mock_iter_namespace(package):
            return [pkgutil.ModuleInfo(None, "broken_syntax_module", False)]

        m.setattr("pytest_impacted.traversal.iter_namespace", mock_iter_namespace)

        original_import = importlib.import_module

        def mock_import_module(name):
            if name == "broken_syntax_module":
                raise SyntaxError("invalid syntax")
            return original_import(name)

        m.setattr(importlib, "import_module", mock_import_module)

        modules = import_submodules("pytest_impacted")
        assert "broken_syntax_module" not in modules


def test_import_submodules_with_import_error():
    """Test import_submodules gracefully handles ImportError from broken modules."""
    from pytest_impacted import traversal

    traversal.import_submodules.cache_clear()
    with pytest.MonkeyPatch.context() as m:

        def mock_iter_namespace(package):
            return [pkgutil.ModuleInfo(None, "broken_import_module", False)]

        m.setattr("pytest_impacted.traversal.iter_namespace", mock_iter_namespace)

        original_import = importlib.import_module

        def mock_import_module(name):
            if name == "broken_import_module":
                raise ImportError("No module named 'missing_dep'")
            return original_import(name)

        m.setattr(importlib, "import_module", mock_import_module)

        modules = import_submodules("pytest_impacted")
        assert "broken_import_module" not in modules


def test_resolve_files_to_modules_edge_cases():
    """Test resolve_files_to_modules with various edge cases."""
    # Test with empty file list
    assert resolve_files_to_modules([], "pytest_impacted") == []

    # Test with non-Python file
    assert resolve_files_to_modules(["test.txt"], "pytest_impacted") == []

    # Test with file outside package
    assert resolve_files_to_modules(["/tmp/test.py"], "pytest_impacted") == []


def test_resolve_modules_to_files_edge_cases():
    """Test resolve_modules_to_files with various edge cases."""
    # Test with empty module list
    assert resolve_modules_to_files([]) == []

    # Test with multiple modules
    files = resolve_modules_to_files(["pytest_impacted.traversal", "pytest_impacted"])
    assert len(files) == 2
    assert all(isinstance(f, str) for f in files)


def test_resolve_modules_to_files_no_file(monkeypatch):
    """Test resolve_modules_to_files with a module that lacks __file__."""

    class DummyModule:
        pass

    dummy = DummyModule()
    monkeypatch.setattr(importlib, "import_module", lambda name: dummy)
    with pytest.raises(AttributeError):
        resolve_modules_to_files(["dummy"])


def test_import_submodules_empty(monkeypatch):
    """Test import_submodules for a package with no submodules."""
    from pytest_impacted import traversal

    traversal.import_submodules.cache_clear()
    monkeypatch.setattr("pytest_impacted.traversal.iter_namespace", lambda pkg: [])
    result = import_submodules("pytest_impacted")
    assert result == {}


def test_iter_namespace_module_without_path(monkeypatch):
    """Test iter_namespace for a module without __path__ attribute."""

    class Dummy:
        __name__ = "dummy"

    dummy = Dummy()
    with pytest.raises(ValueError):
        list(iter_namespace(dummy))


def test_resolve_files_to_modules_with_tests_package():
    """Test resolve_files_to_modules with tests_package parameter."""
    with pytest.MonkeyPatch.context() as m:
        # Mock import_submodules to return known results for both packages
        def mock_import_submodules(package):
            if package == "pytest_impacted":
                return {"pytest_impacted.traversal": types.ModuleType("pytest_impacted.traversal")}
            elif package == "tests":
                return {"tests.test_traversal": types.ModuleType("tests.test_traversal")}
            return {}

        m.setattr("pytest_impacted.traversal.import_submodules", mock_import_submodules)

        # Mock the package path
        def mock_import_module(name):
            if name == "pytest_impacted":
                module = types.ModuleType("pytest_impacted")
                module.__path__ = ["/path/to/pytest_impacted"]
                return module
            elif name == "tests":
                module = types.ModuleType("tests")
                module.__path__ = ["/path/to/tests"]
                return module
            return types.ModuleType(name)

        m.setattr(importlib, "import_module", mock_import_module)

        # Test with a file from the main package
        main_file = "/path/to/pytest_impacted/traversal.py"
        modules = resolve_files_to_modules([main_file], "pytest_impacted", "tests")
        assert len(modules) == 1
        assert modules[0] == "pytest_impacted.traversal"

        # Test with a file from the tests package
        test_file = "/path/to/tests/test_traversal.py"
        modules = resolve_files_to_modules([test_file], "pytest_impacted", "tests")
        assert len(modules) == 1
        assert modules[0] == "tests.test_traversal"


def test_resolve_files_to_modules_init_file():
    """Test resolve_files_to_modules with __init__.py files."""
    with pytest.MonkeyPatch.context() as m:

        def mock_import_submodules(package):
            return {"mypkg": types.ModuleType("mypkg")}

        m.setattr("pytest_impacted.traversal.import_submodules", mock_import_submodules)

        def mock_import_module(name):
            module = types.ModuleType(name)
            module.__path__ = ["/project/mypkg"]
            return module

        m.setattr(importlib, "import_module", mock_import_module)

        modules = resolve_files_to_modules(["/project/mypkg/__init__.py"], "mypkg")
        assert modules == ["mypkg"]


def test_resolve_files_to_modules_relative_git_path():
    """Test resolve_files_to_modules with relative git paths (e.g. 'pytest_impacted/foo.py')."""
    with pytest.MonkeyPatch.context() as m:

        def mock_import_submodules(package):
            return {"mypkg.foo": types.ModuleType("mypkg.foo")}

        m.setattr("pytest_impacted.traversal.import_submodules", mock_import_submodules)

        def mock_import_module(name):
            module = types.ModuleType(name)
            module.__path__ = ["/somewhere/else/mypkg"]
            return module

        m.setattr(importlib, "import_module", mock_import_module)

        # Relative path from git that doesn't match the absolute package path
        modules = resolve_files_to_modules(["mypkg/foo.py"], "mypkg")
        assert modules == ["mypkg.foo"]
