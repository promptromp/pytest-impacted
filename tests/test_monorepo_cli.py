"""Tests for the impacted-packages monorepo CLI."""

import os
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from pytest_impacted.cli import (
    _all_tests_for_package,
    _analyze_direct_package,
    _is_test_file_module,
    _rebase_paths,
)
from pytest_impacted.git import GitMode
from pytest_impacted.workspace import PackageInfo


EXAMPLE_MONOREPO = Path(__file__).parent.parent / "examples" / "monorepo"

PKG_ALPHA = PackageInfo(
    name="pkg-alpha",
    path=PurePosixPath("libs/pkg-alpha"),
    module="src/pkg_alpha",
    tests_dir="tests",
)
PKG_BETA = PackageInfo(
    name="pkg-beta",
    path=PurePosixPath("libs/pkg-beta"),
    module="pkg_beta",
    tests_dir="tests",
    requirements=frozenset({"pkg-alpha"}),
)


class TestHelpers:
    def test_is_test_file_module(self):
        assert _is_test_file_module("tests.test_core")
        assert _is_test_file_module("pkg.core_test")
        assert not _is_test_file_module("tests.conftest")
        assert not _is_test_file_module("pkg_alpha.core")

    def test_rebase_paths(self, tmp_path):
        (tmp_path / "a").mkdir()
        target = tmp_path / "a" / "t.py"
        target.write_text("")
        assert _rebase_paths([str(target)], tmp_path) == ["a/t.py"]


class TestAllTestsForPackage:
    def test_enumerates_all_test_files_src_layout(self):
        result = _all_tests_for_package(PKG_ALPHA, EXAMPLE_MONOREPO)
        assert result == [
            "libs/pkg-alpha/tests/test_core.py",
            "libs/pkg-alpha/tests/test_util.py",
        ]

    def test_enumerates_all_test_files_flat_layout(self):
        result = _all_tests_for_package(PKG_BETA, EXAMPLE_MONOREPO)
        assert result == ["libs/pkg-beta/tests/test_service.py"]

    def test_cwd_is_restored(self):
        before = os.getcwd()
        _all_tests_for_package(PKG_ALPHA, EXAMPLE_MONOREPO)
        assert os.getcwd() == before


class TestAnalyzeDirectPackage:
    def test_rebases_results_and_passes_package_config(self):
        expected_abs = str((EXAMPLE_MONOREPO / "libs/pkg-alpha/tests/test_core.py").resolve())
        with patch("pytest_impacted.cli.get_impacted_tests", return_value=[expected_abs]) as mock_git:
            result = _analyze_direct_package(
                PKG_ALPHA,
                root=EXAMPLE_MONOREPO,
                git_mode=GitMode.UNSTAGED,
                base_branch="main",
                watch_dep_files=True,
                disable_ext=(),
                ext_config={},
            )
        assert result == ["libs/pkg-alpha/tests/test_core.py"]
        kwargs = mock_git.call_args.kwargs
        assert kwargs["ns_module"] == "src/pkg_alpha"
        assert kwargs["tests_dir"] == "tests"
        assert kwargs["root_dir"] == Path(".")

    def test_none_result_is_empty_list(self):
        with patch("pytest_impacted.cli.get_impacted_tests", return_value=None):
            result = _analyze_direct_package(
                PKG_ALPHA,
                root=EXAMPLE_MONOREPO,
                git_mode=GitMode.UNSTAGED,
                base_branch="main",
                watch_dep_files=True,
                disable_ext=(),
                ext_config={},
            )
        assert result == []
