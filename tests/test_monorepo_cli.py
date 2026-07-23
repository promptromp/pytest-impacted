"""Tests for the impacted-packages monorepo CLI."""

import json
import os
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from click.testing import CliRunner

from pytest_impacted.cli import (
    _all_tests_for_package,
    _analyze_direct_package,
    _is_test_file_module,
    _rebase_paths,
    impacted_packages_cli,
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


class TestImpactedPackagesCli:
    def _invoke(self, args, changed_files):
        runner = CliRunner()
        with (
            patch("pytest_impacted.cli.find_impacted_files_in_repo", return_value=changed_files),
            patch(
                "pytest_impacted.cli._analyze_direct_package",
                return_value=["libs/pkg-alpha/tests/test_core.py"],
            ),
        ):
            return runner.invoke(impacted_packages_cli, ["--root-dir", str(EXAMPLE_MONOREPO), *args])

    def test_json_output_direct_and_dependency(self):
        result = self._invoke(["--format", "json"], ["libs/pkg-alpha/src/pkg_alpha/core.py"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "packages": [
                {
                    "name": "pkg-alpha",
                    "path": "libs/pkg-alpha",
                    "reason": "direct",
                    "impacted_tests": ["libs/pkg-alpha/tests/test_core.py"],
                },
                {
                    "name": "pkg-beta",
                    "path": "libs/pkg-beta",
                    "reason": "dependency",
                    "impacted_tests": ["libs/pkg-beta/tests/test_service.py"],
                },
            ]
        }

    def test_text_output_groups_by_package(self):
        result = self._invoke([], ["libs/pkg-alpha/src/pkg_alpha/core.py"])
        assert result.exit_code == 0, result.output
        assert "== pkg-alpha (libs/pkg-alpha) [direct]" in result.output
        assert "libs/pkg-alpha/tests/test_core.py" in result.output
        assert "== pkg-beta (libs/pkg-beta) [dependency]" in result.output
        assert "libs/pkg-beta/tests/test_service.py" in result.output

    def test_root_dep_file_marks_all_packages(self):
        result = self._invoke(["--format", "json"], ["uv.lock"])
        data = json.loads(result.stdout)
        assert [(p["name"], p["reason"]) for p in data["packages"]] == [
            ("pkg-alpha", "dep-files"),
            ("pkg-beta", "dep-files"),
        ]
        # dep-files impact selects ALL tests, not just the direct-analysis result
        assert data["packages"][0]["impacted_tests"] == [
            "libs/pkg-alpha/tests/test_core.py",
            "libs/pkg-alpha/tests/test_util.py",
        ]

    def test_no_changes_yields_empty_result(self):
        result = self._invoke(["--format", "json"], [])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"packages": []}

    def test_no_packages_discovered_is_an_error(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(impacted_packages_cli, ["--root-dir", str(tmp_path)])
        assert result.exit_code != 0

    def test_failing_package_does_not_sink_the_others(self):
        runner = CliRunner()
        with (
            patch(
                "pytest_impacted.cli.find_impacted_files_in_repo",
                return_value=["libs/pkg-alpha/src/pkg_alpha/core.py"],
            ),
            patch("pytest_impacted.cli._analyze_direct_package", side_effect=RuntimeError("boom")),
        ):
            result = runner.invoke(impacted_packages_cli, ["--root-dir", str(EXAMPLE_MONOREPO), "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        by_name = {p["name"]: p for p in data["packages"]}
        assert by_name["pkg-alpha"]["error"] == "boom"
        assert by_name["pkg-beta"]["impacted_tests"] == ["libs/pkg-beta/tests/test_service.py"]
