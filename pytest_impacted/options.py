"""Every pytest-impacted setting, once: its pytest option and ini key, its ``impacted-tests`` spelling, its default.

The plugin registers its options and ini keys from :data:`OPTIONS`, the report header lists them from it, and the
CLI builds its click options and finds each one's pytest setting from it. Each option was spelled in seven places
before, and a setting the plugin checked in one went unchecked in another.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pytest_impacted.modes import GitMode


#: What ``--impacted-backend`` may ask for (see :mod:`pytest_impacted.backends`): the fastest parser that can honour the
#: options, or the standard library's whatever is installed.
BACKENDS = ("auto", "python")


class Kind(StrEnum):
    """How an option takes its value, on the command line and in the pytest configuration."""

    FLAG = "flag"  # on or off: ``store_true``, an ini ``bool``, a click ``--x/--no-x`` pair
    VALUE = "value"  # one string
    LIST = "list"  # repeatable: ``append``, an ini ``args``, a click ``multiple``
    CHOICE = "choice"  # one of ``choices``


class Unimpacted(StrEnum):
    """What becomes of the collected tests no change impacts."""

    DESELECT = "deselect"
    SKIP = "skip"


@dataclass(frozen=True)
class Cli:
    """An option's ``impacted-tests`` spelling: the flag (for a FLAG, the one that turns it on, ``no-merge-base``
    pairing with ``merge-base``), the parameter name click hands the command, its help and its default."""

    flag: str
    param: str
    help: str
    default: Any = None
    metavar: str | None = None


@dataclass(frozen=True)
class Option:
    """One setting: the pytest ini key (also the option's ``dest``), the pytest flag, and how it reads."""

    setting: str
    flag: str
    kind: Kind
    help: str
    default: Any = None
    choices: tuple[str, ...] = ()
    metavar: str | None = None
    #: The plugin accepts the flag alone (``--impacted-git-mode``), and then takes the ini setting.
    optional_value: bool = False
    #: Shown in the report header, which names the settings a run used.
    in_header: bool = True
    #: The ini key's help, when not "default value for <flag>".
    ini_help: str | None = None
    cli: Cli | None = field(default=None)


OPTIONS: tuple[Option, ...] = (
    Option(
        "impacted",
        "--impacted",
        Kind.FLAG,
        "Run only tests impacted by the chosen git state.",
        default=False,
        in_header=False,
    ),
    Option(
        "impacted_module",
        "--impacted-module",
        Kind.VALUE,
        "Module name to check for impacted tests, relative to the pytest rootdir.",
        metavar="MODULE",
        cli=Cli(
            "module",
            "module",
            "Namespace (top-level) module for package we are testing, relative to --root-dir. "
            "Required, here or as impacted_module in the pytest configuration.",
        ),
    ),
    Option(
        "impacted_git_mode",
        "--impacted-git-mode",
        Kind.CHOICE,
        "Which changes count: 'unstaged' (uncommitted work, untracked files included) or "
        "'branch' (changes since forking from --impacted-base-branch).",
        default=GitMode.UNSTAGED.value,
        choices=tuple(mode.value for mode in GitMode),
        optional_value=True,
        cli=Cli("git-mode", "git_mode", "Which changes count.", default=GitMode.UNSTAGED.value),
    ),
    Option(
        "impacted_base_branch",
        "--impacted-base-branch",
        Kind.VALUE,
        "Git reference for computing impacted files when running in 'branch' git mode.",
        cli=Cli("base-branch", "base_branch", "Base branch.", default="main"),
    ),
    Option(
        "impacted_no_merge_base",
        "--impacted-no-merge-base",
        Kind.FLAG,
        "In 'branch' git mode, diff against the base branch's tip instead of the point this "
        "branch forked from, so commits the base gained since also count as changes.",
        default=False,
        cli=Cli(
            "no-merge-base",
            "no_merge_base",
            "In branch mode, diff against the base branch's tip instead of the fork point.",
            default=False,
        ),
    ),
    Option(
        "impacted_tests_dir",
        "--impacted-tests-dir",
        Kind.VALUE,
        "Directory containing the unit-test files, relative to the pytest rootdir. If not "
        "specified, tests will only be found under namespace module directory.",
        cli=Cli(
            "tests-dir",
            "tests_dir",
            "Directory containing the unit-test files, relative to --root-dir. If not specified, "
            "tests will only be found under namespace module directory.",
        ),
    ),
    Option(
        "no_impacted_dep_files",
        "--no-impacted-dep-files",
        Kind.FLAG,
        "Disable dependency and test-config file change detection (uv.lock, requirements*.txt, pytest.ini, etc.).",
        default=False,
        cli=Cli(
            "no-dep-files", "no_dep_files", "Disable dependency and test-config file change detection.", default=False
        ),
    ),
    Option(
        "impacted_invalidate_all",
        "--impacted-invalidate-all",
        Kind.LIST,
        "Glob pattern for files that, when changed, mark ALL tests as impacted "
        "(e.g. '*.json', 'config/*.yaml'). Repeatable.",
        default=[],
        metavar="PATTERN",
        ini_help="default value for --impacted-invalidate-all (list of glob patterns)",
        cli=Cli(
            "invalidate-all",
            "invalidate_all",
            "Glob for files that, when changed, mark ALL tests as impacted (repeatable).",
            default=(),
            metavar="PATTERN",
        ),
    ),
    Option(
        "impacted_map",
        "--impacted-map",
        Kind.LIST,
        "A changed file matching GLOB counts as a change to PATH (a file or directory, relative to the "
        "rootdir), e.g. 'reports/*.json=tests/test_bench.py'. Repeatable.",
        default=[],
        metavar="GLOB=PATH",
        ini_help="default value for --impacted-map (list of GLOB=PATH)",
        cli=Cli(
            "map",
            "file_map",
            "A changed file matching GLOB counts as a change to PATH, relative to --root-dir (repeatable).",
            default=(),
            metavar="GLOB=PATH",
        ),
    ),
    Option(
        "impacted_conftest_imports",
        "--impacted-conftest-imports",
        Kind.FLAG,
        "Also treat a conftest.py that imports changed application code (--impacted-module; directly "
        "or through other modules) as impacting every test in its directory and below, as it does for "
        "changed test code. Safer, but a top-level conftest importing the application selects almost "
        "every test.",
        default=False,
        cli=Cli(
            "conftest-imports",
            "conftest_imports",
            "Also treat a conftest.py that imports changed application code as impacting every test beneath it.",
            default=False,
        ),
    ),
    Option(
        "impacted_ignore_type_checking_imports",
        "--impacted-ignore-type-checking-imports",
        Kind.FLAG,
        "Leave application code's type-only imports out of the dependency graph: those in an "
        "'if TYPE_CHECKING:' block whose names the module uses only in annotations. They never run, but "
        "often close import cycles. Test code and package __init__.py files keep their own. Off by "
        "default, as a type checker, or code evaluating annotations, may still read the modules they name.",
        default=False,
        cli=Cli(
            "ignore-type-checking-imports",
            "ignore_type_checking_imports",
            "Leave application code's type-only imports out of the dependency graph: those in an "
            "'if TYPE_CHECKING:' block whose names the module uses only in annotations. Test code and "
            "package __init__.py files keep their own.",
            default=False,
        ),
    ),
    Option(
        "impacted_unimpacted",
        "--impacted-unimpacted",
        Kind.CHOICE,
        "What to do with the tests no change impacts: 'deselect' them (the default), or mark "
        "them 'skip'ped, as releases up to 0.35 did.",
        default=Unimpacted.DESELECT.value,
        choices=tuple(action.value for action in Unimpacted),
    ),
    Option(
        "impacted_backend",
        "--impacted-backend",
        Kind.CHOICE,
        "Which parser reads the imports: 'auto' (pytest-impacted-rs when it is installed and new enough for the "
        "options, the standard library's ast otherwise) or 'python' (ast, whatever is installed). Both find the "
        "same imports: the choice changes how fast tests are selected, not which.",
        default="auto",
        choices=BACKENDS,
        cli=Cli(
            "backend", "backend", "Which parser reads the imports: auto or python (the same imports).", default="auto"
        ),
    ),
    Option(
        "impacted_disable_ext",
        "--impacted-disable-ext",
        Kind.LIST,
        "Disable a strategy extension by name (repeatable).",
        default=[],
        in_header=False,
        ini_help="Strategy extensions to disable",
        cli=Cli("disable-ext", "disable_ext", "Disable a strategy extension by name (repeatable).", default=()),
    ),
)

#: Each CLI parameter's pytest setting (an extension's option is named as its setting).
CLI_SETTINGS: dict[str, str] = {option.cli.param: option.setting for option in OPTIONS if option.cli}
