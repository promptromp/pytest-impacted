"""Unit tests for the git module."""

import shutil
import sys
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, patch

import pytest
from git import GitCommandError, Repo

from pytest_impacted import git
from pytest_impacted.git import normalize_git_paths


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
        working_tree_dir=None,
    ):
        self.bare = False
        self.git = MagicMock()
        self.git.diff = MagicMock(side_effect=self._diff)
        self.git.ls_files = MagicMock(return_value="".join(f"{path}\0" for path in untracked_files))
        # The base has not moved since the fork, so the fork point *is* the base ref
        # (merge-base is called as: --all --end-of-options <base> <head>).
        self.git.merge_base = MagicMock(side_effect=lambda *args: args[-2])
        self.working_tree_dir = working_tree_dir or str(Path.cwd())
        self._staged, self._unstaged, self._branch = staged, unstaged, diff_branch_result

    def _diff(self, *args):
        if "--cached" in args:
            return self._staged
        if "--end-of-options" in args:
            return self._branch
        return self._unstaged


def test_find_impacted_files_in_repo_invalid_mode():
    """Test find_impacted_files_in_repo with an invalid git_mode."""
    with pytest.raises(ValueError, match="Invalid git mode: invalid_mode"):
        git.find_impacted_files_in_repo(".", "invalid_mode", "main")


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


def test_changeset_from_git_diff_name_status_output_all_statuses():
    """Every status git can emit under ``--no-renames`` parses to its enum member."""
    diff_output = name_status_z(
        ("M", "modified.py"), ("A", "added.py"), ("D", "deleted.py"), ("T", "typed.py"), ("U", "conflicted.py")
    )

    change_set = git.ChangeSet.from_git_diff_name_status_output(diff_output)

    assert [(c.status, c.path) for c in change_set.changes] == [
        (git.GitStatus.MODIFIED, "modified.py"),
        (git.GitStatus.ADDED, "added.py"),
        (git.GitStatus.DELETED, "deleted.py"),
        (git.GitStatus.TYPE_CHANGE, "typed.py"),
        (git.GitStatus.UNMERGED, "conflicted.py"),
    ]


def test_changeset_from_git_diff_name_status_output_dangling_status_is_ignored():
    """A trailing status with no path (truncated output) does not crash the parser."""
    change_set = git.ChangeSet.from_git_diff_name_status_output("M\0a.py\0D\0")
    assert [(c.status, c.path) for c in change_set.changes] == [(git.GitStatus.MODIFIED, "a.py")]


def test_change_class():
    """Test the Change class."""
    change = git.Change("file.py", git.GitStatus.MODIFIED)
    assert change.path == "file.py"
    assert change.status == git.GitStatus.MODIFIED
    assert str(change) == "M\tfile.py"


def test_changeset_class():
    """Test the ChangeSet class."""
    changes = [
        git.Change("file1.py", status=git.GitStatus.MODIFIED),
        git.Change("file2.py", status=git.GitStatus.ADDED),
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
    output = name_status_z(("M", "pkg/café.py"), ("A", 'weird\t"name"\\.py'), ("D", "sp ace/naïve.py"))
    change_set = git.ChangeSet.from_git_diff_name_status_output(output)
    assert [c.path for c in change_set.changes] == ["pkg/café.py", 'weird\t"name"\\.py', "sp ace/naïve.py"]


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


def test_git_status_from_git_diff_name_status_edge_cases():
    """Test GitStatus.from_git_diff_name_status with edge cases."""
    # An unknown letter is an error; an empty token too.
    with pytest.raises(ValueError):
        git.GitStatus.from_git_diff_name_status("Z")
    with pytest.raises(ValueError):
        git.GitStatus.from_git_diff_name_status("")

    # Test other valid status codes
    assert git.GitStatus.from_git_diff_name_status("T") == git.GitStatus.TYPE_CHANGE
    assert git.GitStatus.from_git_diff_name_status("U") == git.GitStatus.UNMERGED
    assert git.GitStatus.from_git_diff_name_status("X") == git.GitStatus.UNKNOWN
    assert git.GitStatus.from_git_diff_name_status("B") == git.GitStatus.PAIRING_BROKEN


def test_change_str_with_none_status():
    """Test Change.__str__ method with None status."""
    change = git.Change("file.py", status=None)
    assert str(change) == "None\tfile.py"


def test_changeset_str_empty():
    """Test ChangeSet.__str__ method with empty changes."""
    change_set = git.ChangeSet([])
    assert str(change_set) == ""


def test_changeset_str_multiple_changes():
    """Test ChangeSet.__str__ method with multiple changes."""
    changes = [
        git.Change("file1.py", status=git.GitStatus.MODIFIED),
        git.Change("file2.py", status=git.GitStatus.ADDED),
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
    change_set = git.ChangeSet([git.Change("a.py", status=status)])
    assert (git._impactful_paths(change_set) == ["a.py"]) is impactful


def test_impacted_files_for_branch_mode_with_empty_path_token():
    """A record whose path token is empty is dropped rather than yielding an empty path."""
    repo = DummyRepo(diff_branch_result=name_status_z(("M", "file1.py"), ("D", "")))

    assert git.impacted_files_for_branch_mode(repo, "main") == ["file1.py"]


def test_branch_mode_needs_a_base_branch(real_repo):
    _repo, root = real_repo

    with pytest.raises(ValueError, match="Base branch is required for running in BRANCH git mode"):
        git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, None)


def test_git_status_from_git_diff_name_status_copy_and_rename():
    """Test GitStatus.from_git_diff_name_status with copy and rename scores."""
    # Test copy with score
    assert git.GitStatus.from_git_diff_name_status("C100") == git.GitStatus.COPIED
    assert git.GitStatus.from_git_diff_name_status("C85") == git.GitStatus.COPIED

    # Test rename with score
    assert git.GitStatus.from_git_diff_name_status("R100") == git.GitStatus.RENAMED
    assert git.GitStatus.from_git_diff_name_status("R75") == git.GitStatus.RENAMED


def test_impacted_files_for_branch_mode_ends_the_diff_at_head():
    """HEAD, not the branch's name (a tag may share it), in the merge base and the diff; ``--`` ends the revisions."""
    repo = DummyRepo(diff_branch_result=name_status_z(("M", "file1.py")))
    repo.git.merge_base = MagicMock(return_value="fork456\n")

    result = git.impacted_files_for_branch_mode(repo, "main")

    assert result == ["file1.py"]
    repo.git.merge_base.assert_called_once_with("--all", "--end-of-options", "main", "HEAD")
    repo.git.diff.assert_called_once_with(
        "--name-status", "-z", "--no-renames", "--end-of-options", "fork456", "HEAD", "--"
    )


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


# --- Tests for find_repo and normalize_git_paths (monorepo support) ---


def test_normalize_git_paths_same_dir():
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


def unstaged(root) -> list[str] | None:
    return git.find_impacted_files_in_repo(root, git.GitMode.UNSTAGED, None)


def branch(root, base) -> list[str] | None:
    return git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, base)


def commit_all(repo, message="change"):
    repo.git.add("--all")
    repo.git.commit("-m", message)


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


@pytest.mark.parametrize("mode", [git.GitMode.UNSTAGED, git.GitMode.BRANCH])
def test_bare_repo_is_a_clear_error(tmp_path, isolated_git_config, mode):
    """There is no working tree to diff, so say so instead of silently reporting no changes."""
    Repo.init(tmp_path, bare=True)
    with pytest.raises(git.GitError, match="bare repository"):
        git.find_impacted_files_in_repo(tmp_path, mode, "main")
    with pytest.raises(git.GitError, match="bare repository"):
        git.check_repository(tmp_path)


def test_git_that_cannot_launch_keeps_its_reason(monkeypatch):
    """GitPython raises GitCommandNotFound for any launch failure, so the reason must survive."""
    from git import GitCommandNotFound  # noqa: PLC0415

    def launch_fails(*_):
        raise GitCommandNotFound("git diff --cached", "WinError 206")

    monkeypatch.setattr(git, "_find_impacted_files", launch_fails)

    with pytest.raises(git.GitUnavailableError) as excinfo:
        git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)

    assert "WinError 206" in str(excinfo.value)
    assert "cmdline" not in str(excinfo.value)  # only the reason, not GitPython's full command line


def test_git_unavailable_error_survives_copy():
    """copy and pickle rebuild from ``args``; the message must not wrap itself again."""
    import copy  # noqa: PLC0415
    import pickle  # noqa: PLC0415

    err = git.GitUnavailableError("reason")

    assert str(copy.copy(err)) == str(pickle.loads(pickle.dumps(err))) == str(err)


def test_find_impacted_files_raises_without_git(monkeypatch):
    """Unknown changes are an error, never ``None`` — which callers read as "nothing changed"."""
    monkeypatch.setattr(git, "GIT_AVAILABLE", False)

    with pytest.raises(git.GitUnavailableError, match="changed files cannot be determined"):
        git.find_impacted_files_in_repo(".", git.GitMode.UNSTAGED, None)


# --- BRANCH mode against a real repository ---------------------------------------
#
# The mocked branch-mode tests pin the parsing; these prove real git accepts the
# flag ordering (options, then --end-of-options, then the revisions).


def test_branch_mode_sees_committed_modification(real_repo):
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (root / "pkg" / "a.py").write_text("x = 2\n")
    commit_all(repo)

    assert branch(root, base) == ["pkg/a.py"]


def test_branch_mode_sees_committed_deletion(real_repo):
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (root / "pkg" / "a.py").unlink()
    commit_all(repo)

    assert branch(root, base) == ["pkg/a.py"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges and core.symlinks on Windows")
def test_branch_mode_sees_committed_type_change(real_repo):
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (root / "pkg" / "a.py").unlink()
    (root / "pkg" / "a.py").symlink_to("b.py")
    commit_all(repo)

    assert branch(root, base) == ["pkg/a.py"]


def test_branch_mode_reports_rename_as_both_paths(real_repo):
    """``--no-renames`` makes a rename a deletion plus an addition, so both paths count."""
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    repo.git.mv("pkg/a.py", "pkg/renamed.py")
    commit_all(repo)

    assert branch(root, base) == ["pkg/a.py", "pkg/renamed.py"]


def test_branch_mode_works_on_detached_head(real_repo):
    """Detached HEAD is the normal state in CI checkouts."""
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (root / "pkg" / "a.py").write_text("x = 2\n")
    commit_all(repo)
    repo.git.checkout("--detach")

    assert branch(root, base) == ["pkg/a.py"]


@pytest.mark.parametrize("detached", [False, True], ids=["on the branch", "detached"])
def test_branch_mode_on_a_branch_named_like_a_directory(real_repo, detached):
    """``git diff <rev> pkg`` is ambiguous when ``pkg`` is a branch and a path too: the revisions end at ``--``."""
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.checkout("-b", "pkg")
    (root / "pkg" / "a.py").write_text("x = 2\n")
    commit_all(repo)
    if detached:
        repo.git.checkout("--detach")

    assert branch(root, base) == ["pkg/a.py"]


def test_branch_mode_against_a_base_named_like_a_directory(real_repo):
    """The base tip, diffed directly (no merge base), is no path either."""
    repo, root = real_repo
    repo.git.branch("-m", "pkg")
    repo.git.checkout("-b", "feature")
    (root / "pkg" / "a.py").write_text("x = 2\n")
    commit_all(repo)

    assert git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, "pkg", use_merge_base=False) == ["pkg/a.py"]


def test_branch_mode_on_a_branch_that_shares_its_name_with_a_tag(real_repo):
    """git resolves a short name to the tag before the branch: the diff must end at HEAD, not at the tag."""
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.tag("release")
    repo.git.checkout("-b", "release")
    (root / "pkg" / "a.py").write_text("x = 2\n")
    commit_all(repo)

    assert branch(root, base) == ["pkg/a.py"]


def test_branch_mode_rejects_option_like_base_ref(real_repo):
    _repo, root = real_repo

    with pytest.raises(git.InvalidGitRefError):
        branch(root, "--output=/tmp/pwned")


def _fork_then_advance_base(repo, root):
    """``feature`` edits a.py; afterwards the base branch edits b.py. Returns the base name."""
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (root / "pkg" / "a.py").write_text("x = 2\n")
    commit_all(repo)
    repo.git.checkout(base)
    (root / "pkg" / "b.py").write_text("y = 2\n")
    commit_all(repo, "base moves on")
    repo.git.checkout("feature")
    return base


def test_branch_mode_diffs_from_the_fork_point(real_repo):
    """Like a pull request's diff: what the base gained since the fork is not this branch's change."""
    repo, root = real_repo
    base = _fork_then_advance_base(repo, root)

    assert branch(root, base) == ["pkg/a.py"]


def test_branch_mode_can_diff_against_the_base_tip(real_repo):
    repo, root = real_repo
    base = _fork_then_advance_base(repo, root)

    result = git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, base, use_merge_base=False)

    assert result == ["pkg/a.py", "pkg/b.py"]


def test_branch_mode_without_a_fork_point_diffs_against_the_base_tip(real_repo):
    """No merge base (unrelated histories): fall back to the tip diff, and say why."""
    repo, root = real_repo
    original = repo.active_branch.name
    repo.git.checkout("--orphan", "unrelated")
    repo.git.rm("-r", "-q", "--cached", ".")
    (root / "other.txt").write_text("unrelated\n")
    repo.git.add("other.txt")
    repo.git.commit("-m", "unrelated root")
    repo.git.checkout("-f", "-b", "feature", original)  # back onto the original history
    (root / "pkg" / "a.py").write_text("x = 2\n")
    commit_all(repo)
    notices: list[str] = []

    result = git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, "unrelated", on_fallback=notices.append)

    assert result == git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, "unrelated", use_merge_base=False)
    assert "pkg/a.py" in result
    assert len(notices) == 1
    assert "the histories are unrelated" in notices[0]


def test_branch_mode_in_a_shallow_clone_falls_back(real_repo, tmp_path):
    """``actions/checkout`` defaults to ``fetch-depth: 1``: the fork point is simply not there."""
    repo, root = real_repo
    base = _fork_then_advance_base(repo, root)
    clone = tmp_path / "shallow"
    Repo.clone_from(f"file://{root}", clone, depth=1, no_single_branch=True).git.checkout("feature")
    notices: list[str] = []

    result = git.find_impacted_files_in_repo(clone, git.GitMode.BRANCH, f"origin/{base}", on_fallback=notices.append)

    tip_diff = git.find_impacted_files_in_repo(clone, git.GitMode.BRANCH, f"origin/{base}", use_merge_base=False)
    assert result == tip_diff
    assert "pkg/a.py" in result
    assert len(notices) == 1
    assert "the clone is shallow" in notices[0]


def test_branch_mode_with_an_unknown_base_is_an_error_not_a_fallback(real_repo):
    """Only "no common ancestor" falls back; a bad ref must not be blamed on a shallow clone."""
    _, root = real_repo
    notices: list[str] = []

    with pytest.raises(git.GitError, match="no-such-branch"):
        git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, "no-such-branch", on_fallback=notices.append)
    assert notices == []


def test_branch_mode_with_criss_cross_merges_combines_every_fork_point(real_repo):
    """Two best common ancestors: diffing from only one of them would miss a file."""
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.checkout("-b", "left")
    (root / "pkg" / "a.py").write_text("x = 2\n")
    commit_all(repo, "left")
    repo.git.checkout("-b", "right", base)
    (root / "pkg" / "b.py").write_text("y = 2\n")
    commit_all(repo, "right")
    left_tip, right_tip = repo.commit("left").hexsha, repo.commit("right").hexsha
    repo.git.checkout("left")
    repo.git.merge("-q", "--no-edit", right_tip)
    repo.git.checkout("right")
    repo.git.merge("-q", "--no-edit", left_tip)
    assert len(repo.git.merge_base("--all", "left", "right").split()) == 2  # precondition

    assert branch(root, "left") == ["pkg/a.py", "pkg/b.py"]


def test_normalize_git_paths_emits_posix_paths_for_windows_paths():
    """Every matcher is PurePosixPath-based; backslashes would become part of a file name."""
    result = git.normalize_git_paths(
        ["backend/src/m.py", "frontend/a.js"], PureWindowsPath("C:/repo"), PureWindowsPath("C:/repo/backend")
    )

    assert result == ["src/m.py", "C:/repo/frontend/a.js"]


@pytest.fixture
def repo_with_submodule(real_repo, tmp_path):
    """``(repo, root)``: the real repo with a submodule ``vendor`` holding ``lib/core.py`` and ``lib/util.py``."""
    repo, root = real_repo
    library = tmp_path / "library"
    library.mkdir()
    (library / "lib").mkdir()
    (library / "lib" / "core.py").write_text("x = 1\n")
    (library / "lib" / "util.py").write_text("y = 1\n")
    library_repo = Repo.init(library)
    library_repo.git.add("--all")
    library_repo.git.commit("-m", "library")
    repo.git.execute(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library), "vendor"])
    commit_all(repo, "add the submodule")
    return repo, root


def test_an_edit_inside_a_submodule_counts_as_a_change_to_the_file(repo_with_submodule):
    """git reports the submodule's own path, which names no module: the files changed inside it are added."""
    _, root = repo_with_submodule
    (root / "vendor" / "lib" / "core.py").write_text("x = 2\n")
    (root / "vendor" / "lib" / "new.py").write_text("z = 1\n")  # untracked inside it

    assert unstaged(root) == ["vendor", "vendor/lib/core.py", "vendor/lib/new.py"]


@pytest.mark.parametrize("edit", ["modify", "delete"])
def test_a_submodule_bumped_on_the_branch_brings_what_changed_inside_it(repo_with_submodule, edit):
    """Diffed inside the submodule, between the commits the superproject records: a deleted module counts too."""
    repo, root = repo_with_submodule
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    if edit == "modify":
        (root / "vendor" / "lib" / "core.py").write_text("x = 2\n")
    else:
        (root / "vendor" / "lib" / "core.py").unlink()
    commit_all(Repo(root / "vendor"), "bump")
    commit_all(repo, "bump the submodule")

    assert branch(root, base) == ["vendor", "vendor/lib/core.py"]


def test_a_submodule_not_checked_out_is_named(repo_with_submodule):
    """actions/checkout leaves submodules out by default: what changed in one cannot be seen, and is said."""
    repo, root = repo_with_submodule
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (root / "vendor" / "lib" / "core.py").write_text("x = 2\n")
    commit_all(Repo(root / "vendor"), "bump")
    commit_all(repo, "bump the submodule")
    repo.git.submodule("deinit", "-q", "-f", "vendor")
    notices: list[str] = []

    found = git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, base, on_fallback=notices.append)

    assert found == ["vendor"]
    assert len(notices) == 1 and "not checked out" in notices[0]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges and core.symlinks on Windows")
def test_only_a_recorded_submodule_is_followed(repo_with_submodule, tmp_path):
    """An untracked clone in the tree, or a symlink to a repository, is no submodule: its files are not added."""
    _, root = repo_with_submodule
    clone = root / "scratch"
    clone.mkdir()
    (clone / "pyproject.toml").write_text("")
    Repo.init(clone).git.add("--all")
    (root / "alias").symlink_to("vendor", target_is_directory=True)

    assert unstaged(root) == ["alias", "scratch/"]


def test_a_submodule_added_on_the_branch_brings_its_files(real_repo, tmp_path):
    repo, root = real_repo
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    library = tmp_path / "library"
    (library / "lib").mkdir(parents=True)
    (library / "lib" / "core.py").write_text("x = 1\n")
    library_repo = Repo.init(library)
    library_repo.git.add("--all")
    library_repo.git.commit("-m", "library")
    repo.git.execute(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library), "vendor"])
    commit_all(repo, "add the submodule")

    assert branch(root, base) == [".gitmodules", "vendor", "vendor/lib/core.py"]


def test_a_submodule_set_to_be_ignored_is_ignored(repo_with_submodule):
    """``ignore = all`` in .gitmodules tells git to look away, for a large vendored tree or a broken one: so do we."""
    repo, root = repo_with_submodule
    repo.git.config("-f", ".gitmodules", "submodule.vendor.ignore", "all")
    commit_all(repo, "ignore the submodule")
    shutil.rmtree(root / ".git" / "modules" / "vendor")  # broken, as main never minded
    (root / "pkg" / "a.py").write_text("x = 2\n")

    assert unstaged(root) == ["pkg/a.py"]


def _bump_on_a_branch(repo, root):
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (root / "vendor" / "lib" / "core.py").write_text("x = 2\n")
    commit_all(Repo(root / "vendor"), "bump")
    commit_all(repo, "bump the submodule")
    return base


@pytest.mark.parametrize(("how", "notices_expected"), [("its gitdir gone", 1), ("removed", 0)])
def test_a_submodule_that_cannot_be_read_never_fails_the_run(repo_with_submodule, how, notices_expected):
    """Nothing was selected for a submodule before: one that cannot be read is said, a removed one is not followed."""
    repo, root = repo_with_submodule
    if how == "removed":
        base = repo.active_branch.name
        repo.git.checkout("-b", "feature")
        repo.git.rm("-q", "vendor")
        commit_all(repo, "remove the submodule")
    else:
        base = _bump_on_a_branch(repo, root)
        shutil.rmtree(root / ".git" / "modules" / "vendor")
    notices: list[str] = []

    found = git.find_impacted_files_in_repo(root, git.GitMode.BRANCH, base, on_fallback=notices.append)

    assert "vendor" in found
    assert len(notices) == notices_expected, notices


def test_many_deleted_files_beside_a_submodule_are_never_asked_about(repo_with_submodule, monkeypatch):
    """A deleted file is no directory, so no submodule: thousands of them overflowed git's command line."""
    repo, root = repo_with_submodule
    repo.git.checkout("-b", "feature")
    for index in range(300):
        (root / "pkg" / f"gone{index}.py").write_text("")
    commit_all(repo, "add")
    base = repo.git.rev_parse("HEAD")
    for index in range(300):
        (root / "pkg" / f"gone{index}.py").unlink()
    commit_all(repo, "delete")
    asked = []
    real = git._gitlinks
    monkeypatch.setattr(
        git, "_gitlinks", lambda repo, revision, paths: asked.append(len(paths)) or real(repo, revision, paths)
    )

    assert len(branch(root, base)) == 300
    assert all(count == 0 for count in asked), asked


def test_a_submodule_s_commands_ignore_a_git_hook_s_environment(repo_with_submodule, monkeypatch):
    """The pre-commit hook exports GIT_INDEX_FILE=.git/index for the superproject; inside a submodule it is wrong."""
    _, root = repo_with_submodule
    (root / "vendor" / "lib" / "core.py").write_text("x = 2\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")

    assert unstaged(root) == ["vendor", "vendor/lib/core.py"]


def test_without_submodules_no_git_call_is_spent_on_them(real_repo, monkeypatch):
    """One process per changed path made 1,000 untracked files take 23 s: without a .gitmodules, none at all."""
    _, root = real_repo
    for index in range(50):
        (root / "pkg" / f"new{index}.py").write_text("")
    calls = []
    monkeypatch.setattr(git, "_gitlinks", lambda *args: calls.append(args) or frozenset())  # no .gitmodules: none

    assert len(unstaged(root)) == 50
    assert calls == []


def test_only_directories_are_asked_about_beside_a_submodule(repo_with_submodule, monkeypatch):
    """A changed file is no submodule: git is asked about candidate paths alone, never the whole index or tree."""
    _, root = repo_with_submodule
    for index in range(50):
        (root / "pkg" / f"new{index}.py").write_text("")
    (root / "vendor" / "lib" / "core.py").write_text("x = 2\n")
    asked = []
    real = git._gitlinks
    monkeypatch.setattr(
        git, "_gitlinks", lambda repo, revision, paths: asked.append(list(paths)) or real(repo, revision, paths)
    )

    found = unstaged(root)

    assert "vendor/lib/core.py" in found and len(found) == 52
    assert ["vendor"] in asked and all(len(paths) <= 1 for paths in asked)
