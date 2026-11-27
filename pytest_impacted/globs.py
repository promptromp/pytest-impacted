"""File globs: the single matcher every file glob goes through, and the patterns it is used with.

Standard library only: the plugin checks ``--impacted-invalidate-all`` globs, and the ``impacted-tests`` CLI and
``--impacted-map`` (``file_map``) read theirs, without loading the strategies, networkx or the graph.
"""

from __future__ import annotations
import os
from collections.abc import Iterable
from fnmatch import fnmatchcase
from functools import lru_cache
from itertools import accumulate
from operator import or_
from pathlib import PurePosixPath


# Default dependency and configuration file basenames that trigger all tests when changed.
# Every file pytest reads its settings from is here too: addopts, markers and
# filterwarnings apply to every test. (The file pytest actually loaded — including one
# passed with ``-c`` — is matched as well; see DependencyFileImpactStrategy.)
DEFAULT_DEPENDENCY_FILE_PATTERNS: tuple[str, ...] = (
    # Lockfiles and project metadata. requirements.txt is also matched by a glob below,
    # but stays here so callers passing their own glob_patterns keep it.
    "uv.lock",
    "requirements.txt",
    "poetry.lock",
    "pdm.lock",
    "pixi.lock",
    "Pipfile",
    "Pipfile.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    # pytest configuration (pytest.toml and .pytest.toml since pytest 9)
    "pytest.ini",
    ".pytest.ini",
    "pytest.toml",
    ".pytest.toml",
    "tox.ini",
)

# Glob-style patterns (see matches_any_glob: right-anchored, so a bare-name glob matches
# at any depth) for files named by convention. No ``**`` segment: it would match at any depth
# below any directory of that name, and plain ``PurePath.match`` would read it as a ``*``.
DEFAULT_DEPENDENCY_GLOB_PATTERNS: tuple[str, ...] = (
    "*requirements*.txt",  # requirements.txt, requirements-dev.txt, test-requirements.txt, ...
    "*requirements*.in",  # pip-tools inputs
    "requirements*.lock",  # rye
    "*constraints*.txt",  # constraints.txt, test-constraints.txt, ...
    "pylock*.toml",  # PEP 751
    "requirements/*.txt",
    "requirements/*/*.txt",
    "requirements/*.in",
    "requirements/*/*.in",
)


def matches_any_glob(file_path: str, glob_patterns: Iterable[str]) -> bool:
    """Check if a repo-relative file path matches any glob pattern.

    Matching is right-anchored (:meth:`PurePosixPath.match`): ``*.json``
    matches a JSON file at any depth, ``config/*.json`` matches JSON files
    directly under any ``config`` directory, and a bare filename matches
    that basename anywhere. ``*`` never spans a ``/``. A ``**`` segment
    stands for any number of segments (:func:`_matches_recursively`):
    ``config/**/*.json`` matches a JSON file anywhere below a ``config``
    directory, ``docs/**`` every file in a ``docs`` tree. Tried after
    :meth:`PurePosixPath.match`, so a glob matches every file it did when ``**``
    was a plain ``*``, and one without a ``**`` segment matches as it always
    did, on each Python. This is the single matching convention for both
    built-in and user-supplied file patterns.
    """
    path = PurePosixPath(file_path)
    return any(path.match(glob_pat) or _matches_recursively(path, glob_pat) for glob_pat in glob_patterns)


def _matches_recursively(path: PurePosixPath, glob_pat: str) -> bool:
    """Whether *path* matches *glob_pat*, each ``**`` segment standing for any number of segments, none included.

    So ``a/**/b`` matches ``a/b`` and ``a/x/y/b``, and ``docs/**`` everything in a
    ``docs`` tree and ``docs`` itself, which git reports for a submodule or a symlink
    to a directory. Python 3.13's ``PurePath.full_match`` has the same ``**``, except
    that at the end it asks for a segment; the package supports 3.11 and 3.12, which lack it.
    Inside a segment ``**`` is a plain ``*`` (``a**b``), as in ``.gitignore``.
    ``False`` for a glob without a ``**`` segment: :func:`matches_any_glob` asks
    :meth:`PurePosixPath.match` first, whose quirks this does not share (3.11 lets
    ``*`` match the root, 3.12 a negated class span a ``/``).
    """
    if (pattern := _double_star_pattern(glob_pat)) is None:
        return False
    anchor, segments = pattern
    if anchor and anchor != path.anchor:  # an absolute glob is anchored at the root too
        return False
    # Anchored at the end: the last segment, unless a ** one, matches the file's name, or nothing does.
    if segments[-1] != "**" and not fnmatchcase(path.name, segments[-1]):
        return False
    return _segments_match(_below_anchor(path), segments)


@lru_cache(maxsize=1024)  # the configuration's globs: a few
def _double_star_pattern(glob_pat: str) -> tuple[str, tuple[str, ...]] | None:
    """*glob_pat*'s anchor and segments, normalised as :class:`PurePosixPath` does; ``None`` without a ``**`` one.

    A relative glob is anchored at the end only, so it gets a leading ``**``.
    """
    pattern = PurePosixPath(glob_pat)
    segments = _below_anchor(pattern)
    if "**" not in segments:
        return None
    return pattern.anchor, segments if pattern.anchor else ("**", *segments)


def _below_anchor(path: PurePosixPath) -> tuple[str, ...]:
    """*path*'s parts, without the root of an absolute one, which no glob segment matches."""
    return path.parts[1:] if path.anchor else path.parts


def _segments_match(parts: tuple[str, ...], segments: tuple[str, ...]) -> bool:
    """Whether all of *parts* match *segments* one by one, a ``**`` segment matching any number of them."""
    # matched[i]: the segments so far match parts[:i].
    matched = [True] + [False] * len(parts)
    for segment in segments:
        if segment == "**":
            matched = list(accumulate(matched, or_))  # any number of parts more
        else:
            matched = [False, *(matched[i] and fnmatchcase(part, segment) for i, part in enumerate(parts))]
    return matched[-1]


def matches_dependency_file(
    file_path: str,
    patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_FILE_PATTERNS,
    glob_patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_GLOB_PATTERNS,
) -> bool:
    """Check if a file path matches any dependency file pattern."""
    basename = PurePosixPath(file_path).name
    if basename in patterns:
        return True
    return matches_any_glob(file_path, glob_patterns)


def has_dependency_file_changes(
    changed_files: list[str],
    patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_FILE_PATTERNS,
    glob_patterns: tuple[str, ...] = DEFAULT_DEPENDENCY_GLOB_PATTERNS,
) -> bool:
    """Check if any changed files match the dependency/configuration file *name* patterns.

    Name patterns only: :class:`DependencyFileImpactStrategy` also counts the
    config file the running pytest loaded, which a name cannot identify.
    """
    return any(matches_dependency_file(f, patterns, glob_patterns) for f in changed_files)


#: The platform's path separator, which a user's glob may use for git's ``/``. A module name: a test patches it alone.
_SEP = os.sep


def normalise_glob(glob: str) -> str:
    """A user's glob as matched: stripped, the platform's separator as git's ``/`` (``config\\**`` on Windows)."""
    return glob.strip().replace(_SEP, "/")


def glob_refusal(glob: str) -> str | None:
    """Why :meth:`PurePosixPath.match` refuses *glob* (``empty pattern`` for ``""``, ``.``, ``./``), or ``None``.

    Asked when the configuration is read: met while matching, it ended the run in an INTERNALERROR.
    """
    try:
        PurePosixPath("x").match(glob)
    except ValueError as err:
        return str(err)
    return None


def parse_invalidation_patterns(
    patterns: str | Iterable[object], option: str = "--impacted-invalidate-all"
) -> tuple[str, ...]:
    """The ``--impacted-invalidate-all`` globs as matched (:func:`normalise_glob`).

    A lone string is one glob, not its characters. An entry that is no string (a TOML list may
    hold any value) is read as one, as the ``impacted-tests`` CLI reads it (``ini.PytestConfig.args``).

    Raises:
        ValueError: :meth:`PurePosixPath.match` refuses a glob (``""``, ``.``, ``./``), naming it and
            *option*, where it came from.
    """
    parsed = []
    for raw in [patterns] if isinstance(patterns, str) else patterns:
        if (refusal := glob_refusal(glob := normalise_glob(str(raw)))) is not None:
            raise ValueError(f"Invalid glob {raw!r} in {option}: no path can match it ({refusal}).")
        parsed.append(glob)
    return tuple(parsed)
