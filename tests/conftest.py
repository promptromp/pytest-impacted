"""pytest fixtures used by unit-tests."""

import os

import pytest


pytest_plugins = "pytester"


def isolated_git_env(home) -> dict[str, str]:
    """Environment that shields git from the developer's global/system config.

    Hooks from ``init.templateDir``, ``core.excludesFile`` patterns, a
    ``diff.renames`` override or a non-``main`` ``init.defaultBranch`` would
    otherwise change what these tests observe. Identity is supplied the same
    way, so no ``git config`` calls are needed.
    """
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,  # git >= 2.32
        "GIT_CONFIG_NOSYSTEM": "1",
        # Older git only knows $HOME/.gitconfig and $XDG_CONFIG_HOME/git/config.
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "xdg"),
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }


@pytest.fixture
def isolated_git_config(monkeypatch, tmp_path):
    """Apply :func:`isolated_git_env` to the current process."""
    for key, value in isolated_git_env(tmp_path).items():
        monkeypatch.setenv(key, value)
