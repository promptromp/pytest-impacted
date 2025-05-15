"""Git related functions."""

from enum import StrEnum
from pathlib import Path
from typing import Any
from git import Repo
from git.diff import Diff


class GitMode(StrEnum):
    """Git modes for the plugin."""

    UNSTAGED = "unstaged"
    BRANCH = "branch"


def without_nones(items: list[Any | None]) -> list[Any]:
    """Remove all Nones from the list."""
    return [item for item in items if item is not None]


def describe_index_diffs(diffs: list[Diff]) -> None:
    """Describe the index diffs to stdout."""
    for diff in diffs:
        print(f"diff: {str(diff)}")


def find_impacted_files_in_repo(
    repo_dir: str | Path, git_mode: GitMode, base_branch: str | None
) -> list[str] | None:
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
    repo = Repo(path=Path(repo_dir))

    match git_mode:
        case GitMode.UNSTAGED:
            impacted_files = impacted_files_for_unstaged_mode(repo)

        case GitMode.BRANCH:
            if not base_branch:
                raise ValueError(
                    "Base branch is required for running in BRANCH git mode"
                )

            impacted_files = impacted_files_for_branch_mode(
                repo, base_branch=base_branch
            )

        case _:
            raise ValueError(f"Invalid git mode: {git_mode}")

    return impacted_files


def impacted_files_for_unstaged_mode(repo: Repo) -> list[str] | None:
    """Get the impacted files when in the UNSTAGED git mode."""
    # Nb. a_path would be None if this is a new file in which case
    # we use the `b_path` argument to get its name and consider it
    # modified.
    if not repo.is_dirty():
        # No changes in the repository and we are working in unstanged mode, nack.
        return None

    impacted_files = [
        item.a_path if item.a_path is not None else item.b_path
        for item in repo.index.diff(None)
    ]

    # Nb. we also include untracked files as they are also
    # potentially impactful for unit-test coverage.
    impacted_files.extend(repo.untracked_files)

    return without_nones(impacted_files) or None


def impacted_files_for_branch_mode(repo: Repo, base_branch: str) -> list[str] | None:
    """Get the impacted files when in the BRANCH git mode."""
    impacted_files = [
        item for item in repo.git.diff(base_branch, name_only=True).splitlines()
    ]

    return without_nones(impacted_files) or None
