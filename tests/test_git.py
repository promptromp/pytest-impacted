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


def test_git_status_from_git_diff_name_status():
    """Test GitStatus.from_git_diff_name_status with various status codes."""
    # Test basic status codes
    assert git.GitStatus.from_git_diff_name_status("A") == git.GitStatus.ADDED
    assert git.GitStatus.from_git_diff_name_status("M") == git.GitStatus.MODIFIED
    assert git.GitStatus.from_git_diff_name_status("D") == git.GitStatus.DELETED

    # Test rename with similarity score
    assert git.GitStatus.from_git_diff_name_status("R100") == git.GitStatus.RENAMED
    assert git.GitStatus.from_git_diff_name_status("R75") == git.GitStatus.RENAMED

    # Test copy with similarity score
    assert git.GitStatus.from_git_diff_name_status("C100") == git.GitStatus.COPIED
    assert git.GitStatus.from_git_diff_name_status("C85") == git.GitStatus.COPIED


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
