"""Unit tests for the git module."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from git import GitCommandError, Repo

from pytest_impacted import git
from pytest_impacted.git import find_repo, normalize_git_paths


def name_status_z(*records: tuple[str, ...]) -> str:
    """Render ``(status, path[, path])`` records as ``git diff --name-status -z`` output."""
    return "".join(f"{field}\0" for record in records for field in record)


class DummyRepo:
    """Stand-in for :class:`git.Repo` exposing only what the git module calls.

    ``staged`` / ``unstaged`` are ``git diff --name-status -z`` outputs for the
    index-vs-HEAD and worktree-vs-index views used by UNSTAGED mode;
    ``diff_branch_result`` is the same format for BRANCH mode. Build them with
    :func:`name_status_z`.
    """

    def __init__(
        self,
        staged="",
        unstaged="",
        untracked_files=(),
        diff_branch_result="",
        current_branch="feature/some-feature-branch",
        working_tree_dir=None,
    ):
        self.bare = False
        self.git = MagicMock()
        self.git.diff = MagicMock(side_effect=self._diff)
        self.git.ls_files = MagicMock(return_value="".join(f"{path}\0" for path in untracked_files))
        self.head = MagicMock()
        self.head.reference = current_branch
        self.working_tree_dir = working_tree_dir or str(Path.cwd())
        self._staged, self._unstaged, self._branch = staged, unstaged, diff_branch_result

    def _diff(self, *args):
        if "--cached" in args:
            return self._staged
        if "--end-of-options" in args:
            return self._branch
        return self._unstaged


@patch("pytest_impacted.git.GIT_AVAILABLE", False)
def test_find_impacted_files_in_repo_git_not_available():
    """Test find_impacted_files_in_repo when git is not available."""
    with pytest.warns(UserWarning, match="Git functionality is disabled"):
        result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result is None


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_clean(mock_repo):
    mock_repo.return_value = DummyRepo()
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result is None


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_dirty(mock_repo):
    mock_repo.return_value = DummyRepo(
        staged=name_status_z(("A", "file2.py")), unstaged=name_status_z(("M", "file1.py"))
    )
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result == ["file1.py", "file2.py"]
    assert mock_repo.return_value.git.diff.call_args_list == [
        call("--name-status", "-z", "--find-renames", "--cached"),
        call("--name-status", "-z", "--find-renames"),
    ]


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_dirty_with_untracked_files(mock_repo):
    mock_repo.return_value = DummyRepo(
        staged=name_status_z(("A", "file2.py")),
        unstaged=name_status_z(("M", "file1.py")),
        untracked_files=["file3.py", "file4.py"],
    )
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result == ["file1.py", "file2.py", "file3.py", "file4.py"]


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_only_deletions(mock_repo):
    """Deletions count: a removed conftest.py or lockfile still impacts tests."""
    mock_repo.return_value = DummyRepo(
        staged=name_status_z(("D", "gone.py")), unstaged=name_status_z(("D", "also_gone.py"))
    )
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result == ["also_gone.py", "gone.py"]


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_branch(mock_repo):
    diff_branch_result = name_status_z(("M", "file3.py"), ("A", "file4.py"))
    mock_repo.return_value = DummyRepo(diff_branch_result=diff_branch_result)
    result = git.find_impacted_files_in_repo(".", git.GitMode.BRANCH, "main")
    assert set(result) == {"file3.py", "file4.py"}


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_branch_none(mock_repo):
    diff_branch_result = ""
    mock_repo.return_value = DummyRepo(diff_branch_result=diff_branch_result)
    result = git.find_impacted_files_in_repo(".", git.GitMode.BRANCH, "main")
    assert result is None


def test_find_impacted_files_in_repo_branch_no_base_branch():
    """Test find_impacted_files_in_repo with BRANCH mode and no base_branch."""
    with pytest.raises(
        ValueError,
        match="Base branch is required for running in BRANCH git mode",
    ):
        git.find_impacted_files_in_repo(".", git.GitMode.BRANCH, None)


def test_find_impacted_files_in_repo_invalid_mode():
    """Test find_impacted_files_in_repo with an invalid git_mode."""
    with pytest.raises(ValueError, match="Invalid git mode: invalid_mode"):
        git.find_impacted_files_in_repo(".", "invalid_mode", "main")


def test_without_nones():
    """Test the without_nones utility function."""
    assert git.without_nones([1, None, 2, 3, None]) == [1, 2, 3]
    assert git.without_nones([None, None, None]) == []
    assert git.without_nones([1, 2, 3]) == [1, 2, 3]
    assert git.without_nones([]) == []


@pytest.mark.parametrize(
    "input_status,expected_status",
    [
        ("A", git.GitStatus.ADDED),
        ("M", git.GitStatus.MODIFIED),
        ("D", git.GitStatus.DELETED),
        ("R100", git.GitStatus.RENAMED),
        ("R75", git.GitStatus.RENAMED),
        ("C100", git.GitStatus.COPIED),
        ("C85", git.GitStatus.COPIED),
    ],
)
def test_git_status_from_git_diff_name_status(input_status, expected_status):
    """Test GitStatus.from_git_diff_name_status with various status codes."""
    assert git.GitStatus.from_git_diff_name_status(input_status) == expected_status


def test_changeset_from_git_diff_name_status_with_scores():
    """Test ChangeSet.from_git_diff_name_status_output with rename and copy scores."""
    diff_output = name_status_z(
        ("M", "modified.py"),
        ("R100", "old_name.py", "new_name.py"),
        ("C85", "original.py", "copy.py"),
        ("D", "deleted.py"),
    )

    change_set = git.ChangeSet.from_git_diff_name_status_output(diff_output)
    changes = change_set.changes

    assert len(changes) == 4

    # Verify modified file
    assert changes[0].status == git.GitStatus.MODIFIED
    assert changes[0].name == "modified.py"

    # Verify renamed file
    assert changes[1].status == git.GitStatus.RENAMED
    assert changes[1].a_path == "old_name.py"
    assert changes[1].b_path == "new_name.py"

    # Verify copied file
    assert changes[2].status == git.GitStatus.COPIED
    assert changes[2].a_path == "original.py"
    assert changes[2].b_path == "copy.py"

    # Verify deleted file
    assert changes[3].status == git.GitStatus.DELETED
    assert changes[3].name == "deleted.py"


def test_deleted_files_from_diff():
    """Test deleted_files_from_diff function."""
    changes = [
        git.Change(a_path="deleted1.py", status=git.GitStatus.DELETED),
        git.Change(a_path="modified.py", status=git.GitStatus.MODIFIED),
        git.Change(a_path="deleted2.py", status=git.GitStatus.DELETED),
        git.Change(a_path="added.py", status=git.GitStatus.ADDED),
    ]
    change_set = git.ChangeSet(changes)

    deleted_files = git.deleted_files_from_diff(change_set)
    assert set(deleted_files) == {"deleted1.py", "deleted2.py"}


def test_change_class():
    """Test the Change class."""
    # Test with all parameters
    change = git.Change(a_path="file.py", b_path="new_file.py", status=git.GitStatus.RENAMED)
    assert change.a_path == "file.py"
    assert change.b_path == "new_file.py"
    assert change.status == git.GitStatus.RENAMED
    assert change.name == "file.py"

    # Test with only b_path (new file)
    change_new = git.Change(a_path=None, b_path="new_file.py", status=git.GitStatus.ADDED)
    assert change_new.name == "new_file.py"

    # Test string representation
    change_str = git.Change(a_path="file.py", status=git.GitStatus.MODIFIED)
    assert str(change_str) == "M\tfile.py"


def test_changeset_class():
    """Test the ChangeSet class."""
    changes = [
        git.Change(a_path="file1.py", status=git.GitStatus.MODIFIED),
        git.Change(a_path="file2.py", status=git.GitStatus.ADDED),
    ]
    change_set = git.ChangeSet(changes)

    assert len(change_set.changes) == 2
    assert "M\tfile1.py" in str(change_set)
    assert "A\tfile2.py" in str(change_set)


def test_changeset_from_git_diff_name_status_output():
    """Test ChangeSet.from_git_diff_name_status_output method."""
    diff_output = name_status_z(("M", "modified.py"), ("A", "added.py"), ("D", "deleted.py"))

    change_set = git.ChangeSet.from_git_diff_name_status_output(diff_output)

    assert len(change_set.changes) == 3
    assert change_set.changes[0].status == git.GitStatus.MODIFIED
    assert change_set.changes[1].status == git.GitStatus.ADDED
    assert change_set.changes[2].status == git.GitStatus.DELETED


def test_changeset_from_git_diff_name_status_output_empty():
    """Test ChangeSet.from_git_diff_name_status_output with empty input."""
    change_set = git.ChangeSet.from_git_diff_name_status_output("")
    assert len(change_set.changes) == 0


def test_changeset_from_git_diff_name_status_output_keeps_special_characters():
    """``-z`` output carries paths verbatim: no C-quoting of non-ASCII, tabs, quotes or backslashes."""
    output = name_status_z(("M", "pkg/café.py"), ("A", 'weird\t"name"\\.py'), ("R100", "sp ace.py", "naïve.py"))
    change_set = git.ChangeSet.from_git_diff_name_status_output(output)
    assert [(c.a_path, c.b_path) for c in change_set.changes] == [
        ("pkg/café.py", None),
        ('weird\t"name"\\.py', None),
        ("sp ace.py", "naïve.py"),
    ]


def test_changeset_from_git_diff_name_status_output_truncated_rename():
    """A rename record missing its destination does not crash the parser."""
    change_set = git.ChangeSet.from_git_diff_name_status_output("R100\0old.py\0")
    assert (change_set.changes[0].a_path, change_set.changes[0].b_path) == ("old.py", None)


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_unstaged_mode_clean_repo(mock_repo):
    """Test impacted_files_for_unstaged_mode with clean repo."""
    repo = DummyRepo()
    result = git.impacted_files_for_unstaged_mode(repo)
    assert result == []


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_unstaged_mode_only_untracked_files(mock_repo):
    """Untracked files should be detected even when no tracked files are modified."""
    repo = DummyRepo(untracked_files=["tests/test_new.py"])
    result = git.impacted_files_for_unstaged_mode(repo)
    assert result == ["tests/test_new.py"]


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_unstaged_mode_with_deleted_files(mock_repo):
    """Test impacted_files_for_unstaged_mode with deleted files."""
    repo = DummyRepo(unstaged=name_status_z(("M", "file1.py"), ("D", "deleted.py")))
    result = git.impacted_files_for_unstaged_mode(repo)

    # Deletions count too: a removed conftest.py or lockfile still impacts tests.
    assert result == ["file1.py", "deleted.py"]


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_branch_mode_with_deleted_files(mock_repo):
    """Test impacted_files_for_branch_mode with deleted files."""
    diff_output = name_status_z(("M", "modified.py"), ("D", "deleted.py"), ("A", "added.py"))

    repo = DummyRepo(diff_branch_result=diff_output)
    result = git.impacted_files_for_branch_mode(repo, "main")

    # Deletions count too: a removed conftest.py or lockfile still impacts tests.
    assert result == ["modified.py", "deleted.py", "added.py"]


def test_git_status_enum_values():
    """Test all GitStatus enum values."""
    assert git.GitStatus.ADDED.value == "A"
    assert git.GitStatus.COPIED.value == "C"
    assert git.GitStatus.DELETED.value == "D"
    assert git.GitStatus.MODIFIED.value == "M"
    assert git.GitStatus.RENAMED.value == "R"
    assert git.GitStatus.TYPE_CHANGE.value == "T"
    assert git.GitStatus.UNMERGED.value == "U"
    assert git.GitStatus.UNKNOWN.value == "X"
    assert git.GitStatus.PAIRING_BROKEN.value == "B"


def test_git_mode_enum_values():
    """Test GitMode enum values."""
    assert git.GitMode.UNSTAGED.value == "unstaged"
    assert git.GitMode.BRANCH.value == "branch"


@patch("pytest_impacted.git.GIT_AVAILABLE", True)
@patch("pytest_impacted.git.warnings.warn")
def test_git_available_warning_not_called(mock_warn):
    """Test that warning is not called when git is available."""
    # Import should not trigger warning when GIT_AVAILABLE is True
    mock_warn.assert_not_called()


def test_git_unavailable_warning():
    """Test that warning is triggered when GitPython is not available."""
    with (
        patch("pytest_impacted.git.GIT_AVAILABLE", False),
        pytest.warns(UserWarning, match="Git functionality is disabled"),
    ):
        git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)


def test_git_status_from_git_diff_name_status_edge_cases():
    """Test GitStatus.from_git_diff_name_status with edge cases."""
    # Test copy with non-digit after C - this should raise ValueError as "CX" is not a valid status
    with pytest.raises(ValueError):
        git.GitStatus.from_git_diff_name_status("CX")

    # Test rename with non-digit after R - this should raise ValueError as "RX" is not a valid status
    with pytest.raises(ValueError):
        git.GitStatus.from_git_diff_name_status("RX")

    # Test other valid status codes
    assert git.GitStatus.from_git_diff_name_status("T") == git.GitStatus.TYPE_CHANGE
    assert git.GitStatus.from_git_diff_name_status("U") == git.GitStatus.UNMERGED
    assert git.GitStatus.from_git_diff_name_status("X") == git.GitStatus.UNKNOWN
    assert git.GitStatus.from_git_diff_name_status("B") == git.GitStatus.PAIRING_BROKEN


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_with_path_object(mock_repo):
    """Test find_impacted_files_in_repo with Path object instead of string."""
    mock_repo.return_value = DummyRepo(unstaged=name_status_z(("M", "file1.py")))

    result = git.find_impacted_files_in_repo(Path("."), git.GitMode.UNSTAGED, None)
    assert result == ["file1.py"]
    # find_repo passes search_parent_directories=True
    mock_repo.assert_called_once_with(path=Path("."), search_parent_directories=True)


def test_change_name_property_with_both_paths():
    """Test Change.name property when both a_path and b_path are present."""
    change = git.Change(a_path="old_file.py", b_path="new_file.py", status=git.GitStatus.RENAMED)
    # When both paths are present, name should return a_path
    assert change.name == "old_file.py"


def test_change_name_property_with_none_paths():
    """Test Change.name property when both paths are None."""
    change = git.Change(a_path=None, b_path=None, status=git.GitStatus.UNKNOWN)
    assert change.name is None


def test_git_module_docstring():
    """Test that the git module has the expected docstring."""
    assert git.__doc__ == "Git related functions."


def test_change_str_with_none_status():
    """Test Change.__str__ method with None status."""
    change = git.Change(a_path="file.py", status=None)
    assert str(change) == "None\tfile.py"


def test_changeset_str_empty():
    """Test ChangeSet.__str__ method with empty changes."""
    change_set = git.ChangeSet([])
    assert str(change_set) == ""


def test_changeset_str_multiple_changes():
    """Test ChangeSet.__str__ method with multiple changes."""
    changes = [
        git.Change(a_path="file1.py", status=git.GitStatus.MODIFIED),
        git.Change(a_path="file2.py", status=git.GitStatus.ADDED),
    ]
    change_set = git.ChangeSet(changes)
    expected = "M\tfile1.py\nA\tfile2.py"
    assert str(change_set) == expected


@pytest.mark.parametrize(
    ("status", "impactful"),
    [
        (git.GitStatus.MODIFIED, True),
        (git.GitStatus.ADDED, True),
        (git.GitStatus.RENAMED, True),
        (git.GitStatus.COPIED, True),
        (git.GitStatus.TYPE_CHANGE, True),
        (git.GitStatus.UNMERGED, True),
        (git.GitStatus.DELETED, True),
        (git.GitStatus.UNKNOWN, False),
        (git.GitStatus.PAIRING_BROKEN, False),
    ],
)
def test_impactful_statuses(status, impactful):
    """Every real change counts, deletions included; only git's "cannot say" statuses are excluded."""
    change_set = git.ChangeSet([git.Change(a_path="a.py", status=status)])
    assert (git._impactful_paths(change_set) == ["a.py"]) is impactful


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_branch_mode_with_none_names(mock_repo):
    """Test impacted_files_for_branch_mode with files that have None names."""
    # Create diff output that results in None names
    diff_output = name_status_z(("M", "file1.py"), ("D", ""))  # Second record has an empty path

    repo = DummyRepo(diff_branch_result=diff_output)
    result = git.impacted_files_for_branch_mode(repo, "main")

    # Should filter out None/empty names
    assert result == ["file1.py"]


def test_without_nones_with_mixed_types():
    """Test without_nones with mixed types including None."""
    items = [1, None, "string", None, [], {}, None]
    result = git.without_nones(items)
    assert result == [1, "string", [], {}]


def test_git_status_from_git_diff_name_status_copy_and_rename():
    """Test GitStatus.from_git_diff_name_status with copy and rename scores."""
    # Test copy with score
    assert git.GitStatus.from_git_diff_name_status("C100") == git.GitStatus.COPIED
    assert git.GitStatus.from_git_diff_name_status("C85") == git.GitStatus.COPIED

    # Test rename with score
    assert git.GitStatus.from_git_diff_name_status("R100") == git.GitStatus.RENAMED
    assert git.GitStatus.from_git_diff_name_status("R75") == git.GitStatus.RENAMED


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_unstaged_mode_with_renamed_files(mock_repo):
    """Test impacted_files_for_unstaged_mode includes both paths for renamed files."""
    repo = DummyRepo(
        staged=name_status_z(("R100", "old_name.py", "new_name.py")), unstaged=name_status_z(("M", "file1.py"))
    )
    result = git.impacted_files_for_unstaged_mode(repo)

    assert set(result) == {"old_name.py", "new_name.py", "file1.py"}


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_branch_mode_with_renamed_files(mock_repo):
    """Test impacted_files_for_branch_mode includes both paths for renamed files."""
    diff_output = name_status_z(("R100", "old_name.py", "new_name.py"), ("M", "modified.py"))
    repo = DummyRepo(diff_branch_result=diff_output)
    result = git.impacted_files_for_branch_mode(repo, "main")

    assert set(result) == {"old_name.py", "new_name.py", "modified.py"}


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_branch_mode_with_copied_files(mock_repo):
    """Test impacted_files_for_branch_mode includes both paths for copied files."""
    diff_output = name_status_z(("C85", "original.py", "copy.py"), ("A", "new_file.py"))
    repo = DummyRepo(diff_branch_result=diff_output)
    result = git.impacted_files_for_branch_mode(repo, "main")

    assert set(result) == {"original.py", "copy.py", "new_file.py"}


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_branch_mode_detached_head(mock_repo):
    """Test impacted_files_for_branch_mode handles detached HEAD (common in CI)."""
    diff_output = name_status_z(("M", "file1.py"))
    repo = DummyRepo(diff_branch_result=diff_output)

    # Simulate detached HEAD: accessing head.reference raises TypeError
    def _raise_type_error(self):
        raise TypeError("HEAD is a detached symbolic reference")

    type(repo.head).reference = property(_raise_type_error)
    repo.head.commit = "abc123"

    result = git.impacted_files_for_branch_mode(repo, "main")

    assert result == ["file1.py"]
    # Verify git.diff was called with the commit hash fallback
    repo.git.diff.assert_called_once_with("--name-status", "-z", "--find-renames", "--end-of-options", "main", "abc123")


# --- Tests for validate_rev (git option-injection guard) ---


@pytest.mark.parametrize("rev", ["main", "origin/main", "HEAD~3", "abc123", "release-1.0"])
def test_validate_rev_accepts_legitimate_refs(rev):
    """validate_rev returns ordinary ref names unchanged."""
    assert git.validate_rev(rev) == rev


@pytest.mark.parametrize("rev", ["--output=/tmp/pwned", "--upload-pack=touch /tmp/x", "-o", "--"])
def test_validate_rev_rejects_option_like_refs(rev):
    """validate_rev rejects values git would parse as options rather than revisions."""
    with pytest.raises(git.InvalidGitRefError):
        git.validate_rev(rev)


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_branch_mode_rejects_option_like_base_branch(mock_repo):
    """An option-like base branch never reaches the git CLI."""
    repo = DummyRepo(diff_branch_result=name_status_z(("M", "file1.py")))

    with pytest.raises(git.InvalidGitRefError):
        git.impacted_files_for_branch_mode(repo, "--output=/tmp/pwned")

    repo.git.diff.assert_not_called()


# --- Tests for rev_args (the shared revision-argument convention) ---


def test_rev_args_prefixes_end_of_options():
    """Revisions are passed as operands, so git cannot parse them as options."""
    assert git.rev_args("main", "HEAD") == ["--end-of-options", "main", "HEAD"]


def test_rev_args_stringifies_non_str_revisions():
    """Callers may pass GitPython Commit/Reference objects rather than plain strings."""

    class Commit:
        def __str__(self):
            return "abc123"

    assert git.rev_args(Commit()) == ["--end-of-options", "abc123"]


@pytest.mark.parametrize("rev", ["--upload-pack=touch /tmp/x", "-o"])
def test_rev_args_rejects_option_like_revisions(rev):
    """validate_rev is applied to every revision, not just the first."""
    with pytest.raises(git.InvalidGitRefError):
        git.rev_args("main", rev)


def test_rev_args_validates_every_revision_including_the_current_ref():
    """The second operand is guarded too — the guard is uniform, not per-call-site."""
    repo = DummyRepo(diff_branch_result=name_status_z(("M", "file1.py")))
    repo.head.reference = "--output=/tmp/pwned"

    with pytest.raises(git.InvalidGitRefError):
        git.impacted_files_for_branch_mode(repo, "main")

    repo.git.diff.assert_not_called()


# --- Tests for find_repo and normalize_git_paths (monorepo support) ---


@patch("pytest_impacted.git.Repo")
def test_find_repo_uses_search_parent_directories(mock_repo):
    """find_repo passes search_parent_directories=True to GitPython."""
    find_repo("/some/path")
    mock_repo.assert_called_once_with(path=Path("/some/path"), search_parent_directories=True)


def testnormalize_git_paths_same_dir():
    """When git_root == working_dir, paths are returned unchanged."""
    paths = ["src/module.py", "tests/test_foo.py"]
    result = normalize_git_paths(paths, Path("/repo"), Path("/repo"))
    assert result == paths


def test_normalize_git_paths_monorepo():
    """Git-root-relative paths are converted to working-dir-relative."""
    paths = ["backend/src/pkg/module.py", "backend/tests/test_foo.py"]
    result = normalize_git_paths(paths, Path("/repo"), Path("/repo/backend"))
    assert result == ["src/pkg/module.py", "tests/test_foo.py"]


def test_normalize_git_paths_file_outside_working_dir():
    """Files outside working_dir are returned as absolute paths."""
    paths = ["frontend/app.js", "backend/src/module.py"]
    result = normalize_git_paths(paths, Path("/repo"), Path("/repo/backend"))
    assert result == ["/repo/frontend/app.js", "src/module.py"]


def test_normalize_git_paths_deeply_nested():
    """Works for deeply nested subdirectories."""
    paths = ["services/backend/python/src/mod.py"]
    result = normalize_git_paths(paths, Path("/mono"), Path("/mono/services/backend/python"))
    assert result == ["src/mod.py"]


def test_normalize_git_paths_empty_list():
    """Empty input returns empty output."""
    result = normalize_git_paths([], Path("/repo"), Path("/repo/sub"))
    assert result == []


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_monorepo_branch_mode(mock_repo):
    """In a monorepo, git-relative paths are converted to CWD-relative."""
    diff_output = name_status_z(("M", "backend/src/pkg/module.py"), ("A", "backend/tests/test_foo.py"))
    mock_repo.return_value = DummyRepo(
        diff_branch_result=diff_output,
        working_tree_dir="/monorepo",
    )

    result = git.find_impacted_files_in_repo(Path("/monorepo/backend"), git.GitMode.BRANCH, "main")

    assert set(result) == {"src/pkg/module.py", "tests/test_foo.py"}


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_monorepo_unstaged_mode(mock_repo):
    """In a monorepo, unstaged files are also normalized to CWD-relative paths."""
    mock_repo.return_value = DummyRepo(
        unstaged=name_status_z(("M", "backend/src/module.py")),
        untracked_files=["backend/src/new_file.py"],
        working_tree_dir="/monorepo",
    )

    result = git.find_impacted_files_in_repo(Path("/monorepo/backend"), git.GitMode.UNSTAGED, None)

    assert set(result) == {"src/module.py", "src/new_file.py"}


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_monorepo_files_outside_cwd(mock_repo):
    """Files in sibling directories are returned as absolute paths."""
    diff_output = name_status_z(("M", "backend/src/module.py"), ("M", "frontend/app.js"))
    mock_repo.return_value = DummyRepo(
        diff_branch_result=diff_output,
        working_tree_dir="/monorepo",
    )

    result = git.find_impacted_files_in_repo(Path("/monorepo/backend"), git.GitMode.BRANCH, "main")

    assert "src/module.py" in result
    assert "/monorepo/frontend/app.js" in result


# --- Real-repository tests for UNSTAGED mode -------------------------------------
#
# The mocked tests above pin the plumbing; these exercise a real git repository so
# that a regression in *which* git comparison is used (index vs HEAD vs worktree)
# cannot hide behind a mock.


@pytest.fixture
def isolated_git_config(monkeypatch, tmp_path):
    """Shield the repositories below from the developer's global/system git config.

    Hooks from ``init.templateDir``, ``core.excludesFile`` patterns or a
    ``diff.renames`` override would otherwise change the outcome.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # git >= 2.32
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Older git only knows $HOME/.gitconfig and $XDG_CONFIG_HOME/git/config.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


@pytest.fixture
def real_repo(tmp_path, isolated_git_config):
    """A committed repository with one package file, returning ``(repo, root)``."""
    repo = Repo.init(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("x = 1\n")
    (pkg / "b.py").write_text("y = 1\n")
    repo.git.add("--all")
    repo.git.commit("-m", "init")
    return repo, tmp_path


def unstaged(root) -> list[str] | None:
    return git.find_impacted_files_in_repo(root, git.GitMode.UNSTAGED, None)


def test_unstaged_mode_sees_staged_only_modification(real_repo):
    """A modification that has been ``git add``-ed but not committed is still uncommitted work."""
    repo, root = real_repo
    (root / "pkg" / "a.py").write_text("x = 2\n")
    repo.git.add("pkg/a.py")

    assert unstaged(root) == ["pkg/a.py"]


def test_unstaged_mode_sees_staged_new_file(real_repo):
    repo, root = real_repo
    (root / "pkg" / "new.py").write_text("y = 1\n")
    repo.git.add("pkg/new.py")

    assert unstaged(root) == ["pkg/new.py"]


def test_unstaged_mode_reports_file_edited_after_staging_once(real_repo):
    repo, root = real_repo
    (root / "pkg" / "a.py").write_text("x = 2\n")
    repo.git.add("pkg/a.py")
    (root / "pkg" / "a.py").write_text("x = 3\n")

    assert unstaged(root) == ["pkg/a.py"]


def test_unstaged_mode_sees_staged_edit_reverted_on_disk(real_repo):
    """Staged content differs from HEAD even though the worktree matches HEAD again (``MM``)."""
    repo, root = real_repo
    (root / "pkg" / "a.py").write_text("x = 2\n")
    repo.git.add("pkg/a.py")
    (root / "pkg" / "a.py").write_text("x = 1\n")

    assert unstaged(root) == ["pkg/a.py"]


def test_unstaged_mode_sees_rename(real_repo):
    repo, root = real_repo
    repo.git.mv("pkg/a.py", "pkg/renamed.py")

    assert unstaged(root) == ["pkg/a.py", "pkg/renamed.py"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges and core.symlinks on Windows")
def test_unstaged_mode_sees_type_change(real_repo):
    """Replacing a file with a symlink changes what ``import`` yields."""
    _repo, root = real_repo
    (root / "pkg" / "a.py").unlink()
    (root / "pkg" / "a.py").symlink_to("b.py")

    assert unstaged(root) == ["pkg/a.py"]


def test_unstaged_mode_sees_conflicted_files(real_repo):
    """A file left in conflict exists on disk with merged markers; its tests must not be skipped."""
    repo, root = real_repo
    repo.git.checkout("-b", "theirs")
    (root / "pkg" / "a.py").write_text("x = 'theirs'\n")
    repo.git.commit("-am", "theirs")
    repo.git.checkout("-")
    (root / "pkg" / "a.py").write_text("x = 'ours'\n")
    repo.git.commit("-am", "ours")
    with pytest.raises(GitCommandError):
        repo.git.merge("theirs")

    assert unstaged(root) == ["pkg/a.py"]


def test_unstaged_mode_sees_deleted_file(real_repo):
    """A deleted conftest.py or lockfile must still reach the strategies."""
    _repo, root = real_repo
    (root / "pkg" / "a.py").unlink()

    assert unstaged(root) == ["pkg/a.py"]


def test_unstaged_mode_sees_untracked_file_with_quote_in_name(real_repo):
    """GitPython's untracked-file parser chokes on such names when core.quotePath is off."""
    repo, root = real_repo
    repo.git.config("core.quotePath", "false")
    (root / "pkg" / 'wé"ird.py').write_text("z = 1\n")

    assert unstaged(root) == ['pkg/wé"ird.py']


def test_unstaged_mode_keeps_non_ascii_paths_unquoted(real_repo):
    """With ``core.quotePath`` at its default, plain ``git diff`` would print ``"pkg/caf\\303\\251.py"``."""
    repo, root = real_repo
    (root / "pkg" / "café.py").write_text("x = 1\n")
    repo.git.add("pkg/café.py")
    (root / "pkg" / "café.py").write_text("x = 2\n")

    assert unstaged(root) == ["pkg/café.py"]


def test_unstaged_mode_still_sees_plain_worktree_edit_and_untracked(real_repo):
    _repo, root = real_repo
    (root / "pkg" / "a.py").write_text("x = 2\n")
    (root / "pkg" / "untracked.py").write_text("z = 1\n")

    assert unstaged(root) == ["pkg/a.py", "pkg/untracked.py"]


def test_unstaged_mode_lists_files_inside_untracked_directory(real_repo):
    _repo, root = real_repo
    (root / "newdir").mkdir()
    (root / "newdir" / "mod.py").write_text("z = 1\n")

    assert unstaged(root) == ["newdir/mod.py"]


def test_unstaged_mode_clean_real_repo_returns_none(real_repo):
    _repo, root = real_repo
    assert unstaged(root) is None


def test_unstaged_mode_repo_without_commits_treats_staged_files_as_added(tmp_path, isolated_git_config):
    """Before the first commit there is no HEAD to diff against; everything staged is new."""
    repo = Repo.init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 1\n")
    repo.git.add("a.py")

    assert unstaged(tmp_path) == ["a.py", "b.py"]


def test_unstaged_mode_works_with_index_v4(tmp_path, isolated_git_config):
    """``feature.manyFiles`` writes an index format GitPython cannot parse; the CLI path must not care."""
    repo = Repo.init(tmp_path)
    repo.git.config("feature.manyFiles", "true")
    (tmp_path / "a.py").write_text("x = 1\n")
    repo.git.add("a.py")
    repo.git.update_index("--index-version", "4")

    assert unstaged(tmp_path) == ["a.py"]


def test_unstaged_mode_bare_repo_returns_none(tmp_path, isolated_git_config):
    Repo.init(tmp_path, bare=True)
    assert unstaged(tmp_path) is None
