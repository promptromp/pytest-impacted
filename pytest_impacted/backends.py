"""The parsers that read a project's imports: one interface, two backends, and the one place a run chooses.

``build_dep_tree`` asks a :class:`Backend` what each source file's import statements may import and what its calls
import by name, and never names a parser:

- :class:`PythonBackend` reads every file, with the standard library's ``ast`` (``parsing``, ``type_only``,
  ``dynamic_imports``);
- :class:`RustBackend` reads with pytest-impacted-rs (ruff's parser, in parallel), and leaves out what it cannot
  read: a file ruff rejects though CPython compiles it, one not UTF-8, one too large, every file when the installed
  extension predates what is asked;
- :class:`WithFallback` hands what one backend leaves out to another, so the extension never finds fewer imports
  than Python does.

:func:`backend_for` picks: the extension, with Python behind it, when it is installed and can honour the options;
Python alone otherwise, or when asked for (``--impacted-backend=python``). Both find the same imports — the parity
tests run each case through both — so the choice changes how fast tests are selected, not which.
"""

import logging
from collections import Counter
from collections.abc import Callable, Sequence
from typing import NamedTuple, Protocol, TypeVar

from pytest_impacted import _rust
from pytest_impacted.dynamic_imports import TABLE, DynamicImports, parse_dynamic_imports
from pytest_impacted.options import BACKENDS
from pytest_impacted.parsing import parse_file_imports, split_file_imports


logger = logging.getLogger(__name__)

#: What ``--impacted-backend`` may ask for: the fastest backend that can honour the options, or Python's whatever is
#: installed (to compare the two, or to rule the extension out).
CHOICES = BACKENDS


class Source(NamedTuple):
    """A file to read, and the module name its relative imports resolve from."""

    path: str
    name: str
    is_package: bool


#: A file's import candidates: those that run, and (when asked for) those only type checkers read.
Split = tuple[list[str], list[str]]
Answer = TypeVar("Answer")


class Backend(Protocol):
    """Reads import candidates; each answer is in the order of *sources*, ``None`` for a file it leaves out."""

    #: How the report header names it (``backend=``).
    name: str

    def imports(self, sources: Sequence[Source], *, find_type_only: bool) -> list[Split | None]:
        """Each file's import statements' candidates (``from pkg import name``: ``pkg`` and ``pkg.name``), split
        into runtime and type-only ones when *find_type_only* (see ``type_only``), all runtime ones otherwise."""
        ...

    def imports_by_name(self, sources: Sequence[Source]) -> list[DynamicImports | None]:
        """What each file's calls import by name (see ``dynamic_imports``)."""
        ...


class PythonBackend:
    """The standard library's ``ast``, file by file: reads every file it can open, and leaves none out."""

    name = "python (ast)"

    def imports(self, sources: Sequence[Source], *, find_type_only: bool) -> list[Split | None]:
        if find_type_only:
            return [split_file_imports(path, name, is_package) for path, name, is_package in sources]
        return [(parse_file_imports(path, name, is_package), []) for path, name, is_package in sources]

    def imports_by_name(self, sources: Sequence[Source]) -> list[DynamicImports | None]:
        return [parse_dynamic_imports(*source) for source in sources]


class RustBackend:
    """pytest-impacted-rs, in parallel: ``None`` for each file it is not handed or hands back.

    The ``fast`` extra is unpinned, so the installed extension may predate a function: it then leaves every file
    out (see ``_rust``), never fails the run.
    """

    name = "rust (ruff parser + rayon)"

    def imports(self, sources: Sequence[Source], *, find_type_only: bool) -> list[Split | None]:
        if not _rust.uses_rust(find_type_only):
            return [None] * len(sources)
        taken = [_rust.takes(path, name) for path, name, _ in sources]  # a non-UTF-8 name: PyO3 cannot be handed it
        read = iter(self._parse([s for s, take in zip(sources, taken, strict=True) if take], find_type_only))
        return [next(read) if take else None for take in taken]

    def _parse(self, sources: list[Source], find_type_only: bool) -> list[Split | None]:
        modules = [tuple(source) for source in sources]
        if _rust.RUST_PARSES_EACH:  # since 0.41: each file parsed, or handed back
            return [
                None if split is None else (split[0], split[1] if find_type_only else [])
                for split in _rust.rust_parse_imports_each(modules, find_type_only)
            ]
        # An older extension answers by module name, and reads every file: a name twice in one batch is left out.
        unique = {name for name, times in Counter(s.name for s in sources).items() if times == 1}
        given = [m for m, source in zip(modules, sources, strict=True) if source.name in unique]
        if find_type_only:
            split = _rust.rust_parse_all_imports_split(given)
            return [tuple(split[s.name]) if s.name in unique else None for s in sources]  # type: ignore[misc]
        found = _rust.rust_parse_all_imports(given)
        return [(found[s.name], []) if s.name in unique else None for s in sources]

    def imports_by_name(self, sources: Sequence[Source]) -> list[DynamicImports | None]:
        """The extension's reading of the table ``dynamic_imports.TABLE``: an older extension may meet a kind of
        name it does not know (ValueError), a row of another shape (TypeError), a number out of range
        (OverflowError) — its to refuse, leaving every file to Python."""
        if not _rust.RUST_PARSES_DYNAMIC_IMPORTS:
            return [None] * len(sources)
        taken = [_rust.takes(path, name) for path, name, _ in sources]
        try:
            read = iter(
                _rust.rust_parse_all_dynamic_imports(
                    [tuple(s) for s, take in zip(sources, taken, strict=True) if take], TABLE
                )
            )
        except UnicodeEncodeError:  # a ValueError too: a string ``takes`` should have kept back
            logger.debug("pytest-impacted-rs was handed a string that is not UTF-8; Python finds imports by name")
            return [None] * len(sources)
        except (TypeError, ValueError, OverflowError):
            logger.debug("pytest-impacted-rs cannot read the loader table; Python finds the imports by name")
            return [None] * len(sources)
        return [_dynamic(next(read)) if take else None for take in taken]


def _dynamic(lists: tuple[list[str], list[str], list[str]] | None) -> DynamicImports | None:
    return None if lists is None else DynamicImports(*lists)


class WithFallback:
    """*first*, and *rest* for each file *first* leaves out: never fewer imports than *rest* finds alone."""

    def __init__(self, first: Backend, rest: Backend):
        self.first, self.rest = first, rest
        self.name = first.name

    def imports(self, sources: Sequence[Source], *, find_type_only: bool) -> list[Split | None]:
        found = self.first.imports(sources, find_type_only=find_type_only)
        return self._fill(found, sources, lambda left: self.rest.imports(left, find_type_only=find_type_only))

    def imports_by_name(self, sources: Sequence[Source]) -> list[DynamicImports | None]:
        return self._fill(self.first.imports_by_name(sources), sources, self.rest.imports_by_name)

    @staticmethod
    def _fill(
        found: list[Answer | None], sources: Sequence[Source], read_rest: Callable[[list[Source]], list[Answer | None]]
    ) -> list[Answer | None]:
        if left := [i for i, answer in enumerate(found) if answer is None]:
            for i, answer in zip(left, read_rest([sources[i] for i in left]), strict=True):
                found[i] = answer
        return found


PYTHON = PythonBackend()
RUST = RustBackend()


def backend_for(ignore_type_checking_imports: bool = False, requested: str = "auto") -> Backend:
    """The backend a run uses: the extension, Python behind it, when installed and new enough for the options
    (``_rust.uses_rust``) and not ruled out by *requested* (``--impacted-backend``); Python alone otherwise."""
    if requested not in CHOICES:
        raise ValueError(f"backend must be one of {', '.join(CHOICES)}, not {requested!r}")
    if requested == "python" or not _rust.uses_rust(ignore_type_checking_imports):
        return PYTHON
    return WithFallback(RUST, PYTHON)
