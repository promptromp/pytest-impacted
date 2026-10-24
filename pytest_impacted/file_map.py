"""``--impacted-map GLOB=PATH``: a changed file a glob matches counts as a change to PATH.

Import analysis cannot see that a test reads a data file. A mapping names the file the
data belongs to, and the run proceeds as if that file had changed too. It widens the
changed files before the pipeline, rather than being a strategy of its own: every rule
then sees the mapped path as it sees an edit (link_changed_files for a file no walk
reaches, package_members for an ``__init__.py``, the conftest rules, the dependency
files, extensions). A strategy re-running one of those rules on the path missed the rest.

A path stands for the files git knows there (:func:`~pytest_impacted.git.files_git_knows`),
where the changed files come from too: a directory for every file in it, whether a walk
reaches them or not, and never a virtualenv git ignores.
"""

import os
import posixpath
from collections import deque
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

from pytest_impacted.display import notify
from pytest_impacted.git import files_git_knows
from pytest_impacted.strategies import matches_any_glob
from pytest_impacted.traversal import _spelled_exactly, forget_listings


def parse_file_map(entries: Iterable[str]) -> list[tuple[str, str]]:
    """``GLOB=PATH`` entries as ``(glob, path)`` pairs, both POSIX, the path normalised and under the rootdir.

    Raises:
        ValueError: An entry lacks the ``=``, the glob or the path, has more than one
            ``=``, or its path is not inside the rootdir.
    """
    pairs = []
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError(f"Invalid --impacted-map entry {entry!r}: expected a GLOB=PATH string.")  # noqa: TRY004 — a config error
        glob, _, path = (part.strip().replace(os.sep, "/") for part in entry.partition("="))
        if not glob or not path or "=" in path:
            raise ValueError(f"Invalid --impacted-map entry {entry!r}: expected GLOB=PATH, one '='.")
        try:
            PurePosixPath("x").match(glob)
        except ValueError as err:  # "." and "/" leave no pattern
            raise ValueError(f"Invalid --impacted-map entry {entry!r}: GLOB {glob!r} matches no path ({err}).") from err
        posix = posixpath.normpath(path)
        if posixpath.isabs(posix) or os.path.isabs(posix) or posix == ".." or posix.startswith("../"):
            raise ValueError(f"Invalid --impacted-map entry {entry!r}: PATH must be relative, inside the rootdir.")
        pairs.append((glob, posix))
    return pairs


def map_changed_files(
    changed_files: list[str], file_map: Sequence[tuple[str, str]], root_dir: str | Path, session=None
) -> list[str]:
    """*changed_files*, then every file the mappings their globs match lead to, followed on.

    A mapped file another mapping's glob matches leads on, as an edit to it would. A path
    git knows no file at leads nowhere, said in a notice: a misspelt one would otherwise
    select nothing, silently.
    """
    if not file_map:
        return changed_files
    root_dir = Path(root_dir)  # the CLI's --root-dir is a string
    known = set(changed_files)
    added: list[str] = []
    queue = deque(changed_files)
    targets: dict[str, list[str]] = {}
    hits: dict[str, list[str]] = {}
    while queue:
        changed = queue.popleft()
        for glob, path in file_map:
            if not matches_any_glob(changed, [glob]):
                continue
            if path not in targets:
                targets[path] = _files_at(path, root_dir, session)
            if targets[path]:
                hits.setdefault(path, []).append(changed)
            for file in targets[path]:
                if file not in known:
                    known.add(file)
                    added.append(file)
                    queue.append(file)
    if hits:
        notify("Mapped file changes detected: " + "; ".join(_described(path, by) for path, by in hits.items()), session)
    return [*changed_files, *added]


def _files_at(path: str, root_dir: Path, session) -> list[str]:
    """The files *path* stands for (:func:`files_at`); a notice for none."""
    files = files_at(path, root_dir)
    if not files:
        notify(f"--impacted-map path {path} matches no file git knows under {root_dir}.", session)
    return files


def files_at(path: str, root_dir: Path) -> list[str]:
    """The files *path* stands for, as an edit to each would: itself, or every file git knows in it.

    Spelled as on disk, case included, by a fresh listing: on macOS git lists a wrong-case
    path it finds as an untracked file, and Python's import is case-sensitive.
    """
    forget_listings()  # this runs before the graph is built, or found in a cache
    if path != "." and os.path.lexists(root_dir / path) and not _spelled_exactly(root_dir, path):
        return []
    return sorted(files_git_knows(root_dir, path))


def _described(path: str, changed: list[str]) -> str:
    """``suite/test_bench.py (6 files: a, b, c, and 3 more)``, or ``suite/test_bench.py (a)``."""
    shown = ", ".join(changed[:3])
    if len(changed) == 1:
        return f"{path} ({shown})"
    more = f", and {len(changed) - 3} more" if len(changed) > 3 else ""
    return f"{path} ({len(changed)} files: {shown}{more})"


def unknown_paths(file_map: Sequence[tuple[str, str]], root_dir: str | Path) -> list[str]:
    """The mapped paths git knows no file at: a misspelt one, whether or not its glob matched a change."""
    return [path for path in dict.fromkeys(path for _, path in file_map) if not files_at(path, Path(root_dir))]
