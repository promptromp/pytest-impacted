"""The suite's throwaway git repositories must never touch the repository it runs in.

Git exports repo-locating variables (``GIT_DIR``, ``GIT_INDEX_FILE``, ...) to
hooks, and the pre-commit hook runs this suite from inside a commit. Inherited,
they point every ``git init``/``git add`` in the tests at the real repository:
from a linked worktree that overwrote its index and re-initialised the shared
``.git`` as bare.
"""

import os
import subprocess
import sys
from pathlib import Path

from .git_helpers import isolated_git_env


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_suite_ignores_repo_locating_git_variables(tmp_path):
    decoy = tmp_path / "decoy"
    git_env = {**os.environ, **isolated_git_env(tmp_path)}
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=git_env)
    (decoy / "tracked.txt").write_text("x\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=decoy, check=True, env=git_env)
    index_before = (decoy / ".git/index").read_bytes()

    hook_env = {
        # PYTEST_ADDOPTS: a developer's own options (e.g. --impacted, -n) must not change the inner run.
        **{k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"},
        "GIT_DIR": str(decoy / ".git"),
        "GIT_INDEX_FILE": str(decoy / ".git/index"),
        "GIT_WORK_TREE": str(decoy),
    }
    # A bare ``git init``, and a test built on the session-scoped repository template —
    # set up before any function-scoped fixture, which is why the scrub is in pytest_configure.
    targets = [
        "tests/test_git.py::test_bare_repo_is_a_clear_error",
        "tests/test_git.py::test_unstaged_mode_clean_real_repo_returns_none",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets],
        cwd=REPO_ROOT,
        env=hook_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout, result.stdout  # the bare test is parametrized over both git modes
    bare = subprocess.run(
        ["git", "-C", str(decoy), "config", "--get", "core.bare"],
        capture_output=True,
        text=True,
        env=git_env,
        check=True,
    )
    assert bare.stdout.strip() == "false"
    assert (decoy / ".git/index").read_bytes() == index_before


def test_isolated_git_env_switches_off_auto_maintenance(tmp_path):
    """Recent git detaches maintenance after a commit, racing copies of the repo (a real CI flake)."""
    env = {**os.environ, **isolated_git_env(tmp_path)}
    for key, expected in (("maintenance.auto", "false"), ("gc.auto", "0")):
        value = subprocess.run(["git", "config", "--get", key], env=env, capture_output=True, text=True, check=True)
        assert value.stdout.strip() == expected
