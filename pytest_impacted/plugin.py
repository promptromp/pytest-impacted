import os
import traceback
from collections.abc import Callable
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from pytest import Config, DoctestItem, ExitCode, Parser, StashKey, UsageError

from pytest_impacted._rust import RUST_AVAILABLE
from pytest_impacted.api import build_strategy_with_extensions, get_impacted_tests, matches_impacted_tests
from pytest_impacted.display import warn
from pytest_impacted.extensions import (
    discover_extension_metadata,
    get_ext_cli_flag,
    get_ext_ini_name,
)
from pytest_impacted.git import (
    GIT_AVAILABLE,
    GitMode,
    GitUnavailableError,
    InvalidGitRefError,
    find_repo,
    rev_args,
)
from pytest_impacted.traversal import canonical_root


class Unimpacted(StrEnum):
    """What becomes of the collected tests no change impacts."""

    DESELECT = "deselect"
    SKIP = "skip"


NOT_IMPACTED = "not impacted by the changes"
# Where a pytest-xdist worker reports its deselection to the controller.
_WORKER_OUTPUT_KEY = "pytest_impacted_deselected"
# Where the controller hands each pytest-xdist worker its analysis.
_WORKER_INPUT_KEY = "pytest_impacted_tests"


# How many tests this plugin deselected.
_DESELECTION = StashKey[int]()
_SESSION = StashKey[pytest.Session]()
# The run's analysis: the impacted test files, relative to the rootdir; None when it fails open.
_ANALYSIS = StashKey[list[str] | None]()
# Where the controller hands each pytest-xdist worker an analysis that raised, to raise again.
_WORKER_ERROR_KEY = "pytest_impacted_error"
_ANALYSIS_ERROR = StashKey[str]()
# The items this plugin judged not impacted, for pytest_deselect_unimpacted to deselect.
_UNIMPACTED_ITEMS = StashKey[set[pytest.Item]]()


def pytest_addoption(parser: Parser):
    """pytest hook to add command line options.

    This is called before any tests are collected.

    """
    group = parser.getgroup("impacted")
    group.addoption(
        "--impacted",
        action="store_true",
        default=None,
        dest="impacted",
        help="Run only tests impacted by the chosen git state.",
    )
    parser.addini(
        "impacted",
        help="default value for --impacted",
        type="bool",
        default=False,
    )

    group.addoption(
        "--impacted-module",
        default=None,
        dest="impacted_module",
        metavar="MODULE",
        help="Module name to check for impacted tests, relative to the pytest rootdir.",
    )
    parser.addini(
        "impacted_module",
        help="default value for --impacted-module",
        default=None,
    )

    group.addoption(
        "--impacted-git-mode",
        action="store",
        dest="impacted_git_mode",
        choices=GitMode.__members__.values(),
        default=None,
        nargs="?",
        help=(
            "Which changes count: 'unstaged' (uncommitted work, untracked files included) or "
            + "'branch' (changes since forking from --impacted-base-branch)."
        ),
    )
    parser.addini(
        "impacted_git_mode",
        help="default value for --impacted-git-mode",
        default=GitMode.UNSTAGED,
    )

    group.addoption(
        "--impacted-base-branch",
        action="store",
        default=None,
        dest="impacted_base_branch",
        help="Git reference for computing impacted files when running in 'branch' git mode.",
    )
    parser.addini(
        "impacted_base_branch",
        help="default value for --impacted-base-branch",
        default=None,
    )

    group.addoption(
        "--impacted-no-merge-base",
        action="store_true",
        default=None,
        dest="impacted_no_merge_base",
        help=(
            "In 'branch' git mode, diff against the base branch's tip instead of the point this "
            + "branch forked from, so commits the base gained since also count as changes."
        ),
    )
    parser.addini(
        "impacted_no_merge_base",
        help="default value for --impacted-no-merge-base",
        type="bool",
        default=False,
    )

    group.addoption(
        "--impacted-tests-dir",
        action="store",
        default=None,
        dest="impacted_tests_dir",
        help=(
            "Directory containing the unit-test files, relative to the pytest rootdir. If not "
            + "specified, tests will only be found under namespace module directory."
        ),
    )
    parser.addini(
        "impacted_tests_dir",
        help="default value for --impacted-tests-dir",
        default=None,
    )

    group.addoption(
        "--no-impacted-dep-files",
        action="store_true",
        default=None,
        dest="no_impacted_dep_files",
        help="Disable dependency and test-config file change detection (uv.lock, requirements*.txt, pytest.ini, etc.).",
    )
    parser.addini(
        "no_impacted_dep_files",
        help="default value for --no-impacted-dep-files",
        type="bool",
        default=False,
    )

    group.addoption(
        "--impacted-invalidate-all",
        action="append",
        default=[],
        dest="impacted_invalidate_all",
        metavar="PATTERN",
        help=(
            "Glob pattern for files that, when changed, mark ALL tests as impacted "
            "(e.g. '*.json', 'config/*.yaml'). Repeatable."
        ),
    )
    parser.addini(
        "impacted_invalidate_all",
        help="default value for --impacted-invalidate-all (list of glob patterns)",
        type="args",
        default=[],
    )

    group.addoption(
        "--impacted-conftest-imports",
        action="store_true",
        default=None,
        dest="impacted_conftest_imports",
        help=(
            "Also treat a conftest.py that imports changed application code (--impacted-module; directly "
            + "or through other modules) as impacting every test in its directory and below, as it does for "
            + "changed test code. Safer, but a top-level conftest importing the application selects almost "
            + "every test."
        ),
    )
    parser.addini(
        "impacted_conftest_imports",
        help="default value for --impacted-conftest-imports",
        type="bool",
        default=False,
    )

    group.addoption(
        "--impacted-unimpacted",
        action="store",
        default=None,
        dest="impacted_unimpacted",
        choices=[action.value for action in Unimpacted],
        help=(
            "What to do with the tests no change impacts: 'deselect' them (the default), or mark "
            + "them 'skip'ped, as releases up to 0.35 did."
        ),
    )
    parser.addini(
        "impacted_unimpacted",
        help="default value for --impacted-unimpacted",
        default=Unimpacted.DESELECT,
    )

    # Extension management
    group.addoption(
        "--impacted-disable-ext",
        action="append",
        default=[],
        dest="impacted_disable_ext",
        help="Disable a strategy extension by name (repeatable).",
    )
    parser.addini(
        "impacted_disable_ext",
        help="Strategy extensions to disable",
        type="args",
        default=[],
    )

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
    backend = "rust (ruff parser + rayon)" if RUST_AVAILABLE else "python (astroid)"
    ext_names = [e.name for e in discover_extension_metadata()]
    header = [
        f"impacted_module={get_option('impacted_module')}",
        f"impacted_git_mode={get_option('impacted_git_mode')}",
        f"impacted_base_branch={get_option('impacted_base_branch')}",
        f"impacted_no_merge_base={get_option('impacted_no_merge_base')}",
        f"impacted_tests_dir={get_option('impacted_tests_dir')}",
        f"no_impacted_dep_files={get_option('no_impacted_dep_files')}",
        f"impacted_invalidate_all={get_option('impacted_invalidate_all')}",
        f"impacted_conftest_imports={get_option('impacted_conftest_imports')}",
        f"impacted_unimpacted={get_option('impacted_unimpacted')}",
        f"backend={backend}",
    ]
    if ext_names:
        header.append(f"extensions={','.join(ext_names)}")
    lines = ["pytest-impacted: " + ", ".join(header)]
    if get_option("impacted") and not GIT_AVAILABLE:
        # The header is written by the controller, so this survives pytest-xdist,
        # whose workers run collection and whose terminal output is discarded.
        lines.append(f"pytest-impacted: WARNING: {GitUnavailableError()} Running every test.")
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
    """
    config = node.config
    session = config.stash.get(_SESSION, None)
    if session is None or not get_option_from_config(config, "impacted"):
        return
    if _ANALYSIS not in config.stash and _ANALYSIS_ERROR not in config.stash:
        try:
            config.stash[_ANALYSIS] = _analyse(session, config)
        except pytest.exit.Exception:
            raise
        except Exception:  # noqa: BLE001 — not swallowed: every worker raises it
            # Raised here it would escape xdist's session start, and pytest would skip
            # every pytest_sessionfinish (no JUnit XML, no combined coverage). So each
            # worker raises it at collection instead, none analysing again.
            config.stash[_ANALYSIS_ERROR] = traceback.format_exc()
    if _ANALYSIS_ERROR in config.stash:
        node.workerinput[_WORKER_ERROR_KEY] = config.stash[_ANALYSIS_ERROR]
    else:
        node.workerinput[_WORKER_INPUT_KEY] = config.stash[_ANALYSIS]


def _analyse(session: pytest.Session, config: Config) -> list[str] | None:
    """The impacted test files, relative to the rootdir, or ``None`` to run every test.

    Relative, so a pytest-xdist worker in another directory reads them as well.
    """
    # Any: ini values are untyped, and validate_config has checked the ones that must be set.
    get_option: Callable[[str], Any] = partial(get_option_from_config, config)
    root_dir = config.rootpath
    strategy = build_strategy_with_extensions(
        watch_dep_files=not get_option("no_impacted_dep_files"),
        invalidate_all_patterns=get_option("impacted_invalidate_all") or [],
        conftest_imports=get_option("impacted_conftest_imports"),
        disabled=get_option("impacted_disable_ext") or [],
        ext_config=_collect_ext_config(config),
    )
    try:
        impacted_tests = get_impacted_tests(
            impacted_git_mode=get_option("impacted_git_mode"),
            impacted_base_branch=get_option("impacted_base_branch"),
            root_dir=root_dir,
            ns_module=get_option("impacted_module"),
            tests_dir=get_option("impacted_tests_dir"),
            session=session,
            strategy=strategy,
            use_merge_base=not get_option("impacted_no_merge_base"),
        )
    except GitUnavailableError as err:
        # Fail open: with the changes unknown, every test may be impacted. Not
        # warnings.warn — `filterwarnings = error` would turn it into a crash.
        # When git was missing at import, pytest_report_header has already said
        # so; only a failure discovered now needs reporting here.
        if GIT_AVAILABLE:
            warn(f"pytest-impacted: {err} Running every test.", session)
        return None
    # The analysis names files under the canonical root; an extension may use the one it was given.
    roots = [canonical_root(root_dir), root_dir]
    return [_relative_to(roots, test) for test in impacted_tests or []]


def _relative_to(roots: list[Path], test: str) -> str:
    """*test* relative to the first of *roots* it is under; as it is if under none, or relative already."""
    path = Path(test)
    if path.is_absolute():
        for root in roots:
            if path.is_relative_to(root):
                return os.path.relpath(path, root)
    return test


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
    # Afresh: a second collection on one config (looponfail) must not see the first's.
    config.stash[_UNIMPACTED_ITEMS] = set()
    config.stash[_DESELECTION] = 0
    workerinput = getattr(config, "workerinput", {})
    if _WORKER_ERROR_KEY in workerinput:
        raise RuntimeError(f"pytest-impacted: the analysis failed on the controller:\n{workerinput[_WORKER_ERROR_KEY]}")
    impacted_tests = workerinput[_WORKER_INPUT_KEY] if _WORKER_INPUT_KEY in workerinput else _analyse(session, config)
    if impacted_tests is None:
        return
    root_dir = config.rootpath
    impacted_items = _impacted_items(items, impacted_tests, root_dir)
    skip = get_option("impacted_unimpacted") == Unimpacted.SKIP
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


def _record(config: Config, count: int) -> None:
    """Keep the *count* deselected for the exit status and the summary.

    A pytest-xdist worker reports it in its ``workeroutput`` too: the controller
    collects nothing itself, so it learns only what the workers report.
    """
    config.stash[_DESELECTION] = count
    if hasattr(config, "workeroutput"):
        config.workeroutput[_WORKER_OUTPUT_KEY] = count


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    """pytest-xdist hook, on the controller: keep the most any finished worker deselected.

    Workers usually collect and deselect the same tests; the most is what the summary
    names when they do not (``--dist each`` across Python versions).
    """
    reported = getattr(node, "workeroutput", {}).get(_WORKER_OUTPUT_KEY)
    if isinstance(reported, int):
        config = node.config
        config.stash[_DESELECTION] = max(reported, config.stash.get(_DESELECTION, 0))


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
    """Say how many tests were deselected as not impacted; pytest-xdist's summary omits it.

    Not under ``-qq``, which asks for no more than pytest's own summary.
    """
    count = config.stash.get(_DESELECTION, 0)
    if count and config.get_verbosity() > -2:
        tests = "test" if count == 1 else "tests"
        terminalreporter.write_line(f"pytest-impacted: {count} {tests} {NOT_IMPACTED}, deselected")


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
    impacted = {(root_dir / test).resolve() for test in impacted_tests}
    resolved: dict[Path, Path] = {}

    def is_impacted(path: Path) -> bool:
        if path not in resolved:
            resolved[path] = path.resolve()
        return resolved[path] in impacted

    return {
        item
        for item in items
        if item.path.suffix != ".py"
        or isinstance(item, DoctestItem)
        or is_impacted(item.path)
        or is_impacted(root_dir / item.location[0])
        or matches_impacted_tests(item.location[0], impacted_tests=impacted_tests)
    }


def get_option_from_config(config: Config, name: str) -> str | None:
    """Get an option from the config.

    If the option is not set via command line, return the default value
    from the ini configuration file (e.g. pytest.ini, pyproject.toml) if present.

    """
    return config.getoption(name) or config.getini(name)


def validate_config(config: Config):
    """Validate the configuration options."""
    get_option = partial(get_option_from_config, config)
    if not get_option("impacted"):
        return

    if not get_option("impacted_module"):
        raise UsageError("No module specified. Please specify a module using --impacted-module.")
    if not get_option("impacted_git_mode"):
        raise UsageError("No git mode specified. Please specify a git mode using --impacted-git-mode.")
    unimpacted = get_option("impacted_unimpacted")
    if unimpacted not in list(Unimpacted):
        choices = ", ".join(f"'{action}'" for action in Unimpacted)
        raise UsageError(f"Invalid impacted_unimpacted value {unimpacted!r}: expected one of {choices}.")

    if get_option("impacted_git_mode") == GitMode.BRANCH and not get_option("impacted_base_branch"):
        raise UsageError("No base branch specified. Please specify a base branch using --impacted-base-branch.")

    root_dir = config.rootpath
    module_name = get_option("impacted_module")
    assert module_name is not None  # guarded by the check above
    validate_module(module_name, root_dir)

    tests_dir = get_option("impacted_tests_dir")
    if tests_dir:
        validate_tests_dir(tests_dir, root_dir)

    base_branch = get_option("impacted_base_branch")
    if get_option("impacted_git_mode") == GitMode.BRANCH and base_branch:
        validate_base_branch(base_branch, str(config.rootpath))


def validate_module(module_name: str, root_dir: Path) -> None:
    """Validate that --impacted-module refers to a Python package under *root_dir*."""
    module_dir = module_name.replace(".", os.sep)
    # os.path, not Path.is_dir(): inside an unsearchable directory it is missing, rather than raising.
    if os.path.isdir(root_dir / module_dir):
        return

    # The directory doesn't exist — try to give a helpful suggestion
    if "-" in module_name:
        suggestion = module_name.replace("-", "_")
        suggestion_dir = suggestion.replace(".", os.sep)
        if os.path.isdir(root_dir / suggestion_dir):
            raise UsageError(
                f"Module '{module_name}' not found. Python module names use underscores, not hyphens. "
                f"Did you mean: --impacted-module={suggestion}"
            )

    # Check for src-layout: module might be under src/
    src_dir = os.path.join("src", module_dir)
    if os.path.isdir(root_dir / src_dir):
        raise UsageError(
            f"Module '{module_name}' not found under '{root_dir}', but found at '{src_dir}'. "
            f"For src-layout projects, use: --impacted-module=src/{module_dir}"
        )

    raise UsageError(
        f"Module '{module_name}' not found (no '{module_dir}/' directory under '{root_dir}'). "
        f"Make sure --impacted-module is a valid Python package name relative to the pytest rootdir."
    )


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
    """Validate that --impacted-tests-dir refers to a directory under *root_dir*."""
    if not os.path.isdir(root_dir / tests_dir):
        raise UsageError(
            f"Tests directory '{tests_dir}' does not exist under '{root_dir}'. "
            f"Please check the path passed to --impacted-tests-dir."
        )


def validate_base_branch(base_branch: str, root_dir: str) -> None:
    """Validate that --impacted-base-branch refers to a valid git ref."""
    if not GIT_AVAILABLE:
        return

    # Importable only when GIT_AVAILABLE; see the guarded import in git.py.
    from git import GitCommandError, GitCommandNotFound, InvalidGitRepositoryError  # noqa: PLC0415

    try:
        args = rev_args(base_branch)
        repo = find_repo(root_dir)
        repo.git.rev_parse("--verify", *args)
    except InvalidGitRefError as err:
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
