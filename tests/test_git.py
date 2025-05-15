"""Unit tests for the git module."""

from unittest.mock import patch, MagicMock
from pytest_impacted import git
import pytest


class DummyRepo:
    def __init__(
        self,
        dirty=False,
        diff_result=None,
        diff_branch_result=None,
        untracked_files=None,
    ):
        self._dirty = dirty
        self._diff_result = diff_result or []
        self._diff_branch_result = diff_branch_result or ""
        self.untracked_files = untracked_files or []
        self.index = MagicMock()
        self.index.diff = MagicMock(return_value=self._diff_result)
        self.git = MagicMock()
        self.git.diff = MagicMock(return_value=self._diff_branch_result)

    def is_dirty(self):
        return self._dirty


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_clean(mock_repo):
    mock_repo.return_value = DummyRepo(dirty=False)
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert result is None


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_dirty(mock_repo):
    diff_result = [
        MagicMock(a_path="file1.py", b_path=None),
        MagicMock(a_path=None, b_path="file2.py"),
    ]
    mock_repo.return_value = DummyRepo(dirty=True, diff_result=diff_result)
    result = git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)
    assert set(result) == {"file1.py", "file2.py"}


@patch("pytest_impacted.git.Repo")
def test_find_impacted_files_in_repo_unstaged_dirty_with_untracked_files(mock_repo):
    diff_result = [
        MagicMock(a_path="file1.py", b_path=None),
        MagicMock(a_path=None, b_path="file2.py"),
    ]
    mock_repo.return_value = DummyRepo(
        dirty=True, diff_result=diff_result, untracked_files=["file3.py", "file4.py"]
    )
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
    diff_branch_result = "file3.py\nfile4.py\n"
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

    # Create mock Diff objects
    # Diff objects have various attributes, but str(diff) is what's used.
    # We can mock the __str__ method or create objects that have a __str__ method.
    class MockDiff:
        def __init__(self, diff_str):
            self.diff_str = diff_str

        def __str__(self):
            return self.diff_str

    diff1 = MockDiff("diff_content_1")
    diff2 = MockDiff("diff_content_2")
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
