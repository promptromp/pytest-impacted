"""Git related functions."""

from enum import StrEnum
from pathlib import Path

from git import Repo


class GitMode(StrEnum):
    """Git modes for the plugin."""

    UNSTAGED = "unstaged"
    BRANCH = "branch"


def find_modified_files_in_repo(
    repo_dir: str, git_mode: str, base_branch: str | None
) -> list[str] | None:
    """Find modifies files in the repository. This corresponds to unstanged changes.

    :param path: path to the root of the git repository.

    """
    repo = Repo(path=Path(repo_dir))

    modified_files = None
    if git_mode == GitMode.UNSTAGED:
        if not repo.is_dirty():
            # No changes in the repository and we are working in unstanged mode.
            return None

        modified_files = [item.a_path for item in repo.index.diff(None)]

    elif git_mode == GitMode.BRANCH:
        # Get the list of modified files in the current branch
        modified_files = [
            item for item in repo.git.diff(base_branch, name_only=True).splitlines()
        ]

    if not modified_files:
        return None

    return modified_files
