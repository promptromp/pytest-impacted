"""pytest fixtures used by unit-tests."""

import os
import subprocess

import pytest


pytest_plugins = "pytester"


#: Fallback for :func:`_git_repo_locating_vars` when git cannot be asked.
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
_scrubbed_git_env: dict[str, str] = {}


def _git_repo_locating_vars() -> list[str]:
    """The variables that tell git which repository to use, as this git defines them."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return list(_GIT_REPO_LOCATING_VARS)
    return result.stdout.split() or list(_GIT_REPO_LOCATING_VARS)


def pytest_configure(config):
    """Scrub repo-locating git variables before any fixture — session-scoped ones included — runs.

    Git exports them to hooks, and the pre-commit hook runs this suite from inside a
    commit, so every throwaway repository the tests create would otherwise resolve to
    the repository being committed — overwriting its index, or re-initialising it as bare.
    """
    for name in _git_repo_locating_vars():
        if name in os.environ:
            _scrubbed_git_env[name] = os.environ.pop(name)


def pytest_unconfigure(config):
    """Restore what :func:`pytest_configure` removed, for callers of ``pytest.main()`` in-process."""
    os.environ.update(_scrubbed_git_env)
    _scrubbed_git_env.clear()


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


@pytest.fixture
def isolated_git_config(monkeypatch, tmp_path):
    """Apply :func:`isolated_git_env` to the current process."""
    for key, value in isolated_git_env(tmp_path).items():
        monkeypatch.setenv(key, value)
