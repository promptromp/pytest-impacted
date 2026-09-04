"""Git related functions."""

# Load-bearing, not cosmetic: ``Repo`` is only bound when GitPython imports, so
# postponed annotations are what keep this module importable without it.
from __future__ import annotations
import warnings
from enum import StrEnum
from pathlib import Path


try:
    from git import Repo

    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    warnings.warn(
        "GitPython package is not available. Git-related functionality will be disabled. "
        "To enable git functionality, install GitPython and ensure git CLI is available.",
        stacklevel=2,
    )


class InvalidGitRefError(ValueError):
    """A ref name was rejected before it could be handed to the git CLI."""


def validate_rev(rev: str) -> str:
    """Reject refs that git would parse as a command-line option, returning *rev* unchanged.

    Refs reach git as positional arguments, so a value such as ``--output=<path>``
    would be interpreted as an option rather than a revision.  Git itself forbids
    ref names beginning with ``-`` (see ``git check-ref-format``), so this rejects
    nothing a user could legitimately pass.
    """
    if rev.startswith("-"):
        raise InvalidGitRefError(f"Ref names may not begin with '-', got: {rev!r}.")
    return rev


#: Tells git that every following token is an operand, never an option.
#: Supported by git >= 2.24.
END_OF_OPTIONS = "--end-of-options"


def rev_args(*revs: object) -> list[str]:
    """Build the positional revision arguments for a ``git`` invocation.

    Every revision handed to the git CLI must go through here, so that the
    option-injection guard is applied uniformly rather than remembered at each
    call site.  Revisions are stringified (callers may pass GitPython ``Commit``
    or ``Reference`` objects), validated by :func:`validate_rev`, and prefixed
    with :data:`END_OF_OPTIONS`.

    The two guards are deliberately redundant: ``validate_rev`` rejects an
    option-like value outright so the user gets a clear error instead of a
    confusing git failure, while ``--end-of-options`` makes the guarantee
    structural — git will not parse these tokens as options even if a value
    ever slips past the string check.
    """
    return [END_OF_OPTIONS, *(validate_rev(str(rev)) for rev in revs)]


class GitMode(StrEnum):
    """Git modes for the plugin."""

    UNSTAGED = "unstaged"
    BRANCH = "branch"


class GitStatus(StrEnum):
    """Git statuses.

    Reference: `man git-diff`

    """

    ADDED = "A"
    COPIED = "C"
    DELETED = "D"
    MODIFIED = "M"
    RENAMED = "R"
    TYPE_CHANGE = "T"
    UNMERGED = "U"
    UNKNOWN = "X"
    PAIRING_BROKEN = "B"

    @classmethod
    def from_git_diff_name_status(cls, status: str) -> GitStatus:
        """Create a GitStatus from a ``--name-status`` token; ``R100``/``C85`` carry a similarity score."""
        return cls(status[:1])


# Every reported change counts, deletions included (a removed conftest.py or
# lockfile still matters to the glob-based strategies). Only git's "cannot say"
# statuses are excluded.
_NON_IMPACTFUL_STATUSES = frozenset({GitStatus.UNKNOWN, GitStatus.PAIRING_BROKEN})


class Change:
    """A change to a git repository file."""

    def __init__(self, path: str, status: GitStatus | None = None):
        self.path = path
        self.status = status

    def __str__(self) -> str:
        return f"{self.status}\t{self.path}"


class ChangeSet:
    """A set of changes to files in a git repository."""

    def __init__(self, changes: list[Change]):
        self.changes = changes

    def __str__(self) -> str:
        return "\n".join(str(change) for change in self.changes)

    @classmethod
    def from_git_diff_name_status_output(cls, output: str) -> ChangeSet:
        """Create a ChangeSet from ``git diff --name-status -z --no-renames`` output.

        Records are NUL-separated ``status, path`` pairs. ``-z`` matters:
        without it git C-quotes any path containing non-ASCII, tab, quote or
        backslash characters (``core.quotePath``), and the quoted string would
        never match a file on disk. ``--no-renames`` matters too: a rename is
        then a deletion plus an addition, so no record ever carries two paths.

        Example (NULs shown as ``|``)::

            M|setup.py|D|setup.cfg|A|new.py|
        """
        tokens = output.split("\0")
        if tokens and tokens[-1] == "":
            tokens.pop()  # the terminator of the final record, not an empty field
        pairs = zip(tokens[::2], tokens[1::2], strict=False)
        return cls([Change(path, GitStatus.from_git_diff_name_status(status)) for status, path in pairs])


def find_repo(path: str | Path) -> Repo:
    """Find the git repository by searching the given path and its parent directories.

    Uses ``search_parent_directories=True`` so the caller does not need to be
    at the exact git root — essential for monorepo layouts where the Python
    project lives in a subdirectory.
    """
    return Repo(path=Path(path), search_parent_directories=True)


def normalize_git_paths(file_paths: list[str], git_root: Path, working_dir: Path) -> list[str]:
    """Convert git-root-relative file paths to working-dir-relative paths.

    Git returns paths relative to the repository root.  When *working_dir* differs
    from *git_root* (monorepo layout), the paths must be rebased so that downstream
    code calling ``os.path.abspath()`` resolves them correctly.

    Files that fall outside *working_dir* are returned as absolute paths so they
    can still be matched (though they typically won't belong to any discovered module).
    """
    if git_root == working_dir:
        return file_paths  # Fast path: no conversion needed

    result: list[str] = []
    for file_path in file_paths:
        abs_path = git_root / file_path
        try:
            result.append(str(abs_path.relative_to(working_dir)))
        except ValueError:
            # File is outside the working directory — use absolute path
            result.append(str(abs_path))
    return result


def find_impacted_files_in_repo(repo_dir: str | Path, git_mode: GitMode, base_branch: str | None) -> list[str] | None:
    """Find impacted files in the repository. The definition of impacted is dependent on the git mode:

    UNSTAGED:
        - All files with uncommitted changes, whether staged with ``git add`` or not.
        - Any untracked files are also included.

    BRANCH:
        - All files that have been modified in the current branch, relative to the base branch.
        - This does *not* include untracked files as the expectation is that this is used for committed changes.

    :param repo_dir: path to the project directory (may be a subdirectory of the git root).
    :param git_mode: the git mode to use.
    :param base_branch: the base branch to compare against.

    """
    if not GIT_AVAILABLE:
        warnings.warn(
            "Git functionality is disabled because GitPython is not available. "
            "To enable git functionality, install GitPython and ensure git CLI is available.",
            stacklevel=2,
        )
        return None

    repo = find_repo(repo_dir)
    if repo.bare:
        raise ValueError(f"{repo.git_dir} is a bare repository; pytest-impacted needs a working tree to diff.")

    match git_mode:
        case GitMode.UNSTAGED:
            impacted_files = impacted_files_for_unstaged_mode(repo)

        case GitMode.BRANCH:
            if not base_branch:
                raise ValueError("Base branch is required for running in BRANCH git mode")

            impacted_files = impacted_files_for_branch_mode(repo, base_branch=base_branch)

        case _:
            raise ValueError(f"Invalid git mode: {git_mode}")

    if not impacted_files:
        return None

    # Normalize git-root-relative paths to working-dir-relative paths
    if repo.working_tree_dir is not None:
        git_root = Path(repo.working_tree_dir).resolve()
        working_dir = Path(repo_dir).resolve()
        impacted_files = normalize_git_paths(impacted_files, git_root, working_dir)
    return sorted(set(impacted_files))


#: ``--name-status -z`` is what :meth:`ChangeSet.from_git_diff_name_status_output`
#: parses (``-z`` keeps non-ASCII paths unquoted). ``--no-renames`` reports a
#: rename as a deletion plus an addition — the same paths, without git's
#: similarity pass and independent of each developer's ``diff.renames`` setting.
_DIFF_FLAGS = ("--name-status", "-z", "--no-renames")


def _name_status_diff(repo: Repo, *args: str) -> ChangeSet:
    """Run ``git diff`` with the project's fixed flags and parse the result.

    Every diff in this module goes through here so the flags cannot drift
    between call sites. *args* follow the flags, so revisions produced by
    :func:`rev_args` keep their ``--end-of-options`` guard in front of them.
    """
    return ChangeSet.from_git_diff_name_status_output(repo.git.diff(*_DIFF_FLAGS, *args))


def _impactful_paths(change_set: ChangeSet) -> list[str]:
    """The file paths from *change_set* whose change could affect test outcomes."""
    return [
        change.path for change in change_set.changes if change.path and change.status not in _NON_IMPACTFUL_STATUSES
    ]


def impacted_files_for_unstaged_mode(repo: Repo) -> list[str]:
    """Get the impacted files when in the UNSTAGED git mode.

    Uncommitted work is the union of three views: the index against HEAD
    (``git diff --cached``, which also works before the first commit and
    catches a staged edit whose file was reverted on disk), the working tree
    against the index (``git diff``), and untracked files. Diffing only one
    of them misses the others.
    """
    staged = _impactful_paths(_name_status_diff(repo, "--cached"))
    unstaged = _impactful_paths(_name_status_diff(repo))
    return [*staged, *unstaged, *_untracked_files(repo)]


def _untracked_files(repo: Repo) -> list[str]:
    """Untracked, non-ignored files, read with ``-z`` so paths arrive verbatim.

    GitPython's ``Repo.untracked_files`` parses the quoted ``git status``
    format and can raise on some non-ASCII names; ``ls-files -z`` needs no
    unquoting.
    """
    output = repo.git.ls_files("--others", "--exclude-standard", "-z")
    return [path for path in output.split("\0") if path]


def impacted_files_for_branch_mode(repo: Repo, base_branch: str) -> list[str]:
    """Get the impacted files when in the BRANCH git mode."""

    try:
        current_ref = repo.head.reference
    except TypeError:
        # Detached HEAD state (common in CI) — fall back to HEAD commit
        current_ref = repo.head.commit

    return _impactful_paths(_name_status_diff(repo, *rev_args(base_branch, current_ref)))
