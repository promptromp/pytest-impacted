"""Unit tests for the git module."""

from unittest.mock import patch, MagicMock
from pytest_impacted import git


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
