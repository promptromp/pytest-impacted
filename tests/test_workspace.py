"""Unit tests for the workspace (monorepo) discovery module."""

from pathlib import PurePosixPath

from pytest_impacted.workspace import (
    PackageInfo,
    discover_packages,
    load_package,
    normalize_package_name,
)


def _write_pyproject(pkg_dir, text):
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "pyproject.toml").write_text(text)


def _make_module(pkg_dir, module_rel):
    module_dir = pkg_dir / module_rel
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "__init__.py").write_text("")


class TestNormalizePackageName:
    def test_pep503_normalization(self):
        assert normalize_package_name("My_Package.Name") == "my-package-name"

    def test_already_normalized(self):
        assert normalize_package_name("pkg-alpha") == "pkg-alpha"


class TestLoadPackage:
    def test_explicit_config_is_used_verbatim(self, tmp_path):
        pkg = tmp_path / "libs" / "beta"
        _write_pyproject(
            pkg,
            '[project]\nname = "pkg-beta"\nversion = "0.1.0"\ndependencies = ["pkg-alpha>=1.0", "click"]\n'
            '[tool.pytest.ini_options]\nimpacted_module = "pkg_beta"\nimpacted_tests_dir = "tests"\n',
        )
        info = load_package(pkg, tmp_path)
        assert info == PackageInfo(
            name="pkg-beta",
            path=PurePosixPath("libs/beta"),
            module="pkg_beta",
            tests_dir="tests",
            requirements=frozenset({"pkg-alpha", "click"}),
        )

    def test_infers_src_layout_module_and_tests_dir(self, tmp_path):
        pkg = tmp_path / "libs" / "alpha"
        _write_pyproject(pkg, '[project]\nname = "pkg-alpha"\nversion = "0.1.0"\n')
        _make_module(pkg, "src/pkg_alpha")
        (pkg / "tests").mkdir()
        info = load_package(pkg, tmp_path)
        assert info is not None
        assert info.module == "src/pkg_alpha"
        assert info.tests_dir == "tests"

    def test_infers_flat_layout_without_tests_dir(self, tmp_path):
        pkg = tmp_path / "libs" / "alpha"
        _write_pyproject(pkg, '[project]\nname = "pkg-alpha"\nversion = "0.1.0"\n')
        _make_module(pkg, "pkg_alpha")
        info = load_package(pkg, tmp_path)
        assert info is not None
        assert info.module == "pkg_alpha"
        assert info.tests_dir is None

    def test_root_package_gets_dot_path(self, tmp_path):
        _write_pyproject(tmp_path, '[project]\nname = "root-pkg"\nversion = "0.1.0"\n')
        _make_module(tmp_path, "root_pkg")
        info = load_package(tmp_path, tmp_path)
        assert info is not None
        assert info.path == PurePosixPath(".")

    def test_missing_project_name_returns_none(self, tmp_path):
        _write_pyproject(tmp_path, '[tool.uv.workspace]\nmembers = ["libs/*"]\n')
        assert load_package(tmp_path, tmp_path) is None

    def test_unresolvable_module_returns_none_with_warning(self, tmp_path, caplog):
        pkg = tmp_path / "libs" / "ghost"
        _write_pyproject(pkg, '[project]\nname = "pkg-ghost"\nversion = "0.1.0"\n')
        with caplog.at_level("WARNING", logger="pytest_impacted.workspace"):
            assert load_package(pkg, tmp_path) is None
        assert "pkg-ghost" in caplog.text

    def test_optional_dependencies_and_bad_requirements(self, tmp_path):
        pkg = tmp_path / "libs" / "beta"
        _write_pyproject(
            pkg,
            '[project]\nname = "pkg-beta"\nversion = "0.1.0"\ndependencies = ["!!!not-a-req!!!"]\n'
            '[project.optional-dependencies]\nextra = ["pkg-alpha[fast]>=1.0"]\n',
        )
        _make_module(pkg, "pkg_beta")
        info = load_package(pkg, tmp_path)
        assert info is not None
        assert info.requirements == frozenset({"pkg-alpha"})


class TestDiscoverPackages:
    def _make_package(self, root, rel, name, layout="flat"):
        pkg = root / rel
        _write_pyproject(pkg, f'[project]\nname = "{name}"\nversion = "0.1.0"\n')
        module_dir = name.replace("-", "_")
        _make_module(pkg, f"src/{module_dir}" if layout == "src" else module_dir)
        return pkg

    def test_uv_workspace_members_and_exclude(self, tmp_path):
        _write_pyproject(tmp_path, '[tool.uv.workspace]\nmembers = ["libs/*"]\nexclude = ["libs/skipme"]\n')
        self._make_package(tmp_path, "libs/alpha", "pkg-alpha", layout="src")
        self._make_package(tmp_path, "libs/beta", "pkg-beta")
        self._make_package(tmp_path, "libs/skipme", "pkg-skipme")
        self._make_package(tmp_path, "unlisted", "pkg-unlisted")  # not in members -> ignored
        packages = discover_packages(tmp_path)
        assert [p.name for p in packages] == ["pkg-alpha", "pkg-beta"]

    def test_uv_workspace_includes_root_when_it_has_project(self, tmp_path):
        _write_pyproject(
            tmp_path,
            '[project]\nname = "root-pkg"\nversion = "0.1.0"\n[tool.uv.workspace]\nmembers = ["libs/*"]\n',
        )
        _make_module(tmp_path, "root_pkg")
        self._make_package(tmp_path, "libs/alpha", "pkg-alpha")
        packages = discover_packages(tmp_path)
        assert {p.name for p in packages} == {"root-pkg", "pkg-alpha"}

    def test_scan_fallback_finds_nested_packages_and_prunes(self, tmp_path):
        self._make_package(tmp_path, "libs/alpha", "pkg-alpha")
        self._make_package(tmp_path, "services/deep/gamma", "pkg-gamma")
        self._make_package(tmp_path, ".hidden/secret", "pkg-secret")
        self._make_package(tmp_path, "node_modules/junk", "pkg-junk")
        packages = discover_packages(tmp_path)
        assert [p.name for p in packages] == ["pkg-alpha", "pkg-gamma"]

    def test_duplicate_package_names_keep_first(self, tmp_path, caplog):
        self._make_package(tmp_path, "a/dupe", "pkg-dupe")
        self._make_package(tmp_path, "b/dupe", "pkg-dupe")
        with caplog.at_level("WARNING", logger="pytest_impacted.workspace"):
            packages = discover_packages(tmp_path)
        assert [str(p.path) for p in packages] == ["a/dupe"]
        assert "pkg-dupe" in caplog.text
