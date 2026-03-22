"""Tests for the traversal module."""

import importlib
import pkgutil
from pathlib import Path

import pytest

from pytest_impacted.traversal import (
    _find_non_package_prefix,
    discover_submodules,
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


def test_discover_submodules():
    """Test discover_submodules function."""
    modules = discover_submodules("pytest_impacted")
    assert isinstance(modules, dict)
    assert len(modules) > 0
    # Values are absolute file paths (strings), not ModuleType
    assert all(isinstance(v, str) for v in modules.values())
    assert "pytest_impacted.traversal" in modules
    assert modules["pytest_impacted.traversal"].endswith("traversal.py")


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
    files = resolve_modules_to_files(["pytest_impacted.traversal"], ns_module="pytest_impacted")
    assert len(files) == 1
    assert files[0].endswith("traversal.py")


def test_resolve_files_to_modules_with_invalid_file():
    """Test resolve_files_to_modules with an invalid file."""
    # Test with a non-existent file
    modules = resolve_files_to_modules(["nonexistent.py"], "pytest_impacted")
    assert len(modules) == 0


def test_resolve_modules_to_files_with_invalid_module():
    """Test resolve_modules_to_files with a module not in the discovered package."""
    # Module not in the package should be silently skipped (with a warning log)
    files = resolve_modules_to_files(["nonexistent.module"], ns_module="pytest_impacted")
    assert files == []


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
    # Simple directory name
    assert path_to_package_name("tests") == "tests"
    assert path_to_package_name(Path("tests")) == "tests"

    # Nested path
    assert path_to_package_name("tests/unit") == "tests.unit"
    assert path_to_package_name(Path("tests/unit")) == "tests.unit"

    # Normalizes ./ prefix
    assert path_to_package_name("./tests") == "tests"
    assert path_to_package_name("./tests/unit") == "tests.unit"

    # Normalizes trailing slash
    assert path_to_package_name("tests/") == "tests"


def test_iter_namespace_invalid_input():
    """Test iter_namespace with invalid input types."""
    with pytest.raises(ValueError, match="Invalid namespace package"):
        list(iter_namespace(123))  # type: ignore


def test_discover_submodules_skips_missing_files():
    """Test discover_submodules skips modules whose files don't exist on disk."""
    from pytest_impacted import traversal

    traversal.discover_submodules.cache_clear()
    with pytest.MonkeyPatch.context() as m:

        def mock_iter_namespace(package, *, scan_path=None):
            return [pkgutil.ModuleInfo(None, "nonexistent.module", False)]

        m.setattr("pytest_impacted.traversal.iter_namespace", mock_iter_namespace)

        modules = discover_submodules("some_package")
        # Module file won't exist on disk, so it should be skipped
        assert "nonexistent.module" not in modules


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
    assert resolve_modules_to_files([], ns_module="pytest_impacted") == []

    # Test with multiple modules
    modules = ["pytest_impacted.traversal", "pytest_impacted.graph"]
    files = resolve_modules_to_files(modules, ns_module="pytest_impacted")
    assert len(files) == 2
    assert all(isinstance(f, str) for f in files)


def test_discover_submodules_empty(monkeypatch):
    """Test discover_submodules for a package with no submodules."""
    from pytest_impacted import traversal

    traversal.discover_submodules.cache_clear()
    monkeypatch.setattr("pytest_impacted.traversal.iter_namespace", lambda pkg, **kwargs: [])
    result = discover_submodules("some_package")
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
        # Mock discover_submodules to return known results (name -> abs filepath)
        def mock_discover_submodules(package, **kwargs):
            if package == "pytest_impacted":
                return {"pytest_impacted.traversal": "/path/to/pytest_impacted/traversal.py"}
            elif package == "tests":
                return {"tests.test_traversal": "/path/to/tests/test_traversal.py"}
            return {}

        m.setattr("pytest_impacted.traversal.discover_submodules", mock_discover_submodules)

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

        def mock_discover_submodules(package, **kwargs):
            return {"mypkg": "/project/mypkg/__init__.py"}

        m.setattr("pytest_impacted.traversal.discover_submodules", mock_discover_submodules)

        modules = resolve_files_to_modules(["/project/mypkg/__init__.py"], "mypkg")
        assert modules == ["mypkg"]


def test_resolve_files_to_modules_relative_git_path():
    """Test resolve_files_to_modules with relative git paths (e.g. 'pytest_impacted/foo.py')."""
    import os

    with pytest.MonkeyPatch.context() as m:

        def mock_discover_submodules(package, **kwargs):
            # The absolute path must match what os.path.abspath("mypkg/foo.py") resolves to
            return {"mypkg.foo": os.path.abspath("mypkg/foo.py")}

        m.setattr("pytest_impacted.traversal.discover_submodules", mock_discover_submodules)

        # Relative path from git that doesn't match the absolute package path
        modules = resolve_files_to_modules(["mypkg/foo.py"], "mypkg")
        assert modules == ["mypkg.foo"]


def test_discover_submodules_without_init_in_subdirectory(tmp_path, monkeypatch):
    """Modules in subdirectories without __init__.py should be discovered with require_init=False."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").touch()
    (tmp_path / "pkg" / "sub").mkdir()
    (tmp_path / "pkg" / "sub" / "test_thing.py").write_text("def test_it(): pass\n")

    monkeypatch.chdir(tmp_path)
    discover_submodules.cache_clear()

    modules = discover_submodules("pkg", require_init=False)
    assert "pkg.sub.test_thing" in modules


def test_discover_submodules_without_init_in_ancestor_directory(tmp_path, monkeypatch):
    """Modules should be discovered even when an ancestor directory lacks __init__.py."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").touch()
    (tmp_path / "tests" / "app").mkdir()
    (tmp_path / "tests" / "app" / "unit").mkdir()
    (tmp_path / "tests" / "app" / "unit" / "__init__.py").touch()
    (tmp_path / "tests" / "app" / "unit" / "test_core.py").write_text("def test_core(): pass\n")

    monkeypatch.chdir(tmp_path)
    discover_submodules.cache_clear()

    modules = discover_submodules("tests", require_init=False)
    assert "tests.app.unit.test_core" in modules


def test_discover_submodules_require_init_skips_no_init_dirs(tmp_path, monkeypatch):
    """With require_init=True, directories without __init__.py should be skipped."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").touch()
    (tmp_path / "pkg" / "visible.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "no_init_dir").mkdir()
    (tmp_path / "pkg" / "no_init_dir" / "hidden.py").write_text("y = 2\n")

    monkeypatch.chdir(tmp_path)
    discover_submodules.cache_clear()

    modules = discover_submodules("pkg", require_init=True)
    assert "pkg.visible" in modules
    assert "pkg.no_init_dir.hidden" not in modules


def test_discover_submodules_filesystem_nonexistent_dir(tmp_path, monkeypatch):
    """Filesystem discovery should return empty dict for a nonexistent directory."""
    monkeypatch.chdir(tmp_path)
    discover_submodules.cache_clear()

    modules = discover_submodules("nonexistent_pkg", require_init=False)
    assert modules == {}


# --- Tests for _find_non_package_prefix (src-layout support) ---


def test_find_non_package_prefix_flat_layout(tmp_path, monkeypatch):
    """Flat layout: mypackage/ has __init__.py → no prefix."""
    (tmp_path / "mypackage").mkdir()
    (tmp_path / "mypackage" / "__init__.py").touch()
    monkeypatch.chdir(tmp_path)

    prefix, importable = _find_non_package_prefix("mypackage")
    assert prefix == ""
    assert importable == "mypackage"


def test_find_non_package_prefix_src_layout(tmp_path, monkeypatch):
    """src-layout: src/ has no __init__.py, src/predicated/ has __init__.py."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "predicated").mkdir()
    (tmp_path / "src" / "predicated" / "__init__.py").touch()
    monkeypatch.chdir(tmp_path)

    prefix, importable = _find_non_package_prefix("src/predicated")
    assert prefix == "src"
    assert importable == "predicated"


def test_find_non_package_prefix_deeply_nested(tmp_path, monkeypatch):
    """Deeply nested non-package prefix: src/lib/mypackage."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib").mkdir()
    (tmp_path / "src" / "lib" / "mypackage").mkdir()
    (tmp_path / "src" / "lib" / "mypackage" / "__init__.py").touch()
    monkeypatch.chdir(tmp_path)

    prefix, importable = _find_non_package_prefix("src/lib/mypackage")
    assert prefix == "src/lib"
    assert importable == "mypackage"


def test_find_non_package_prefix_no_init_anywhere(tmp_path, monkeypatch):
    """No __init__.py found anywhere → fallback: no prefix, whole path is importable."""
    (tmp_path / "ns_pkg").mkdir()
    monkeypatch.chdir(tmp_path)

    prefix, importable = _find_non_package_prefix("ns_pkg")
    assert prefix == ""
    assert importable == "ns_pkg"


def test_discover_submodules_src_layout(tmp_path, monkeypatch):
    """discover_submodules with src-layout produces importable module names, not src-prefixed."""
    # Create src/srcpkg_a/ layout
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "srcpkg_a").mkdir()
    (tmp_path / "src" / "srcpkg_a" / "__init__.py").write_text("# init\n")
    (tmp_path / "src" / "srcpkg_a" / "core.py").write_text("x = 1\n")
    (tmp_path / "src" / "srcpkg_a" / "utils.py").write_text("y = 2\n")

    monkeypatch.chdir(tmp_path)
    discover_submodules.cache_clear()
    importlib.invalidate_caches()

    modules = discover_submodules("src.srcpkg_a", require_init=True)

    # Module names should use the importable prefix, not src-prefixed
    assert "srcpkg_a.core" in modules
    assert "srcpkg_a.utils" in modules
    # Should NOT have src-prefixed names
    assert "src.srcpkg_a" not in modules
    assert "src.srcpkg_a.core" not in modules


def test_discover_submodules_src_layout_with_subpackage(tmp_path, monkeypatch):
    """Recursive sub-package discovery works correctly in src-layout."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "srcpkg_b").mkdir()
    (tmp_path / "src" / "srcpkg_b" / "__init__.py").write_text("")
    (tmp_path / "src" / "srcpkg_b" / "sub").mkdir()
    (tmp_path / "src" / "srcpkg_b" / "sub" / "__init__.py").write_text("")
    (tmp_path / "src" / "srcpkg_b" / "sub" / "module.py").write_text("z = 3\n")

    monkeypatch.chdir(tmp_path)
    discover_submodules.cache_clear()
    importlib.invalidate_caches()

    modules = discover_submodules("src.srcpkg_b", require_init=True)

    assert "srcpkg_b.sub" in modules
    assert "srcpkg_b.sub.module" in modules
    assert "src.srcpkg_b.sub" not in modules


def test_discover_submodules_flat_layout_backward_compat(tmp_path, monkeypatch):
    """Flat layout (no src/) continues to work as before."""
    (tmp_path / "flatpkg_a").mkdir()
    (tmp_path / "flatpkg_a" / "__init__.py").write_text("")
    (tmp_path / "flatpkg_a" / "module.py").write_text("x = 1\n")

    monkeypatch.chdir(tmp_path)
    discover_submodules.cache_clear()
    importlib.invalidate_caches()

    modules = discover_submodules("flatpkg_a", require_init=True)

    # pkgutil.iter_modules yields children, not the package itself
    assert "flatpkg_a.module" in modules


def test_iter_namespace_with_scan_path(tmp_path, monkeypatch):
    """iter_namespace uses scan_path for filesystem scanning while keeping module prefix."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "pkg" / "mod.py").write_text("")

    monkeypatch.chdir(tmp_path)

    # scan_path points to the filesystem location, but module names use "pkg" prefix
    modules = iter_namespace("pkg", scan_path="src/pkg")
    names = [m.name for m in modules]
    assert "pkg.mod" in names
    # Should NOT have "src.pkg.mod"
    assert all(not n.startswith("src.") for n in names)
