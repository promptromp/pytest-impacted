"""Git related functions."""

from pathlib import Path

from git import Repo


def find_modified_files_in_repo(repo_dir: str) -> list[str] | None:
    """Find modifies files in the repository. This corresponds to unstanged changes.

    :param path: path to the root of the git repository.

    """
    repo = Repo(path=Path(repo_dir))

    if not repo.is_dirty():
        # No changes in the repository and we are working in unstanged mode.
        return None

    modified_files = [item.a_path for item in repo.index.diff(None)]
    if not modified_files:
        return None

    return modified_files
