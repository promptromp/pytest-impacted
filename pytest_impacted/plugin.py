import importlib
import os
import re
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pytest import Config, DoctestItem, ExitCode, Parser, StashKey, UsageError

from pytest_impacted import _rust
from pytest_impacted.display import warn
from pytest_impacted.extensions import (
    check_ext_config,
    discover_extension_metadata,
    enabled_extension_metadata,
    get_ext_cli_flag,
    get_ext_ini_name,
)
from pytest_impacted.modes import GitMode
from pytest_impacted.options import BACKENDS as OPTIONS_BACKENDS, OPTIONS, Kind, Option, Unimpacted


def _lazily(name: str) -> Callable[[], ModuleType]:
    """A module, imported at the first call, never at the import of this one — with every warning silenced.

    The first call comes inside a pytest hook, where the user's filters apply: under ``filterwarnings = error`` a
    warning networkx or GitPython gave while loading would fail the run, which loading them with the
    entry point, before any filter applied, never did.
    """

    def module() -> ModuleType:
        if name not in sys.modules:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                importlib.import_module(name)
        return sys.modules[name]

    return module


# The analysis and what it needs (networkx, GitPython, which runs ``git version`` as it loads) load only
# once --impacted asks for them: this plugin is a pytest11 entry point, imported by every pytest run, with
# --impacted or without, and loading them there doubled the start-up of a run with one test (127 ms -> 249 ms).
# Functions, not module-like objects: pytest probes every attribute of a plugin module for fixtures, which would
# import such an object's module at start-up.
_api = _lazily("pytest_impacted.api")
_file_map = _lazily("pytest_impacted.file_map")
_git = _lazily("pytest_impacted.git")
_strategies = _lazily("pytest_impacted.strategies")
_parsing = _lazily("pytest_impacted.parsing")
_traversal = _lazily("pytest_impacted.traversal")
_validation = _lazily("pytest_impacted.validation")


NOT_IMPACTED = "not impacted by the changes"
# Where a pytest-xdist worker reports its deselection to the controller, and the tests no analysis judged.
_WORKER_OUTPUT_KEY = "pytest_impacted_deselected"
_WORKER_UNJUDGED_KEY = "pytest_impacted_unjudged"
# Where the controller hands each pytest-xdist worker its analysis: the impacted test files, and the test files the
# walks name (any other a worker collects is no analysis's to judge).
_WORKER_INPUT_KEY = "pytest_impacted_tests"
_WORKER_KNOWN_KEY = "pytest_impacted_known_tests"


# How many tests this plugin deselected, and how many it ran unjudged (see :func:`_unjudged_items`).
_DESELECTION = StashKey[int]()
_UNJUDGED = StashKey[int]()
_SESSION = StashKey[pytest.Session]()


@dataclass(frozen=True)
class _Handoff:
    """The controller's analysis for every pytest-xdist worker (``None``: run every test)."""

    tests: list[str] | None
    known: list[str]


# The controller's handoff, or None for none: each worker then analyses for itself.
_HANDOFF = StashKey[_Handoff | None]()
# The items this plugin judged not impacted, for pytest_deselect_unimpacted to deselect, and those no analysis
# judged (outside the analysed directories), for it to count once -k and -m have filtered.
_UNIMPACTED_ITEMS = StashKey[set[pytest.Item]]()
_UNJUDGED_ITEMS = StashKey[set[pytest.Item]]()


def pytest_addoption(parser: Parser):
    """pytest hook to add command line options: each of :data:`~pytest_impacted.options.OPTIONS`, with its ini key,
    then the options of the extensions discovered."""
    group = parser.getgroup("impacted")
    for option in OPTIONS:
        group.addoption(option.flag, dest=option.setting, help=option.help, **_option_kwargs(option))
        ini_help = option.ini_help or f"default value for {option.flag}"
        parser.addini(option.setting, help=ini_help, **_ini_kwargs(option))

    # Register config options from discovered extensions
    for ext in discover_extension_metadata():
        for opt in ext.config_options:
            flag = get_ext_cli_flag(ext.name, opt.name)
            ini_name = get_ext_ini_name(ext.name, opt.name)
            add_kwargs: dict[str, Any] = {
                "default": None,
                "dest": ini_name,
                "help": f"[ext:{ext.name}] {opt.help}",
            }
            if opt.type is bool:
                add_kwargs["action"] = "store_true"
            group.addoption(flag, **add_kwargs)
            ini_default = str(opt.default) if opt.default is not None else None
            parser.addini(ini_name, help=opt.help, default=ini_default)


def _option_kwargs(option: Option) -> dict[str, Any]:
    """How the command line takes *option*: ``None`` (or ``[]``) when it is not given, so the ini setting applies."""
    if option.kind is Kind.FLAG:
        return {"action": "store_true", "default": None}
    if option.kind is Kind.LIST:
        return {"action": "append", "default": [], "metavar": option.metavar}
    if option.kind is Kind.CHOICE:
        optional = {"nargs": "?"} if option.optional_value else {}
        return {"choices": list(option.choices), "default": None} | optional
    return {"default": None, "metavar": option.metavar}


def _ini_kwargs(option: Option) -> dict[str, Any]:
    """How the pytest configuration takes *option*."""
    if option.kind is Kind.FLAG:
        return {"type": "bool", "default": bool(option.default)}
    if option.kind is Kind.LIST:
        return {"type": "args", "default": []}
    return {"default": option.default}


def pytest_configure(config: Config):
    """pytest hook to configure the plugin.

    This is called after the command line options have been parsed.

    """
    validate_config(config)

    config.addinivalue_line(
        "markers",
        "impacted(state): mark test as impacted by the state of the git repository",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_report_header(config: Config) -> list[str]:
    """Add pytest-impacted config to pytest header."""
    get_option = partial(get_option_from_config, config)
    ignore_type_checking_imports = get_option("impacted_ignore_type_checking_imports")
    ext_names = [e.name for e in discover_extension_metadata()]
    header = [
        *(f"{option.setting}={get_option(option.setting)}" for option in OPTIONS if option.in_header),
        f"backend={_backend(bool(ignore_type_checking_imports), get_option('impacted_backend') or 'auto')}",
    ]
    if ext_names:
        header.append(f"extensions={','.join(ext_names)}")
    return ["pytest-impacted: " + ", ".join(header), *_header_warnings(config)]


def _header_warnings(config: Config) -> list[str]:
    """The warnings the report header carries — or, when pytest leaves it out, the terminal summary.

    Both are written by the controller, so they survive pytest-xdist, whose workers run collection and whose
    terminal output is discarded.
    """
    get_option = partial(get_option_from_config, config)
    if not get_option("impacted"):
        return []
    lines: list[str] = []
    if _git().GIT_AVAILABLE and (file_map := _file_map_or_none(get_option("impacted_map"))):
        unknown = _unknown_paths_or_none(file_map, config.rootpath)
        lines.extend(
            f"pytest-impacted: WARNING: --impacted-map path {path} matches no file git knows." for path in unknown
        )
    python_asked_for = get_option("impacted_backend") == "python"  # the extension is not used: nothing to upgrade
    if not python_asked_for and (
        outdated := _rust.outdated_for(bool(get_option("impacted_ignore_type_checking_imports")))
    ):
        lines.append(f"pytest-impacted: WARNING: {outdated}")
    if not _git().GIT_AVAILABLE:
        lines.append(f"pytest-impacted: WARNING: {_git().GitUnavailableError()} Running every test.")
    return lines


def pytest_sessionstart(session: pytest.Session) -> None:
    """Keep the session for ``pytest_configure_node``, which gets none."""
    session.config.stash[_SESSION] = session


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """pytest-xdist hook, on the controller: analyse once, and hand every worker the result.

    Workers analysing for themselves could disagree (a file written meanwhile), and
    xdist aborts a run whose workers collect different tests. This runs before any
    worker starts, and here, unlike in a worker, a notice reaches the terminal.

    Handed on only when every worker would compute the same, so never worse than each
    worker analysing for itself: with only the built-in strategies (an extension may read
    what only a worker has: ``session.items``, ``workerinput``), workers started here with
    this interpreter (``--tx`` may start one that parses differently), and an analysis that
    succeeded. Otherwise each worker analyses, and skips rather than deselects
    (``pytest_collection_modifyitems``): workers' skips may differ, their collections not.
    """
    config = node.config
    session = config.stash.get(_SESSION, None)
    if session is None or not get_option_from_config(config, "impacted"):
        return
    if _HANDOFF not in config.stash:
        config.stash[_HANDOFF] = _handoff(session, config)
    if (handoff := config.stash[_HANDOFF]) is not None:
        node.workerinput[_WORKER_INPUT_KEY] = handoff.tests
        node.workerinput[_WORKER_KNOWN_KEY] = handoff.known


def _handoff(session: pytest.Session, config: Config) -> _Handoff | None:
    """The controller's analysis for the workers, or None, leaving each to analyse for itself."""
    if enabled_extension_metadata(get_option_from_config(config, "impacted_disable_ext") or []):
        return None
    # ``-n`` starts plain ``popen`` workers; any other spec may be another interpreter or host.
    if any(re.sub(r"^\d+\*", "", spec) != "popen" for spec in config.getoption("tx", default=None) or []):
        return None
    try:
        tests = _analyse(session, config)
        return _Handoff(tests, [] if tests is None else _known_tests(config))  # None: every test runs, none judged
    except (Exception, pytest.fail.Exception, pytest.skip.Exception) as err:  # noqa: BLE001 — warned, and retried
        # Raised here it would escape xdist's session start, and pytest would skip every
        # pytest_sessionfinish (no JUnit XML, no combined coverage); in a worker it may
        # not even happen. So each worker analyses for itself, raising it there if it must.
        warn(
            f"pytest-impacted: the analysis failed on the controller ({err!r}); each worker analyses for itself.",
            session,
        )
        return None


def _backend(ignore_type_checking_imports: bool, requested: str) -> str:
    """The parser the analysis uses, for the report header, which pytest writes on every run, ``--impacted`` or not:
    decided in ``_rust``, as ``backends.backend_for`` decides, without loading the parsers. An invalid *requested*
    is validate_config's."""
    if requested not in OPTIONS_BACKENDS:
        return requested
    return _rust.RUST_LABEL if _rust.parses_with_rust(ignore_type_checking_imports, requested) else _rust.PYTHON_LABEL


def _analyse(session: pytest.Session, config: Config) -> list[str] | None:
    """The impacted test files, relative to the rootdir, or ``None`` to run every test.

    Relative, so a pytest-xdist worker in another directory reads them as well.
    """
    # Any: ini values are untyped, and validate_config has checked the ones that must be set.
    get_option: Callable[[str], Any] = partial(get_option_from_config, config)
    root_dir = config.rootpath
    strategy = _api().build_strategy_with_extensions(
        watch_dep_files=not get_option("no_impacted_dep_files"),
        invalidate_all_patterns=get_option("impacted_invalidate_all") or [],
        conftest_imports=get_option("impacted_conftest_imports"),
        disabled=get_option("impacted_disable_ext") or [],
        ext_config=_collect_ext_config(config),
        session=session,
    )
    try:
        impacted_tests = _api().get_impacted_tests(
            impacted_git_mode=get_option("impacted_git_mode"),
            impacted_base_branch=get_option("impacted_base_branch"),
            root_dir=root_dir,
            ns_module=get_option("impacted_module"),
            tests_dir=get_option("impacted_tests_dir"),
            session=session,
            strategy=strategy,
            use_merge_base=not get_option("impacted_no_merge_base"),
            file_map=_file_map().parse_file_map(get_option("impacted_map") or []),
            pythonpath=_pythonpath(config),
            ignore_type_checking_imports=bool(get_option("impacted_ignore_type_checking_imports")),
            backend=get_option("impacted_backend") or "auto",
        )
    except _git().GitError as err:  # validate_config asked first; git may still refuse later (a ref deleted since)
        # Under pytest-xdist the controller's _handoff warns of it and each worker raises it again: rare enough,
        # and raising it there would skip every pytest_sessionfinish (see _handoff).
        raise UsageError(str(err)) from err
    except _git().GitUnavailableError as err:
        # Fail open: with the changes unknown, every test may be impacted. Not
        # warnings.warn — `filterwarnings = error` would turn it into a crash.
        # When git was missing at import, pytest_report_header has already said
        # so; only a failure discovered now needs reporting here.
        if _git().GIT_AVAILABLE:
            warn(f"pytest-impacted: {err} Running every test.", session)
        return None
    # The analysis names files under the canonical root; an extension may use the one it was given.
    roots = [_traversal().canonical_root(root_dir), root_dir]
    return [_relative_to(roots, test) for test in impacted_tests or []]


def _known_tests(config: Config) -> list[str]:
    """The test files the walks name, relative to the rootdir: the files the analysis may select or leave out.

    A test pytest collects from any other file no analysis judges, and it runs: one not named as a test module
    (``test_*.py``, ``*_test.py``, or under a ``test``/``tests`` directory) that a ``python_files`` pattern collects,
    or one in a package directory whose name is no identifier, which the package walk does not enter. Discovery
    alone (cached, no parsing), so a test file the graph found only through an import (rare: tests are not imported)
    runs too, unjudged — the safe side. Files outside the rootdir are left out: they are outside the analysed
    directories too, so they run anyway.
    """
    get_option = partial(get_option_from_config, config)
    tests_dir = get_option("impacted_tests_dir")
    found = _traversal().discover_project_modules(
        _validation().normalized_module(str(get_option("impacted_module"))),
        _traversal().path_to_package_name(tests_dir) if tests_dir else None,
        root_dir=config.rootpath,
    )
    is_test, real_path = _parsing().is_test_module, _traversal().real_path
    root = real_path(config.rootpath)
    real = (real_path(path) for name, path in found.modules.items() if is_test(name))
    return sorted(path.relative_to(root).as_posix() for path in real if path.is_relative_to(root))


def _pythonpath(config: Config) -> list[Path]:
    """pytest's ``pythonpath`` entries; none when no plugin declares the setting.

    Before pytest 8.4 the setting belongs to the ``python_path`` plugin, which ``-p no:python_path``
    blocks, and ``getini`` then raises. Its entries are then on no ``sys.path`` either.
    """
    try:
        return config.getini("pythonpath")
    except ValueError:
        return []


def _relative_to(roots: list[Path], test: str) -> str:
    """*test* relative to the first of *roots* it is under; as it is if under none, or relative already."""
    path = Path(test)
    if path.is_absolute():
        for root in roots:
            if path.is_relative_to(root):
                return os.path.relpath(path, root)
    return test


def _file_map_or_none(entries) -> list[tuple[str, str]]:
    try:
        return _file_map().parse_file_map(entries or [])
    except ValueError:
        return []  # validate_config has said so


def _unknown_paths_or_none(file_map: list[tuple[str, str]], root_dir: Path) -> list[str]:
    """For the header: the mapped paths git knows nothing at; none when git cannot tell."""
    try:
        return _file_map().unknown_paths(file_map, root_dir)
    except Exception:  # noqa: BLE001 — a header line, never a failure of its own; the run reports git
        return []


def pytest_collection_modifyitems(session, config, items):
    """pytest hook to modify the collected test items.

    This is called after the tests have been collected and before
    they are run. It marks them, before ``-k`` and ``-m`` filter (``-m impacted``);
    ``pytest_deselect_unimpacted`` deselects after them. A pytest-xdist worker takes
    the controller's analysis (``pytest_configure_node``).

    """
    get_option = partial(get_option_from_config, config)
    if not get_option("impacted"):
        return
    # Afresh: a second collection on one config must not count the first's deselection.
    config.stash[_UNIMPACTED_ITEMS] = set()
    config.stash[_UNJUDGED_ITEMS] = set()
    _record(config, 0)
    _report(config, _UNJUDGED, _WORKER_UNJUDGED_KEY, 0)
    workerinput = getattr(config, "workerinput", None)
    handed_on = workerinput is not None and _WORKER_INPUT_KEY in workerinput
    impacted_tests = workerinput[_WORKER_INPUT_KEY] if handed_on else _analyse(session, config)
    if impacted_tests is None:
        return
    root_dir = config.rootpath
    known = workerinput[_WORKER_KNOWN_KEY] if handed_on else _known_tests(config)
    real_root = _traversal().real_path(root_dir)
    unjudged = _unjudged_items(items, _analysed_dirs(config), real_root, {real_root / test for test in known})
    config.stash[_UNJUDGED_ITEMS] = unjudged
    impacted_items = _impacted_items(items, impacted_tests, root_dir) | unjudged
    # A worker analysing for itself skips: another may judge differently, and xdist stops a
    # run whose workers collect different tests, not one whose skips differ.
    analysed_alone = workerinput is not None and not handed_on
    skip = get_option("impacted_unimpacted") == Unimpacted.SKIP or analysed_alone
    unimpacted: set[pytest.Item] = set()
    for item in items:
        if item in impacted_items:
            item.add_marker(pytest.mark.impacted)
        elif skip:
            item.add_marker(pytest.mark.skip(reason=NOT_IMPACTED))
        else:
            unimpacted.add(item)
    config.stash[_UNIMPACTED_ITEMS] = unimpacted


@pytest.hookimpl(trylast=True, specname="pytest_collection_modifyitems")
def pytest_deselect_unimpacted(config: Config, items: list[pytest.Item]) -> None:
    """Deselect what ``pytest_collection_modifyitems`` judged not impacted.

    Last, after ``-k`` and ``-m``: whether this plugin deselected anything decides the
    exit status of a run left without tests (``pytest_sessionfinish``), and a ``-k``
    matching nothing is a mistake to report, even when the changes impact no test.
    """
    judged = config.stash.get(_UNIMPACTED_ITEMS, set())
    kept: list[pytest.Item] = []
    unimpacted: list[pytest.Item] = []
    for item in items:
        # Only what was judged: an item another plugin added since was not, and runs.
        (unimpacted if item in judged else kept).append(item)
    if unimpacted:
        config.hook.pytest_deselected(items=unimpacted)
        items[:] = kept
        _record(config, len(unimpacted))
    # Counted now, after -k and -m: the summary says how many ran.
    unjudged = config.stash.get(_UNJUDGED_ITEMS, set())
    _report(config, _UNJUDGED, _WORKER_UNJUDGED_KEY, sum(item in unjudged for item in kept))


def _record(config: Config, count: int) -> None:
    """Keep the *count* deselected for the exit status and the summary (see :func:`_report`)."""
    _report(config, _DESELECTION, _WORKER_OUTPUT_KEY, count)


def _report(config: Config, key: StashKey[int], worker_key: str, count: int) -> None:
    """Keep *count* under *key* for the summary.

    A pytest-xdist worker reports it in its ``workeroutput`` too: the controller
    collects nothing itself, so it learns only what the workers report.
    """
    config.stash[key] = count
    if hasattr(config, "workeroutput"):
        config.workeroutput[worker_key] = count


def _analysed_dirs(config: Config) -> list[Path]:
    """The directories the analysis walks, ``--impacted-module``'s and ``--impacted-tests-dir``'s, as given and
    resolved: a test reached through a symlink inside one is judged, by either spelling."""
    get_option = partial(get_option_from_config, config)
    module, tests_dir = get_option("impacted_module"), get_option("impacted_tests_dir")
    traversal = _traversal()
    # As the analysis reads them: ``./app`` is ``app``, a dotted tests dir ``suite.unit`` walks ``suite/unit``. A tests
    # dir that is a directory as given is the one walked (validation checks so), its spelling kept (an absolute one).
    named = [traversal.package_name_to_path(_validation().normalized_module(str(module)))]
    if tests_dir:
        named.append(tests_dir if os.path.isdir(config.rootpath / tests_dir) else traversal.walked_directory(tests_dir))
    given = [Path(os.path.abspath(config.rootpath / name)) for name in named]
    return [*given, *(_traversal().real_path(directory) for directory in given)]


def _unjudged_items(items: list[pytest.Item], analysed: list[Path], root: Path, known: set[Path]) -> set[pytest.Item]:
    """The Python test items no analysis judges, so they run.

    - Outside the analysed directories: their files are no graph node (nothing in the application imports a test),
      so a rule that impacts "every test" never reached them either: a second tests directory, or the tests
      themselves when only ``--impacted-module`` is set. A walk reaches a file under an analysed directory,
      resolved, or through a symlink under one that stays inside the project (*root*).
    - Inside them, in a file that is none of the *known* test files the walks name: a test a ``python_files``
      pattern collects from ``check_db.py``, or one in a directory whose name is no identifier.
    """
    resolved: dict[Path, bool] = {}
    real_path = _traversal().real_path

    def unjudged(path: Path) -> bool:
        if path not in resolved:
            real, spelled = real_path(path), Path(os.path.abspath(path))
            walked = any(real.is_relative_to(d) for d in analysed) or (
                real.is_relative_to(root) and any(spelled.is_relative_to(d) for d in analysed)
            )
            resolved[path] = not (walked and real in known)
        return resolved[path]

    # Tests pytest's Python collector made: a linter plugin's items (pytest-ruff, pytest-mypy) are no tests.
    return {item for item in items if isinstance(item, pytest.Function) and unjudged(item.path)}


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    """pytest-xdist hook, on the controller: keep the most any finished worker deselected.

    Workers usually collect and deselect the same tests; the most is what the summary
    names when they do not: ``--dist each`` allows it, and a crashed worker's replacement
    may collect what appeared since.
    """
    for key, worker_key in ((_DESELECTION, _WORKER_OUTPUT_KEY), (_UNJUDGED, _WORKER_UNJUDGED_KEY)):
        reported = getattr(node, "workeroutput", {}).get(worker_key)
        if isinstance(reported, int):
            config = node.config
            config.stash[key] = max(reported, config.stash.get(key, 0))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    """A run left with no tests passes when this plugin deselected some, as skipped ones did.

    pytest exits 5 when no test runs, so deselecting every test would fail a run whose
    changes impact nothing, such as a docs-only commit, or a shard a later plugin handed
    only tests this one deselected. One with no tests at all, or emptied by ``-k`` (which
    runs first, leaving this plugin nothing to deselect), still exits 5. Early
    (``tryfirst``), so most other plugins reading ``session.exitstatus`` see the run's
    status, and any may still set a failure of its own; the hooks' ``exitstatus``
    argument, and so pytest's own summary ("no tests ran"), stays pytest's 5.
    """
    if session.exitstatus == ExitCode.NO_TESTS_COLLECTED and session.config.stash.get(_DESELECTION, 0):
        session.exitstatus = ExitCode.OK


def pytest_terminal_summary(terminalreporter, config: Config) -> None:
    """Say how many tests were deselected as not impacted, which pytest-xdist's summary omits — not under
    ``-qq``, which asks for no more than pytest's own summary — and, when pytest left the report header out
    (``-q``, ``-qq``, ``--no-header``), the warnings it would have carried: why every test runs, say.
    """
    count = config.stash.get(_DESELECTION, 0)
    if count and config.get_verbosity() > -2:
        tests = "test" if count == 1 else "tests"
        terminalreporter.write_line(f"pytest-impacted: {count} {tests} {NOT_IMPACTED}, deselected")
    if (unjudged := config.stash.get(_UNJUDGED, 0)) and config.get_verbosity() > -2:
        tests = "test" if unjudged == 1 else "tests"
        terminalreporter.write_line(
            f"pytest-impacted: {unjudged} {tests} that no analysis judges ran: in files outside --impacted-module and "
            "--impacted-tests-dir, or not known as test modules (collected by a python_files pattern though named "
            "neither test_*.py nor *_test.py outside a tests directory, or under a directory whose name is no "
            "identifier). Point --impacted-tests-dir at a directory holding them, or rename them, to select among "
            "them.",
            yellow=True,
        )
    # pytest leaves the report header out under -q and --no-header; a warning it carries is said here instead.
    if config.getoption("verbose") < 0 or config.getoption("no_header"):
        for line in _header_warnings(config):
            terminalreporter.write_line(line, yellow=True, bold=True)


def _impacted_items(items: list[pytest.Item], impacted_tests: list[str], root_dir: Path) -> set[pytest.Item]:
    """The collected items that belong to an impacted test file — or that cannot be judged.

    An item belongs to both the file it was collected from (``item.path``) and the
    file its test function is defined in (``item.location``). They differ for an
    inherited test (the base class's module) and a pytest-bdd scenario (inside
    ``pytest_bdd``); matching only one of them skipped tests, so either selects it.
    The location is also matched the way it always was, by path suffix, so nothing
    selected before is lost (e.g. a location outside a symlinked rootdir).

    Import analysis only ranks *test modules*, so two kinds of item always run:
    items from non-Python files (a ``--doctest-glob`` text file, a YAML collector)
    and doctests (``--doctest-modules`` collects them from source modules too).
    """
    traversal, api = _traversal(), _api()
    impacted = {traversal.real_path(root_dir / test) for test in impacted_tests}
    resolved: dict[Path, Path] = {}

    def is_impacted(path: Path) -> bool:
        if path not in resolved:
            resolved[path] = traversal.real_path(path)
        return resolved[path] in impacted

    return {
        item
        for item in items
        if item.path.suffix != ".py"
        or isinstance(item, DoctestItem)
        or is_impacted(item.path)
        or is_impacted(root_dir / item.location[0])
        or api.matches_impacted_tests(item.location[0], impacted_tests=impacted_tests)
    }


def get_option_from_config(config: Config, name: str) -> str | None:
    """Get an option from the config.

    If the option is not set via command line, return the default value
    from the ini configuration file (e.g. pytest.ini, pyproject.toml) if present. A choice setting left empty
    there (``impacted_backend =``) is its default, as the ``impacted-tests`` CLI reads it.
    """
    value = config.getoption(name) or config.getini(name)
    if value == "" and (choice := _CHOICE_OPTIONS.get(name)):
        return choice.default
    return value


_CHOICE_OPTIONS = {option.setting: option for option in OPTIONS if option.kind is Kind.CHOICE}


def validate_config(config: Config):
    """Validate the configuration options."""
    get_option = partial(get_option_from_config, config)
    if not get_option("impacted") or config.getoption("help", default=False):
        return  # `pytest --help` shows the help, whatever addopts holds and wherever it runs (an sdist: no git)

    if not get_option("impacted_module"):
        raise UsageError("No module specified. Please specify a module using --impacted-module.")
    if not get_option("impacted_git_mode"):
        raise UsageError("No git mode specified. Please specify a git mode using --impacted-git-mode.")
    for option in OPTIONS:  # ``choices`` guards the command line only; a setting would reach the analysis and crash it
        if option.kind is Kind.CHOICE and (value := get_option(option.setting)) not in option.choices:
            choices = ", ".join(f"'{choice}'" for choice in option.choices)
            raise UsageError(f"Invalid {option.setting} value {value!r}: expected one of {choices}.")

    try:
        _file_map().parse_file_map(get_option("impacted_map") or [])
        # Named where the globs came from: the option given, else the setting and its file.
        given = config.getoption("impacted_invalidate_all", default=None)
        source = "--impacted-invalidate-all" if given else f"impacted_invalidate_all in {config.inipath}"
        _strategies().parse_invalidation_patterns(get_option("impacted_invalidate_all") or [], source)
        check_ext_config(disabled=get_option("impacted_disable_ext") or [], ext_config=_collect_ext_config(config))
    except ValueError as err:
        raise UsageError(str(err)) from err

    if get_option("impacted_git_mode") == GitMode.BRANCH and not get_option("impacted_base_branch"):
        raise UsageError("No base branch specified. Please specify a base branch using --impacted-base-branch.")

    root_dir = config.rootpath
    module_name = get_option("impacted_module")
    assert module_name is not None  # guarded by the check above
    validate_module(module_name, root_dir)

    tests_dir = get_option("impacted_tests_dir")
    if tests_dir:
        validate_tests_dir(tests_dir, root_dir)

    validate_repository(root_dir)
    base_branch = get_option("impacted_base_branch")
    if get_option("impacted_git_mode") == GitMode.BRANCH and base_branch:
        validate_base_branch(base_branch, str(config.rootpath))


def validate_repository(root_dir: Path) -> None:
    """Refuse a project git cannot read — no repository, or one it refuses — in git's own words.

    Without git there is nothing to check: the run fails open (see :class:`~pytest_impacted.git.GitUnavailableError`).
    """
    try:
        _git().check_repository(root_dir)
    except _git().GitUnavailableError:
        return
    except _git().GitError as err:
        raise UsageError(f"{err} pytest-impacted reads the changed files from git.") from err


def validate_module(module_name: str, root_dir: Path) -> None:
    """Validate that --impacted-module names a package under *root_dir* (see :mod:`~pytest_impacted.validation`)."""
    if problem := _validation().module_problem(module_name, root_dir, "--impacted-module"):
        raise UsageError(problem)


def _collect_ext_config(config: Config) -> dict[str, Any]:
    """Collect all extension config values from pytest config."""
    ext_config: dict[str, Any] = {}
    for ext in discover_extension_metadata():
        for opt in ext.config_options:
            ini_name = get_ext_ini_name(ext.name, opt.name)
            value = get_option_from_config(config, ini_name)
            if value is not None:
                ext_config[ini_name] = value
    return ext_config


def validate_tests_dir(tests_dir: str, root_dir: Path) -> None:
    """Validate that --impacted-tests-dir is a directory under *root_dir* the analysis can walk."""
    if problem := _validation().tests_dir_problem(tests_dir, root_dir, "--impacted-tests-dir"):
        raise UsageError(problem)


def validate_base_branch(base_branch: str, root_dir: str) -> None:
    """Validate that --impacted-base-branch refers to a valid git ref."""
    if not _git().GIT_AVAILABLE:
        return

    # Importable only when GIT_AVAILABLE; see the guarded import in git.py.
    from git import GitCommandError, GitCommandNotFound, InvalidGitRepositoryError  # noqa: PLC0415

    try:
        args = _git().rev_args(base_branch)
        repo = _git().find_repo(root_dir)
        repo.git.rev_parse("--verify", *args)
    except _git().InvalidGitRefError as err:
        raise UsageError(
            f"Invalid base branch: {err} Please check the value passed to --impacted-base-branch."
        ) from err
    except GitCommandNotFound:
        return  # Nothing to validate against; the run itself fails open.
    except InvalidGitRepositoryError as err:
        raise UsageError(
            f"No git repository found at or above '{root_dir}'. Make sure you are running from within a git repository."
        ) from err
    except GitCommandError as err:
        # List available local branches for the suggestion
        try:
            branches = [ref.name for ref in repo.references]
            branch_list = ", ".join(sorted(branches)[:10])
            suffix = f" Available refs: {branch_list}"
            if len(repo.references) > 10:
                suffix += ", ..."
        except (AttributeError, TypeError):
            suffix = ""

        raise UsageError(
            f"Base branch '{base_branch}' does not exist in the git repository. "
            f"Please check the value passed to --impacted-base-branch.{suffix}"
        ) from err
