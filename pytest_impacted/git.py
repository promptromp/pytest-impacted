"""Git related functions."""

import warnings
from enum import StrEnum
from pathlib import Path
from typing import Any

try:
    from git import Repo
    from git.diff import Diff

    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    warnings.warn(
        "GitPython package is not available. Git-related functionality will be disabled. "
        "To enable git functionality, install GitPython and ensure git CLI is available.",
        stacklevel=2,
    )


class GitMode(StrEnum):
    """Git modes for the plugin."""

    UNSTAGED = "unstaged"
    BRANCH = "branch"


class GitStatus(StrEnum):
    """Git statuses.

    Reference: `man git-diff`

    """

    ADDED = "A"
    COPIED = "C"
    DELETED = "D"
    MODIFIED = "M"
    RENAMED = "R"
    TYPE_CHANGE = "T"
    UNMERGED = "U"
    UNKNOWN = "X"
    PAIRING_BROKEN = "B"

    @classmethod
    def from_git_diff_name_status(cls, status: str) -> "GitStatus":
        """Create a GitStatus from a git diff name status."""
        match status:
            case _ as status if status.startswith("R") and status[1:].isdigit():
                # git diff --name-status output may report <X><score> for renamed files
                return cls.RENAMED
            case _ as status if status.startswith("C") and status[1:].isdigit():
                # git diff --name-status output may report <X><score> for copied files
                return cls.COPIED
            case _:
                return cls(status)


class Change:
    """A change to a git repository file."""

    def __init__(
        self,
        a_path: str | None = None,
        b_path: str | None = None,
        status: GitStatus | None = None,
    ):
        self.a_path = a_path
        self.b_path = b_path
        self.status = status

    def __str__(self) -> str:
        return f"{self.status}\t{self.name}"

    @property
    def name(self) -> str | None:
        """The name of the file."""
        return self.a_path if self.a_path is not None else self.b_path

    @classmethod
    def from_git_diff_name_status(cls, *, name: str | None, status: str | None) -> "Change":
        """Create a Change from a git diff.

        Input is the output of `git diff --name-status`.
        For rename and copy operations, the name will contain both source and destination
        paths separated by a tab.

        """
        if status is not None and status.startswith(("R", "C")):
            # For rename/copy operations, split the name into source and destination
            if name is not None and "\t" in name:
                a_path, b_path = name.split("\t", 1)
                return cls(
                    a_path=a_path,
                    b_path=b_path,
                    status=GitStatus.from_git_diff_name_status(status),
                )

        return cls(
            a_path=name,
            b_path=None,
            status=GitStatus.from_git_diff_name_status(status) if status is not None else None,
        )


class ChangeSet:
    """A set of changes to files in a git repository."""

    def __init__(self, changes: list[Change]):
        self.changes = changes

    def __str__(self) -> str:
        return "\n".join(str(change) for change in self.changes)

    @classmethod
    def from_diff_objs(cls, diffs: list[Diff]) -> "ChangeSet":
        """Create a ChangeSet from a list of git diff objects."""
        # Nb. a_path would be None if this is a new file in which case
        # we use the `b_path` argument to get its name and consider it
        # modified.
        changes = [
            Change.from_git_diff_name_status(
                status=diff.change_type,
                name=diff.a_path if diff.a_path is not None else diff.b_path,
            )
            for diff in diffs
        ]
        return cls(changes)

    @classmethod
    def from_git_diff_name_status_output(cls, diffs_str: str) -> "ChangeSet":
        """Create a ChangeSet from a list of git diffs.

        Input is the output of `git diff --name-status`.

        Example input format:
        ```
        M\tsetup.py
        D\tsetup.cfg
        ```

        """
        diffs = [line.split("\t", 1) for line in diffs_str.splitlines()]

        changes = [Change.from_git_diff_name_status(status=status, name=name) for (status, name) in diffs]
        return cls(changes)


def without_nones(items: list[Any | None]) -> list[Any]:
    """Remove all Nones from the list."""
    return [item for item in items if item is not None]


def describe_index_diffs(diffs: list[Diff]) -> None:
    """Describe the index diffs to stdout."""
    for diff in diffs:
        print(f"diff: {str(diff)}")


def find_impacted_files_in_repo(repo_dir: str | Path, git_mode: GitMode, base_branch: str | None) -> list[str] | None:
    """Find impacted files in the repository. The definition of impacted is dependent on the git mode:

    UNSTAGED:
        - All files that have been modified in the working directory according to git diff.
        - Any untracked files are also included.

    BRANCH:
        - All files that have been modified in the current branch, relative to the base branch.
        - This does *not* include untracked files as the expectation is that this is used for committed changes.

    :param repo_dir: path to the root of the git repository.
    :param git_mode: the git mode to use.
    :param base_branch: the base branch to compare against.

    """
    if not GIT_AVAILABLE:
        warnings.warn(
            "Git functionality is disabled because GitPython is not available. "
            "To enable git functionality, install GitPython and ensure git CLI is available.",
            stacklevel=2,
        )
        return None

    repo = Repo(path=Path(repo_dir))

    match git_mode:
        case GitMode.UNSTAGED:
            impacted_files = impacted_files_for_unstaged_mode(repo)

        case GitMode.BRANCH:
            if not base_branch:
                raise ValueError("Base branch is required for running in BRANCH git mode")

            impacted_files = impacted_files_for_branch_mode(repo, base_branch=base_branch)

        case _:
            raise ValueError(f"Invalid git mode: {git_mode}")

    return impacted_files


def impacted_files_for_unstaged_mode(repo: Repo) -> list[str] | None:
    """Get the impacted files when in the UNSTAGED git mode."""
    if not repo.is_dirty():
        # No changes in the repository and we are working in unstanged mode, nack.
        return None

    diffs = repo.index.diff(None)
    change_set = ChangeSet.from_diff_objs(diffs)

    impacted_files = [item.name for item in change_set.changes if item.status in (GitStatus.MODIFIED, GitStatus.ADDED)]

    # Nb. we also include untracked files as they are also
    # potentially impactful for unit-test coverage.
    impacted_files.extend(repo.untracked_files)

    return without_nones(impacted_files) or None


def impacted_files_for_branch_mode(repo: Repo, base_branch: str) -> list[str] | None:
    """Get the impacted files when in the BRANCH git mode."""

    current_branch = repo.head.reference
    diffs = repo.git.diff(base_branch, current_branch, name_status=True)
    change_set = ChangeSet.from_git_diff_name_status_output(diffs)

    impacted_files = [item.name for item in change_set.changes if item.status in (GitStatus.MODIFIED, GitStatus.ADDED)]

    return without_nones(impacted_files) or None


def deleted_files_from_diff(change_set: ChangeSet) -> list[str]:
    """Get a list of deleted files from git diffs."""
    return without_nones([item.name for item in change_set.changes if item.status == GitStatus.DELETED])
