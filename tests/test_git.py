"""Unit tests for the git module."""

from unittest.mock import MagicMock, patch

import pytest

from pytest_impacted import git


class DummyRepo:
    def __init__(
        self,
        dirty=False,
        diff_result=None,
        diff_branch_result=None,
        untracked_files=None,
        current_branch="feature/some-feature-branch",
    ):
        self._dirty = dirty
        self._diff_result = diff_result or []
        self._diff_branch_result = diff_branch_result or ""
        self.untracked_files = untracked_files or []
        self.index = MagicMock()
        self.index.diff = MagicMock(return_value=self._diff_result)
        self.git = MagicMock()
        self.git.diff = MagicMock(return_value=self._diff_branch_result)
        self.commit = MagicMock()
        self.head = MagicMock()
        self.head.reference = current_branch

    def is_dirty(self):
        return self._dirty


@patch("pytest_impacted.git.GIT_AVAILABLE", False)
def test_find_impacted_files_in_repo_git_not_available():
    """Test find_impacted_files_in_repo when git is not available."""
    with pytest.warns(UserWarning, match="Git functionality is disabled"):
        result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result is None


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_clean(mock_repo):
    mock_repo.return_value = DummyRepo(dirty=False)
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result is None


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_dirty(mock_repo):
    # Create mock diff objects with change_type attribute
    diff1 = MagicMock(a_path="file1.py", b_path=None, change_type="M")
    diff2 = MagicMock(a_path=None, b_path="file2.py", change_type="A")
    diff_result = [diff1, diff2]
    mock_repo.return_value = DummyRepo(dirty=True, diff_result=diff_result)
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert set(result) == {"file1.py", "file2.py"}


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_dirty_with_untracked_files(mock_repo):
    # Create mock diff objects with change_type attribute
    diff1 = MagicMock(a_path="file1.py", b_path=None, change_type="M")
    diff2 = MagicMock(a_path=None, b_path="file2.py", change_type="A")
    diff_result = [diff1, diff2]
    mock_repo.return_value = DummyRepo(dirty=True, diff_result=diff_result, untracked_files=["file3.py", "file4.py"])
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert set(result) == {"file1.py", "file2.py", "file3.py", "file4.py"}


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_dirty_no_changes(mock_repo):
    """Test UNSTAGED mode when repo is dirty but no actual file changes or untracked files."""
    mock_repo.return_value = DummyRepo(dirty=True, diff_result=[], untracked_files=[])
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result is None


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_branch(mock_repo):
    diff_branch_result = "M\tfile3.py\nA\tfile4.py\n"
    mock_repo.return_value = DummyRepo(diff_branch_result=diff_branch_result)
    result = git.find_impacted_files_in_repo(".", git.GitMode.BRANCH, "main")
    assert set(result) == {"file3.py", "file4.py"}


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_branch_none(mock_repo):
    diff_branch_result = ""
    mock_repo.return_value = DummyRepo(diff_branch_result=diff_branch_result)
    result = git.find_impacted_files_in_repo(".", git.GitMode.BRANCH, "main")
    assert result is None


@patch("builtins.print")
def test_describe_index_diffs(mock_print):
    """Test the describe_index_diffs function."""

    # Create mock Diff objects with change_type attribute
    diff1 = MagicMock(change_type="M")
    diff1.__str__ = MagicMock(return_value="diff_content_1")
    diff2 = MagicMock(change_type="A")
    diff2.__str__ = MagicMock(return_value="diff_content_2")
    diffs = [diff1, diff2]

    git.describe_index_diffs(diffs)

    # Check that print was called with the correct messages
    mock_print.assert_any_call("diff: diff_content_1")
    mock_print.assert_any_call("diff: diff_content_2")
    assert mock_print.call_count == 2


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
    diff_output = """M\tmodified.py
R100\told_name.py\tnew_name.py
C85\toriginal.py\tcopy.py
D\tdeleted.py"""

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


def test_change_from_git_diff_name_status_simple():
    """Test Change.from_git_diff_name_status with simple status codes."""
    # Test modified file
    change = git.Change.from_git_diff_name_status(name="file.py", status="M")
    assert change.a_path == "file.py"
    assert change.b_path is None
    assert change.status == git.GitStatus.MODIFIED

    # Test added file
    change = git.Change.from_git_diff_name_status(name="new_file.py", status="A")
    assert change.a_path == "new_file.py"
    assert change.status == git.GitStatus.ADDED

    # Test with None status
    change = git.Change.from_git_diff_name_status(name="file.py", status=None)
    assert change.a_path == "file.py"
    assert change.status is None


def test_change_from_git_diff_name_status_rename_copy():
    """Test Change.from_git_diff_name_status with rename and copy operations."""
    # Test rename with tab-separated paths
    change = git.Change.from_git_diff_name_status(name="old_file.py\tnew_file.py", status="R100")
    assert change.a_path == "old_file.py"
    assert change.b_path == "new_file.py"
    assert change.status == git.GitStatus.RENAMED

    # Test copy with tab-separated paths
    change = git.Change.from_git_diff_name_status(name="original.py\tcopy.py", status="C85")
    assert change.a_path == "original.py"
    assert change.b_path == "copy.py"
    assert change.status == git.GitStatus.COPIED

    # Test rename without tab (edge case)
    change = git.Change.from_git_diff_name_status(name="file.py", status="R100")
    assert change.a_path == "file.py"
    assert change.b_path is None
    assert change.status == git.GitStatus.RENAMED


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


def test_changeset_from_diff_objs():
    """Test ChangeSet.from_diff_objs method."""
    # Create mock diff objects
    diff1 = MagicMock(a_path="file1.py", b_path=None, change_type="M")
    diff2 = MagicMock(a_path=None, b_path="file2.py", change_type="A")
    diff3 = MagicMock(a_path="old.py", b_path="new.py", change_type="R")

    diffs = [diff1, diff2, diff3]
    change_set = git.ChangeSet.from_diff_objs(diffs)

    assert len(change_set.changes) == 3
    assert change_set.changes[0].name == "file1.py"
    assert change_set.changes[1].name == "file2.py"
    assert change_set.changes[2].name == "old.py"


def test_changeset_from_git_diff_name_status_output():
    """Test ChangeSet.from_git_diff_name_status_output method."""
    diff_output = """M\tmodified.py
A\tadded.py
D\tdeleted.py"""

    change_set = git.ChangeSet.from_git_diff_name_status_output(diff_output)

    assert len(change_set.changes) == 3
    assert change_set.changes[0].status == git.GitStatus.MODIFIED
    assert change_set.changes[1].status == git.GitStatus.ADDED
    assert change_set.changes[2].status == git.GitStatus.DELETED


def test_changeset_from_git_diff_name_status_output_empty():
    """Test ChangeSet.from_git_diff_name_status_output with empty input."""
    change_set = git.ChangeSet.from_git_diff_name_status_output("")
    assert len(change_set.changes) == 0


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_unstaged_mode_clean_repo(mock_repo):
    """Test impacted_files_for_unstaged_mode with clean repo."""
    repo = DummyRepo(dirty=False)
    result = git.impacted_files_for_unstaged_mode(repo)
    assert result is None


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_unstaged_mode_with_deleted_files(mock_repo):
    """Test impacted_files_for_unstaged_mode with deleted files."""
    diff1 = MagicMock(a_path="file1.py", b_path=None, change_type="M")
    diff2 = MagicMock(a_path="deleted.py", b_path=None, change_type="D")
    diff_result = [diff1, diff2]

    repo = DummyRepo(dirty=True, diff_result=diff_result)
    result = git.impacted_files_for_unstaged_mode(repo)

    # Should only include modified and added files, not deleted
    assert result == ["file1.py"]


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_branch_mode_with_deleted_files(mock_repo):
    """Test impacted_files_for_branch_mode with deleted files."""
    diff_output = """M\tmodified.py
D\tdeleted.py
A\tadded.py"""

    repo = DummyRepo(diff_branch_result=diff_output)
    result = git.impacted_files_for_branch_mode(repo, "main")

    # Should only include modified and added files, not deleted
    assert set(result) == {"modified.py", "added.py"}


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
    with patch("pytest_impacted.git.GIT_AVAILABLE", False):
        with pytest.warns(UserWarning, match="Git functionality is disabled"):
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


def test_changeset_from_git_diff_name_status_output_single_column():
    """Test ChangeSet.from_git_diff_name_status_output with single column input."""
    diff_output = "M"
    # This should raise a ValueError due to unpacking error when split doesn't return 2 elements
    with pytest.raises(ValueError):
        git.ChangeSet.from_git_diff_name_status_output(diff_output)


def test_changeset_from_git_diff_name_status_output_malformed():
    """Test ChangeSet.from_git_diff_name_status_output with malformed input."""
    # This tests the edge case where split doesn't return exactly 2 elements
    diff_output = "M\tfile1.py\textra_data"
    change_set = git.ChangeSet.from_git_diff_name_status_output(diff_output)

    assert len(change_set.changes) == 1
    # The split with maxsplit=1 should handle this correctly
    assert change_set.changes[0].status == git.GitStatus.MODIFIED
    assert change_set.changes[0].name == "file1.py\textra_data"


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_with_path_object(mock_repo):
    """Test find_impacted_files_in_repo with Path object instead of string."""
    from pathlib import Path

    diff1 = MagicMock(a_path="file1.py", b_path=None, change_type="M")
    diff_result = [diff1]
    mock_repo.return_value = DummyRepo(dirty=True, diff_result=diff_result)

    result = git.find_impacted_files_in_repo(Path("."), git.GitMode.UNSTAGED, None)
    assert result == ["file1.py"]
    # Verify that Repo was called with a Path object
    mock_repo.assert_called_once_with(path=Path("."))


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


def test_change_from_git_diff_name_status_with_none_name():
    """Test Change.from_git_diff_name_status with None name."""
    change = git.Change.from_git_diff_name_status(name=None, status="M")
    assert change.a_path is None
    assert change.b_path is None
    assert change.status == git.GitStatus.MODIFIED


def test_change_from_git_diff_name_status_rename_without_tab():
    """Test Change.from_git_diff_name_status with rename status but no tab in name."""
    change = git.Change.from_git_diff_name_status(name="file.py", status="R100")
    assert change.a_path == "file.py"
    assert change.b_path is None
    assert change.status == git.GitStatus.RENAMED


def test_changeset_from_diff_objs_with_none_a_path():
    """Test ChangeSet.from_diff_objs with diff objects that have None a_path."""
    # Create mock diff objects where a_path is None (new files)
    diff1 = MagicMock(a_path=None, b_path="new_file.py", change_type="A")
    diff2 = MagicMock(a_path="existing_file.py", b_path=None, change_type="M")

    diffs = [diff1, diff2]
    change_set = git.ChangeSet.from_diff_objs(diffs)

    assert len(change_set.changes) == 2
    assert change_set.changes[0].name == "new_file.py"  # Uses b_path when a_path is None
    assert change_set.changes[1].name == "existing_file.py"  # Uses a_path when available


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_unstaged_mode_with_none_names(mock_repo):
    """Test impacted_files_for_unstaged_mode with files that have None names."""
    # Create diff objects where some have None names
    diff1 = MagicMock(a_path="file1.py", b_path=None, change_type="M")
    diff2 = MagicMock(a_path=None, b_path=None, change_type="D")  # This will have None name
    diff_result = [diff1, diff2]

    repo = DummyRepo(dirty=True, diff_result=diff_result, untracked_files=["untracked.py"])
    result = git.impacted_files_for_unstaged_mode(repo)

    # Should filter out None names and only include valid files
    assert set(result) == {"file1.py", "untracked.py"}


@patch("pytest_impacted.git.Repo")
def test_impacted_files_for_branch_mode_with_none_names(mock_repo):
    """Test impacted_files_for_branch_mode with files that have None names."""
    # Create diff output that results in None names
    diff_output = "M\tfile1.py\nD\t"  # Second line has empty filename

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
