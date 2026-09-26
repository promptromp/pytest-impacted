"""pytest fixtures used by unit-tests."""

import os

import pytest


pytest_plugins = "pytester"


#: Variables that tell git which repository to use (``git rev-parse --local-env-vars``).
#: Git exports them to hooks, and the pre-commit hook runs this suite from inside a
#: commit, so every throwaway repository the tests create would otherwise resolve to
#: the repository being committed — overwriting its index, or re-initialising it as bare.
_GIT_REPO_LOCATING_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)


def pytest_configure(config):
    """Scrub repo-locating git variables before any fixture — session-scoped ones included — runs."""
    for name in _GIT_REPO_LOCATING_VARS:
        os.environ.pop(name, None)


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
