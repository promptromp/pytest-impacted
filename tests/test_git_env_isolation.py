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

from .conftest import isolated_git_env


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_suite_ignores_repo_locating_git_variables(tmp_path):
    decoy = tmp_path / "decoy"
    git_env = {**os.environ, **isolated_git_env(tmp_path)}
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=git_env)
    (decoy / "tracked.txt").write_text("x\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=decoy, check=True, env=git_env)
    index_before = (decoy / ".git/index").read_bytes()

    hook_env = {
        **os.environ,
        "GIT_DIR": str(decoy / ".git"),
        "GIT_INDEX_FILE": str(decoy / ".git/index"),
        "GIT_WORK_TREE": str(decoy),
    }
    # The tests that create repositories, including a bare one.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_git.py", "-k", "bare or real"],
        cwd=REPO_ROOT,
        env=hook_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    bare = subprocess.run(
        ["git", "-C", str(decoy), "config", "--get", "core.bare"],
        capture_output=True,
        text=True,
        env=git_env,
        check=True,
    )
    assert bare.stdout.strip() == "false"
    assert (decoy / ".git/index").read_bytes() == index_before
