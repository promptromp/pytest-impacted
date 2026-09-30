"""Tests for the traversal module."""

import importlib
import os
import pkgutil
import sys
from pathlib import Path

import pytest

from pytest_impacted import traversal
from pytest_impacted.traversal import (
    _ConftestCandidate,
    clear_discovery_cache,
    discover_ancestor_conftests,
    discover_application_files,
    discover_project_modules,
    discover_submodules,
    find_non_package_prefix,
    import_roots,
    iter_namespace,
    package_name_to_path,
    path_to_package_name,
    resolve_files_to_modules,
    resolve_modules_to_files,
    split_import_roots,
)

from .git_helpers import write_files


# This repository's own package, found from this file rather than the working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_package_name_to_path():
    """Test the package_name_to_path helper function."""
    assert package_name_to_path("simple") == "simple"
    assert package_name_to_path("nested.package") == "nested/package"
    assert package_name_to_path("deeply.nested.package") == "deeply/nested/package"


def test_iter_namespace_with_string():
    """Test iter_namespace with string input."""
    # Test with a known package
    modules = list(iter_namespace("pytest_impacted", scan_path=str(REPO_ROOT / "pytest_impacted")))
    assert len(modules) > 0

    # pkgutil.iter_modules returns ModuleInfo objects, not ModuleType
    assert all(hasattr(m, "name") for m in modules)

    # Verify the path conversion is working by checking the module names
    # All module names should start with the original package name
    assert all(m.name.startswith("pytest_impacted.") for m in modules)


def test_discover_submodules():
    """Test discover_submodules function."""
    modules = discover_submodules("pytest_impacted", root_dir=REPO_ROOT)
    assert isinstance(modules, dict)
    assert len(modules) > 0
    # Values are absolute file paths (strings), not ModuleType
    assert all(isinstance(v, str) for v in modules.values())
    assert "pytest_impacted.traversal" in modules
    assert modules["pytest_impacted.traversal"].endswith("traversal.py")


def test_resolve_files_to_modules():
    """Test resolve_files_to_modules function."""
    test_file = str(REPO_ROOT / "pytest_impacted" / "traversal.py")
    modules = resolve_files_to_modules([test_file], "pytest_impacted", root_dir=REPO_ROOT)
    assert len(modules) == 1
    assert modules[0] == "pytest_impacted.traversal"


def test_resolve_modules_to_files():
    """Test resolve_modules_to_files function."""
    # Test with a known module
    files = resolve_modules_to_files(["pytest_impacted.traversal"], ns_module="pytest_impacted", root_dir=REPO_ROOT)
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

        modules = list(iter_namespace("nested.package", scan_path="nested/package"))
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


def test_discover_submodules_skips_missing_files():
    """Test discover_submodules skips modules whose files don't exist on disk."""
    traversal.clear_discovery_cache()
    with pytest.MonkeyPatch.context() as m:

        def mock_iter_namespace(package, *, scan_path):
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
    files = resolve_modules_to_files(modules, ns_module="pytest_impacted", root_dir=REPO_ROOT)
    assert len(files) == 2
    assert all(isinstance(f, str) for f in files)


def test_a_package_with_no_submodules_is_itself(tmp_path):
    """Its own ``__init__.py`` is a module, so ``from pkg import name`` has something to link to."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").touch()

    assert discover_submodules("pkg", root_dir=tmp_path) == {"pkg": str((tmp_path / "pkg/__init__.py").resolve())}


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


def test_resolve_files_to_modules_relative_git_path():
    """Test resolve_files_to_modules with relative git paths (e.g. 'pytest_impacted/foo.py')."""
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
    clear_discovery_cache()

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
    clear_discovery_cache()

    modules = discover_submodules("tests", require_init=False)
    assert "tests.app.unit.test_core" in modules


@pytest.mark.parametrize("require_init", [True, False])
def test_discover_submodules_nonexistent_dir(tmp_path, require_init):
    """Either mode returns an empty dict for a package that does not exist, rather than raising."""
    assert discover_submodules("nonexistent_pkg", require_init=require_init, root_dir=tmp_path) == {}


# --- Tests for find_non_package_prefix (src-layout support) ---


def test_find_non_package_prefix_flat_layout(tmp_path):
    """Flat layout: mypackage/ has __init__.py → no prefix."""
    (tmp_path / "mypackage").mkdir()
    (tmp_path / "mypackage" / "__init__.py").touch()

    prefix, importable = find_non_package_prefix("mypackage", tmp_path)
    assert prefix == ""
    assert importable == "mypackage"


def test_find_non_package_prefix_src_layout(tmp_path):
    """src-layout: src/ has no __init__.py, src/predicated/ has __init__.py."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "predicated").mkdir()
    (tmp_path / "src" / "predicated" / "__init__.py").touch()

    prefix, importable = find_non_package_prefix("src/predicated", tmp_path)
    assert prefix == "src"
    assert importable == "predicated"


def testfind_non_package_prefix_deeply_nested(tmp_path):
    """Deeply nested non-package prefix: src/lib/mypackage."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib").mkdir()
    (tmp_path / "src" / "lib" / "mypackage").mkdir()
    (tmp_path / "src" / "lib" / "mypackage" / "__init__.py").touch()

    prefix, importable = find_non_package_prefix("src/lib/mypackage", tmp_path)
    assert prefix == "src/lib"
    assert importable == "mypackage"


def test_find_non_package_prefix_no_init_anywhere(tmp_path):
    """No __init__.py found anywhere → fallback: no prefix, whole path is importable."""
    (tmp_path / "ns_pkg").mkdir()

    prefix, importable = find_non_package_prefix("ns_pkg", tmp_path)
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
    clear_discovery_cache()
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
    clear_discovery_cache()
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
    clear_discovery_cache()
    importlib.invalidate_caches()

    modules = discover_submodules("flatpkg_a", require_init=True)

    assert set(modules) == {"flatpkg_a", "flatpkg_a.module"}


def test_iter_namespace_with_scan_path(tmp_path, monkeypatch):
    """iter_namespace uses scan_path for filesystem scanning while keeping module prefix."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "pkg" / "mod.py").write_text("")

    monkeypatch.chdir(tmp_path)

    # scan_path points to the filesystem location, but module names use "pkg" prefix
    modules = iter_namespace("pkg", scan_path=str(tmp_path / "src/pkg"))
    names = [m.name for m in modules]
    assert "pkg.mod" in names
    # Should NOT have "src.pkg.mod"
    assert all(not n.startswith("src.") for n in names)


# --- root_dir: discovery must not depend on the process working directory ---------


@pytest.fixture
def two_projects(tmp_path):
    """Two separate projects that both contain a package named ``mypkg``."""
    for name, module in (("first", "alpha"), ("second", "beta")):
        pkg = tmp_path / name / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").touch()
        (pkg / f"{module}.py").write_text("x = 1\n")
    clear_discovery_cache()
    yield tmp_path / "first", tmp_path / "second"
    clear_discovery_cache()


def test_discover_submodules_uses_root_dir_not_cwd(two_projects, monkeypatch):
    """The scan follows root_dir even when the working directory is somewhere else entirely."""
    first, second = two_projects
    monkeypatch.chdir(second)

    modules = discover_submodules("mypkg", root_dir=first)

    assert set(modules) == {"mypkg", "mypkg.alpha"}
    assert modules["mypkg.alpha"] == str(first / "mypkg" / "alpha.py")


def test_discover_submodules_cache_is_keyed_by_root_dir(two_projects):
    """Two projects with the same package name must not share a cache entry."""
    first, second = two_projects

    assert set(discover_submodules("mypkg", root_dir=first)) == {"mypkg", "mypkg.alpha"}
    assert set(discover_submodules("mypkg", root_dir=second)) == {"mypkg", "mypkg.beta"}


def test_discover_submodules_resolves_symlinked_root(two_projects, tmp_path):
    """A project reached through a symlink yields real paths, not paths through the link."""
    first, _second = two_projects
    link = tmp_path / "link-to-first"
    link.symlink_to(first)

    via_link = discover_submodules("mypkg", root_dir=link)
    clear_discovery_cache()  # or the second call would just reuse the first entry
    direct = discover_submodules("mypkg", root_dir=first)

    assert via_link == direct
    assert not any("link-to-first" in path for path in via_link.values())


def test_resolve_files_to_modules_uses_root_dir_not_cwd(two_projects, monkeypatch):
    """Git reports paths relative to the repository root, not the working directory."""
    first, second = two_projects
    monkeypatch.chdir(second)

    assert resolve_files_to_modules(["mypkg/alpha.py"], ns_module="mypkg", root_dir=first) == ["mypkg.alpha"]


def test_resolve_modules_to_files_uses_root_dir_not_cwd(two_projects, monkeypatch):
    first, second = two_projects
    monkeypatch.chdir(second)

    assert resolve_modules_to_files(["mypkg.alpha"], ns_module="mypkg", root_dir=first) == [
        str(first / "mypkg" / "alpha.py")
    ]


def test_discover_submodules_without_init_uses_root_dir(tmp_path, monkeypatch):
    """The filesystem-walking mode (test directories) honours root_dir too."""
    tests_dir = tmp_path / "proj" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_thing.py").write_text("def test_it(): pass\n")
    clear_discovery_cache()
    monkeypatch.chdir(tmp_path)

    modules = discover_submodules("tests", require_init=False, root_dir=tmp_path / "proj")

    assert set(modules) == {"tests.test_thing"}


def test_discover_ancestor_conftests(tmp_path):
    """Conftests between the root and a package are found; ones inside it or elsewhere are not."""
    for rel in ("conftest.py", "backend/conftest.py", "backend/tests/conftest.py", "frontend/conftest.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    found = discover_ancestor_conftests(["backend/tests"], root_dir=tmp_path)

    assert found == {
        "conftest": str((tmp_path / "conftest.py").resolve()),
        "backend.conftest": str((tmp_path / "backend/conftest.py").resolve()),
    }


def test_discover_ancestor_conftests_without_any(tmp_path):
    (tmp_path / "pkg").mkdir()
    assert discover_ancestor_conftests(["pkg"], root_dir=tmp_path) == {}


@pytest.mark.parametrize(
    ("files", "package", "aliases"),
    [
        pytest.param(
            ["mysite/conftest.py", "mysite/mysite/__init__.py", "mysite/mysite/conftest.py"],
            "mysite/mysite",
            {},
            id="named_like_a_conftest_in_the_package",
        ),
        pytest.param(
            ["x/conftest.py", "x/x/conftest.py", "x/x/mod.py"],
            "x/x",
            {"x.conftest": "x.x.conftest"},
            id="named_like_an_alias_of_a_package_file",
        ),
        pytest.param(
            ["src/app/__init__.py", "src/app/conftest.py", "src/app/core/__init__.py"],
            "src/app/core",
            {"src.app.conftest": "app.conftest"},
            id="importable_under_its_full_path_too",
        ),
        pytest.param(
            ["src/company/conftest.py", "src/company/app/__init__.py"],
            "src/company/app",
            {"company.conftest": "src.company.conftest"},
            id="below_a_namespace_package",
        ),
    ],
)
def test_every_conftest_is_one_module_under_its_own_name(tmp_path, files, package, aliases):
    """An ancestor conftest never takes a name already in use, nor is it dropped for lack of one."""
    for rel in files:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    project = discover_project_modules(package, root_dir=tmp_path)
    names = [
        resolve_files_to_modules([rel], package, root_dir=tmp_path) for rel in files if rel.endswith("conftest.py")
    ]

    assert all(len(found) == 1 for found in names), names
    assert len({found[0] for found in names}) == len(names), names
    assert {alias: project.aliases[alias] for alias in aliases} == aliases


def test_a_conftest_keeps_its_own_name_before_another_takes_it_as_an_alias(tmp_path):
    """``x/y/conftest.py`` can be imported as ``y.conftest`` too, but that is ``y/conftest.py``'s own name."""
    for rel in ("x/y/pkg/__init__.py", "x/y/conftest.py", "y/conftest.py", "y/tests/test_a.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    project = discover_project_modules("x/y/pkg", "y/tests", root_dir=tmp_path)

    assert project.modules["y.conftest"] == str((tmp_path / "y/conftest.py").resolve())
    assert project.modules["x.y.conftest"] == str((tmp_path / "x/y/conftest.py").resolve())
    assert "y.conftest" not in project.aliases


def test_conftests_are_named_rank_by_rank():
    """A name one conftest ranks later never takes the name another ranks first."""
    first = _ConftestCandidate(["taken", "b.conftest"], ".first.conftest")
    second = _ConftestCandidate(["b.conftest"], ".second.conftest")

    named = traversal._name_conftests({"/first": first, "/second": second}, taken={"taken"})

    assert named.modules == {"b.conftest": "/second", ".first.conftest": "/first"}
    assert named.contested == {"taken": {".first.conftest"}, "b.conftest": {".first.conftest"}}


def test_two_conftests_that_can_take_one_name_are_both_modules(tmp_path):
    """``y.conftest`` can mean ``y/conftest.py`` or, with ``x/`` on ``sys.path``, ``x/y/conftest.py``:
    one takes it, the other its next name or its last resort, and contests it."""
    for rel in ("x/y/__init__.py", "x/y/conftest.py", "x/y/pkg/__init__.py", "y/conftest.py", "y/tests/test_a.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    discovered = traversal._discover_project("x/y/pkg", "y/tests", root_dir=tmp_path)

    names = {path: name for name, path in discovered.modules.items()}
    conftests = {names[str((tmp_path / rel).resolve())] for rel in ("x/y/conftest.py", "y/conftest.py")}
    owner = discovered.aliases.get("y.conftest", "y.conftest")
    assert {owner, *discovered.contested["y.conftest"]} == conftests


def test_conftests_above_the_packages_are_project_modules_and_resolve(tmp_path, caplog):
    """The graph has them as nodes, so an edit to one must resolve to its node like any module."""
    for rel in ("conftest.py", "backend/conftest.py", "backend/app/__init__.py", "suite/test_x.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    project = discover_project_modules("backend/app", "suite", root_dir=tmp_path)
    with caplog.at_level("WARNING", logger="pytest_impacted.traversal"):
        resolved = resolve_files_to_modules(["conftest.py", "backend/conftest.py"], "backend/app", "suite", tmp_path)

    assert project.modules["conftest"] == str((tmp_path / "conftest.py").resolve())
    assert project.modules["backend.conftest"] == str((tmp_path / "backend/conftest.py").resolve())
    assert resolved == ["conftest", "backend.conftest"]
    assert "could not be resolved" not in caplog.text


# --- implicit namespace sub-packages (PEP 420) -----------------------------------------


@pytest.fixture(params=["flat", "src"])
def prefix(request):
    """The package at the root, or under a non-package ``src/``: module names must not differ."""
    return "src/" if request.param == "src" else ""


def make_package(root: Path, prefix: str, *rels: str) -> None:
    for rel in ("pkg/__init__.py", *rels):
        (root / prefix / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / prefix / rel).touch()


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param(["pkg/processors/ocr.py"], {"pkg.processors.ocr"}, id="module_in_a_namespace_subpackage"),
        pytest.param(["pkg/a/b/deep.py"], {"pkg.a.b.deep"}, id="namespace_inside_a_namespace"),
        pytest.param(
            ["pkg/ns/regular/__init__.py", "pkg/ns/regular/mod.py"],
            {"pkg.ns.regular", "pkg.ns.regular.mod"},
            id="regular_package_inside_a_namespace",
        ),
        pytest.param(
            ["pkg/regular/__init__.py", "pkg/regular/ns/mod.py"],
            {"pkg.regular", "pkg.regular.ns.mod"},
            id="namespace_inside_a_regular_subpackage",
        ),
        pytest.param(["pkg/my-data/x.py", "pkg/.cache/y.py"], set(), id="non_identifier_directories_cannot_import"),
        pytest.param(["pkg/node_modules/lodash/fp.py", "pkg/__pycache__/x.py"], set(), id="never_package_directories"),
        pytest.param(
            ["pkg/tests.py", "pkg/tests/test_x.py"],
            {"pkg.tests", "pkg.tests.test_x"},
            id="shadowed_by_a_module_still_walked",
        ),
    ],
)
def test_namespace_subpackages_are_discovered(tmp_path, prefix, files, expected):
    """A directory without ``__init__.py`` inside a package still imports, so its modules are found."""
    make_package(tmp_path, prefix, *files)

    assert set(discover_submodules(f"{prefix}pkg", root_dir=tmp_path)) == {"pkg", *expected}


@pytest.mark.parametrize(
    ("files", "package", "name"),
    [
        pytest.param(["pkg/__init__.py"], "pkg", "pkg", id="flat"),
        pytest.param(["src/pkg/__init__.py"], "src/pkg", "pkg", id="src_layout"),
        pytest.param(["pkg/__init__.py", "pkg/sub/__init__.py"], "pkg/sub", "pkg.sub", id="a_subpackage"),
        pytest.param(["src/company/pkg/__init__.py"], "src/company/pkg", "pkg", id="below_a_namespace_package"),
    ],
)
def test_the_analysed_package_itself_is_a_module(tmp_path, files, package, name):
    """``pkgutil`` lists only a package's children, but its ``__init__.py`` runs on every ``import pkg``."""
    for rel in [*files, f"{package}/core.py"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    init = f"{package}/__init__.py"

    assert discover_submodules(package, root_dir=tmp_path)[name] == str((tmp_path / init).resolve())
    assert resolve_files_to_modules([init], package, root_dir=tmp_path) == [name]


def test_a_namespace_package_has_no_init_to_discover(tmp_path):
    """Without ``__init__.py`` there is no file for the package itself: only its modules are found."""
    (tmp_path / "ns").mkdir()
    (tmp_path / "ns/core.py").touch()

    assert set(discover_submodules("ns", root_dir=tmp_path)) == {"ns.core"}


def test_a_module_in_a_namespace_subpackage_resolves(tmp_path, prefix):
    """The changed-file path resolves to the name imports use, so the change reaches its dependents."""
    make_package(tmp_path, prefix, "pkg/processors/ocr.py")

    modules = resolve_files_to_modules([f"{prefix}pkg/processors/ocr.py"], f"{prefix}pkg", root_dir=tmp_path)

    assert modules == ["pkg.processors.ocr"]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_an_unreadable_directory_has_no_modules(tmp_path, prefix):
    """A container's bind-mounted data dir (mode 000 to us) must not crash discovery, as it never did."""
    make_package(tmp_path, prefix, "pkg/core.py", "pkg/pgdata/base/x.py")
    locked = tmp_path / prefix / "pkg/pgdata/base"
    locked.chmod(0)
    try:
        found = discover_submodules(f"{prefix}pkg", root_dir=tmp_path)
    finally:
        locked.chmod(0o755)

    assert set(found) == {"pkg", "pkg.core"}


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_an_unsearchable_package_directory_has_no_modules(tmp_path):
    """Checking the analysed package's own ``__init__.py`` must not raise where listing it did not."""
    make_package(tmp_path, "", "pkg/sub/__init__.py", "pkg/sub/core.py")
    locked = tmp_path / "pkg/sub"
    locked.chmod(0)
    try:
        found = discover_submodules("pkg/sub", root_dir=tmp_path)
    finally:
        locked.chmod(0o755)

    assert found == {}


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
@pytest.mark.parametrize(
    "probe",
    [
        pytest.param(lambda root: discover_submodules("pkg", root_dir=root), id="discovery_under_it"),
        pytest.param(lambda root: find_non_package_prefix("ns/data", root), id="its_package_prefix"),
        pytest.param(
            lambda root: resolve_files_to_modules(["pkg/data/gone.py"], "pkg", root_dir=root), id="a_file_in_it"
        ),
        pytest.param(
            lambda root: discover_project_modules("pkg", "pkg/data/suite", root_dir=root), id="a_tests_dir_in_it"
        ),
    ],
)
def test_a_listable_but_unsearchable_directory_raises_nothing(tmp_path, probe):
    """Mode 644, as a badly permissioned or bind-mounted data dir can be: listing works, stat raises on 3.11–3.13."""
    make_package(tmp_path, "", "pkg/data/__init__.py", "pkg/data/x.py", "ns/data/__init__.py")
    locked = [tmp_path / "pkg/data", tmp_path / "ns/data"]
    for directory in locked:
        directory.chmod(0o644)
    try:
        probe(tmp_path)
    finally:
        for directory in locked:
            directory.chmod(0o755)


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        pytest.param({"pkg/ns/loop": "."}, set(), id="to_itself"),
        pytest.param({"pkg/ns/up": "../.."}, set(), id="up_to_the_project_root"),
        pytest.param({"pkg/media": "../../external"}, set(), id="out_of_the_project"),
        pytest.param({"pkg/a/x": "../b", "pkg/b/y": "../a"}, {"pkg.a.x.mod_b", "pkg.b.y.mod_a"}, id="crossed"),
        pytest.param({"pkg/alias": "../shared"}, {"pkg.alias.mod_shared"}, id="within_the_project_is_followed"),
    ],
)
def test_symlinked_namespace_portions(tmp_path, links, expected):
    """A link is followed like the import system follows it, but never out of the project or back up it."""
    root = tmp_path / "project"
    for rel in ("pkg/__init__.py", "pkg/ns/mod.py", "pkg/a/mod_a.py", "pkg/b/mod_b.py", "shared/mod_shared.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).touch()
    (tmp_path / "external").mkdir()
    (tmp_path / "external/mod_ext.py").touch()
    for link, target in links.items():
        (root / link).symlink_to(target, target_is_directory=True)

    found = set(discover_submodules("pkg", root_dir=root))

    assert found - {"pkg", "pkg.ns.mod", "pkg.a.mod_a", "pkg.b.mod_b"} == expected


@pytest.mark.parametrize(
    ("files", "package", "tests_package", "canonical", "aliases"),
    [
        pytest.param(
            ["app/__init__.py", "app/tests/factories.py"],
            "app",
            "app/tests",
            "app.tests.factories",
            {"tests.factories"},
            id="tests_dir_inside_the_package",
        ),
        pytest.param(
            ["src/app/__init__.py", "src/app/core.py"], "src/app", None, "app.core", {"src.app.core"}, id="src_layout"
        ),
        pytest.param(
            ["src/company/app/__init__.py", "src/company/app/core.py"],
            "src/company/app",
            None,
            "app.core",
            {"company.app.core", "src.company.app.core"},
            id="package_below_a_namespace_package",
        ),
        pytest.param(
            ["src/company/app/__init__.py", "src/company/app/core.py"],
            "src/company",
            None,
            "src.company.app.core",
            {"company.app.core", "app.core"},
            id="top_level_namespace_package",
        ),
        pytest.param(
            ["src/company/core.py"],
            "src/company",
            None,
            "src.company.core",
            {"company.core"},
            id="namespace_package_with_no_regular_package_at_all",
        ),
        pytest.param(
            ["app/__init__.py", "app/sub/__init__.py", "app/sub/x.py"],
            "app/sub",
            None,
            "app.sub.x",
            set(),
            id="a_regular_package_is_never_a_sys_path_root",
        ),
        pytest.param(["src/app/__init__.py"], "src/app", None, "app", {"src.app"}, id="src_layout_package_itself"),
        pytest.param(
            ["src/company/app/__init__.py"],
            "src/company/app",
            None,
            "app",
            {"company.app", "src.company.app"},
            id="package_itself_below_a_namespace_package",
        ),
    ],
)
def test_each_file_has_one_canonical_name_and_its_other_names_as_aliases(
    tmp_path, files, package, tests_package, canonical, aliases
):
    """A file is one module, however many names reach it: two nodes would double every count."""
    for rel in files:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    path = str((tmp_path / files[-1]).resolve())

    project = discover_project_modules(package, tests_package, root_dir=tmp_path)

    assert [name for name, p in project.modules.items() if p == path] == [canonical]
    assert {alias for alias, name in project.aliases.items() if name == canonical} == aliases
    assert resolve_files_to_modules([files[-1]], package, tests_package, root_dir=tmp_path) == [canonical]


def test_a_symlinked_directory_inside_the_package_is_an_alias_not_a_second_module(tmp_path):
    """The walk finds ``pkg/real/core.py`` twice; the name not reached through the link is canonical."""
    make_package(tmp_path, "", "pkg/real/core.py")
    (tmp_path / "pkg/link").symlink_to("real", target_is_directory=True)

    project = discover_project_modules("pkg", root_dir=tmp_path)

    assert [name for name in project.modules if name.endswith("core")] == ["pkg.real.core"]
    assert project.aliases["pkg.link.core"] == "pkg.real.core"
    assert resolve_files_to_modules(["pkg/real/core.py"], "pkg", root_dir=tmp_path) == ["pkg.real.core"]


def test_an_alias_resolves_to_its_file(tmp_path):
    """Extensions may name a test by its tests-dir name; it must still resolve to the file."""
    make_package(tmp_path, "", "pkg/tests/test_x.py")

    files = resolve_modules_to_files(["tests.test_x", "pkg.tests.test_x"], "pkg", "pkg/tests", root_dir=tmp_path)

    assert files == [str((tmp_path / "pkg/tests/test_x.py").resolve())] * 2


@pytest.mark.parametrize(
    ("tests_package", "application"),
    [
        pytest.param(None, {"app/__init__.py", "app/db.py", "app/checks/test_db.py"}, id="no_tests_dir"),
        pytest.param("suite", {"app/__init__.py", "app/db.py", "app/checks/test_db.py"}, id="tests_dir_beside"),
        pytest.param("app/checks", {"app/__init__.py", "app/db.py"}, id="tests_dir_inside"),
        pytest.param("app", {"app/__init__.py", "app/db.py", "app/checks/test_db.py"}, id="tests_dir_is_the_package"),
    ],
)
def test_discover_application_files(tmp_path, tests_package, application):
    """The package walk's files, less the tests dir's — unless the tests dir holds the whole package."""
    for rel in ("app/__init__.py", "app/db.py", "app/checks/test_db.py", "suite/test_x.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    found = discover_application_files("app", tests_package, root_dir=tmp_path)

    assert found == {str((tmp_path / rel).resolve()) for rel in application}


def test_discover_application_files_ignores_a_tests_dir_holding_the_package_despite_symlinks(tmp_path):
    """The tests walk never follows a symlinked directory the package walk does; that must not matter."""
    for rel in ("app/__init__.py", "app/db.py", "libs/shared/__init__.py", "libs/shared/x.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    os.symlink(tmp_path / "libs/shared", tmp_path / "app/shared", target_is_directory=True)

    found = discover_application_files("app", "app", root_dir=tmp_path)

    assert str((tmp_path / "app/db.py").resolve()) in found


def test_import_roots_are_where_each_name_spells_its_file_from(tmp_path):
    """A module file or a package's ``__init__.py``; a name that does not spell its path roots nothing."""
    root = tmp_path.resolve()
    modules = {
        "lib": str(root / "vendor/lib/__init__.py"),
        "tools.cli": str(root / "scripts/tools/cli.py"),
        "pkg.alias.mod": str(root / "shared/mod.py"),  # reached through a symlink
        ".qa.conftest": str(root / "qa/conftest.py"),  # a last-resort name
    }

    roots = import_roots(["app"], modules, {"x.lib": "lib"}, root)

    assert roots == [root, root / "vendor", root / "scripts"]


def test_a_name_spelling_a_file_outside_the_rootdir_roots_nothing(tmp_path):
    """``pkg/ext -> <outside>/pkg/ext``: the file's real path spells ``pkg.ext.mod`` from a directory
    no part of the project."""
    root = (tmp_path / "project").resolve()
    root.mkdir()
    modules = {"pkg.ext.mod": str(tmp_path.resolve() / "outside/pkg/ext/mod.py")}

    assert import_roots(["pkg"], modules, {}, root) == [root]


@pytest.mark.parametrize(
    ("packages", "above"),
    [
        pytest.param(["src/app", "tests"], ["src"], id="above_the_package"),
        pytest.param(["a/b/app"], ["a", "a/b"], id="every_directory_above_it"),
        pytest.param(["app", "backend/qa/suite"], ["backend", "backend/qa"], id="above_the_tests_dir"),
        pytest.param(["app", "app/sub/tests"], [], id="not_inside_the_package"),
        pytest.param(["src/app", "src/app/tests"], ["src"], id="above_a_package_holding_the_tests_dir"),
    ],
)
def test_every_directory_above_an_analysed_directory_is_an_import_root_package_or_not(tmp_path, packages, above):
    root = tmp_path.resolve()
    for directory in ("src", "a", "a/b", "app", "app/sub", "backend", "backend/qa"):
        write_files(root, {f"{directory}/__init__.py": ""})

    assert import_roots(packages, {}, {}, root) == [root, *(root / directory for directory in above)]
    assert split_import_roots(packages, {}, {}, root) == ([root], [root / directory for directory in above])


def test_a_directory_above_the_package_that_a_name_is_rooted_at_is_implied_not_assumed(tmp_path):
    """``src/`` without an ``__init__.py`` is where ``app.core`` spells its file from: no guess."""
    root = tmp_path.resolve()
    write_files(root, {"lib/__init__.py": ""})
    modules = {"app.core": str(root / "src/app/core.py"), "lib.pkg.x": str(root / "lib/pkg/x.py")}

    implied, assumed = split_import_roots(["src/app", "lib/pkg"], modules, {}, root)

    assert implied == [root, root / "src"]
    assert assumed == [root / "lib"]


def test_import_roots_of_nothing_analysed_are_the_rootdir(tmp_path):
    assert import_roots([], {}, {}, tmp_path) == [tmp_path.resolve()]
