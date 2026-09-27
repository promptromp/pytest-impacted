"""Plain helpers for tests that drive git (import these; fixtures live in conftest.py)."""

import os


def isolated_git_env(home) -> dict[str, str]:
    """Environment that shields git from the developer's global/system config.

    Hooks from ``init.templateDir``, ``core.excludesFile`` patterns, a
    ``diff.renames`` override or a non-``main`` ``init.defaultBranch`` would
    otherwise change what these tests observe. Identity is supplied the same
    way, so no ``git config`` calls are needed.

    Automatic maintenance is switched off: recent git detaches it after a
    commit, and a copy of the repository taken meanwhile races its
    ``.git/objects/maintenance.lock``.
    """
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,  # git >= 2.32
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "2",  # git >= 2.31
        "GIT_CONFIG_KEY_0": "maintenance.auto",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "gc.auto",
        "GIT_CONFIG_VALUE_1": "0",
        # Older git only knows $HOME/.gitconfig and $XDG_CONFIG_HOME/git/config.
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "xdg"),
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }


def edit_file(pytester, rel: str) -> None:
    """Make an uncommitted change to *rel*, as unstaged git mode sees it."""
    path = pytester.path / rel
    path.write_text(path.read_text() + "\n# edited\n")


def write_files(root, files: dict[str, str]) -> None:
    """Write ``{relative path: source}`` under *root*, creating directories as needed."""
    for rel, source in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(source)
