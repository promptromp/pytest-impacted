"""pytest fixtures used by unit-tests."""

import os
import subprocess
import textwrap

import pytest

from pytest_impacted.strategies import clear_dep_tree_cache

from .git_helpers import isolated_git_env


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


@pytest.fixture
def isolated_git_config(monkeypatch, tmp_path):
    """Apply :func:`isolated_git_env` to the current process."""
    for key, value in isolated_git_env(tmp_path).items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _fresh_analysis_caches():
    """Start and end every test with empty dependency-graph and discovery caches.

    Both are process-wide LRU caches keyed on the project root, and pytester runs
    in-process: a graph cached by one test must never answer for another.
    """
    clear_dep_tree_cache()
    yield
    clear_dep_tree_cache()


@pytest.fixture
def make_git_project(pytester, monkeypatch):
    """Factory: write ``{path: source}`` and an ini into pytester's directory, and commit it.

    Returns the pytester, ready for ``edit_file`` and ``runpytest``. The whole test —
    the setup commits *and* the plugin's own git calls during in-process
    ``runpytest`` — runs under :func:`isolated_git_env`, so the developer's git
    configuration cannot change what it observes.
    """
    for key, value in isolated_git_env(pytester.path / "git-home").items():
        monkeypatch.setenv(key, value)
    env = dict(os.environ)

    def make(files: dict[str, str], ini: str):
        for rel, source in {**files, ".gitignore": "__pycache__/\n"}.items():
            path = pytester.path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source))
        pytester.makeini(ini)
        for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "init"]):
            subprocess.run(["git", *args], cwd=pytester.path, env=env, check=True, capture_output=True)
        return pytester

    return make
