"""End-to-end: ``--impacted-ignore-type-checking-imports`` leaves type-only imports out of the graph.

An import in the body of ``if TYPE_CHECKING:`` never runs (``typing.TYPE_CHECKING`` is ``False`` at
runtime), but by default it is an edge like any other: a type checker, or code evaluating annotations,
may still read the module. Such imports often close import cycles, which then select each other's tests.
"""

import pytest
from click.testing import CliRunner

from pytest_impacted import _rust
from pytest_impacted.cli import impacted_tests_cli

from .git_helpers import edit_file as edit


FILES = {
    "app/__init__.py": "",
    "app/a.py": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.b import X\n"
        "def f(x: 'X') -> int:\n"
        "    return 1\n"
    ),
    "app/b.py": "class X:\n    pass\n",
    "suite/test_a.py": "from app.a import f\n\ndef test_a():\n    assert f(None) == 1\n",
    "suite/test_b.py": "from app.b import X\n\ndef test_b():\n    assert X()\n",
}
INI = "[pytest]\npythonpath = .\nimpacted_module = app\nimpacted_tests_dir = suite\n"
FLAG = "--impacted-ignore-type-checking-imports"


def run(project, *args):
    return project.runpytest("--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", "-v", *args)


def cli(project, *args):
    return CliRunner().invoke(impacted_tests_cli, ["--root-dir", str(project.path), *args])


@pytest.fixture
def project(make_git_project):
    return make_git_project(FILES, INI)


def test_a_type_only_import_selects_its_importer_s_tests_by_default(project):
    edit(project, "app/b.py")

    result = run(project)

    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*impacted_ignore_type_checking_imports=False*"])


def test_ignored_a_type_only_import_selects_nothing(project):
    """The issue's case: an edit to ``app/b.py`` selects ``test_b`` only."""
    edit(project, "app/b.py")

    result = run(project, FLAG)

    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines(["*impacted_ignore_type_checking_imports=True*", "*suite/test_b.py::test_b PASSED*"])


def test_the_test_left_out_never_imports_the_module_at_runtime(project):
    """The oracle: break ``app/b.py`` at import, and the test the option leaves out still passes when run."""
    (project.path / "app/b.py").write_text("raise RuntimeError('imported')\n")

    selected = run(project, FLAG, "--continue-on-collection-errors")
    everything = project.runpytest("-p", "no:cacheprovider", "-v", "--continue-on-collection-errors")

    selected.assert_outcomes(errors=1, deselected=1)  # test_b's module fails to import
    everything.assert_outcomes(passed=1, errors=1)
    everything.stdout.fnmatch_lines(["*suite/test_a.py::test_a PASSED*"])


def test_the_ini_setting_turns_it_on(make_git_project):
    project = make_git_project(FILES, INI + "impacted_ignore_type_checking_imports = true\n")
    edit(project, "app/b.py")

    result = run(project)

    result.assert_outcomes(passed=1, deselected=1)


def test_ignored_a_type_only_import_no_longer_closes_a_cycle(make_git_project):
    """``a`` uses ``b`` at runtime and ``b`` names ``a`` in annotations only: an edit to ``a`` selected
    ``b``'s tests through the cycle; ignored, only ``a``'s. An edit to ``b`` still selects both."""
    files = {
        "app/__init__.py": "",
        "app/a.py": "from app.b import helper\n\nclass Model:\n    value = helper()\n",
        "app/b.py": (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from app.a import Model\n"
            "def helper() -> int:\n"
            "    return 1\n"
            "def describe(model: 'Model') -> str:\n"
            "    return 'model'\n"
        ),
        "suite/test_a.py": "from app.a import Model\n\ndef test_a():\n    assert Model.value == 1\n",
        "suite/test_b.py": "from app.b import describe\n\ndef test_b():\n    assert describe(None)\n",
    }
    project = make_git_project(files, INI)
    edit(project, "app/a.py")

    run(project).assert_outcomes(passed=2)
    run(project, FLAG).assert_outcomes(passed=1, deselected=1)

    (project.path / "app/a.py").write_text(files["app/a.py"])  # unedited again
    edit(project, "app/b.py")
    run(project, FLAG).assert_outcomes(passed=2)


def test_the_cli_takes_the_option_and_the_setting(make_git_project):
    project = make_git_project(FILES, INI + "impacted_ignore_type_checking_imports = true\n")
    edit(project, "app/b.py")
    test_b = str((project.path / "suite/test_b.py").resolve())
    test_a = str((project.path / "suite/test_a.py").resolve())

    from_setting = cli(project)
    overridden = cli(project, "--no-ignore-type-checking-imports")

    assert from_setting.exit_code == 0, from_setting.output
    assert from_setting.stdout.splitlines() == [test_b]
    assert "ignore-type-checking-imports: True" in from_setting.output
    assert overridden.exit_code == 0, overridden.output
    assert sorted(overridden.stdout.splitlines()) == [test_a, test_b]


def test_the_cli_flag(project):
    edit(project, "app/b.py")

    result = cli(project, "--ignore-type-checking-imports")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str((project.path / "suite/test_b.py").resolve())]


def test_under_xdist(project):
    """The controller analyses with the option, and the workers deselect what it leaves out."""
    pytest.importorskip("xdist")
    edit(project, "app/b.py")

    result = project.runpytest_subprocess(
        "--impacted", "-p", "no:cacheprovider", "--impacted-git-mode=unstaged", FLAG, "-n", "2"
    )

    result.assert_outcomes(passed=1)  # the workers deselect: xdist's summary counts no deselection
    result.stdout.fnmatch_lines(["pytest-impacted: 1 test not impacted by the changes, deselected"])


LAZY_INITS = {
    "module_getattr": (
        "from importlib import import_module\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.models import Model\n"
        "def __getattr__(name):\n"
        "    if name == 'Model':\n"
        "        return import_module('app.models').Model\n"
        "    raise AttributeError(name)\n"
    ),
    "sys_modules": (
        "import sys\n"
        "import types\n"
        "from importlib import import_module\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.models import Model\n"
        "else:\n"
        "    class _Lazy(types.ModuleType):\n"
        "        def __getattr__(self, name):\n"
        "            if name == 'Model':\n"
        "                return import_module('app.models').Model\n"
        "            raise AttributeError(name)\n"
        "    _module = _Lazy(__name__)\n"
        "    _module.__path__, _module.__spec__, _module.__file__ = __path__, __spec__, __file__\n"
        "    sys.modules[__name__] = _module\n"
    ),
    # Loads by a name it computes, mentions ``Model`` in no string, and uses it in a type hint: to the rule, a
    # type-only import (see test_type_only); kept because the file is a package's ``__init__``.
    "computed_name_annotated": (
        "from importlib import import_module\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.models import Model\n"
        "def __getattr__(name):\n"
        "    if not name[:1].isupper():\n"
        "        raise AttributeError(name)\n"
        "    return getattr(import_module(f'app.{name.lower()}s'), name)\n"
        "def build(cls: 'type[Model]') -> 'Model':\n"
        "    return cls()\n"
    ),
}


@pytest.mark.parametrize("init", LAZY_INITS.values(), ids=LAZY_INITS.keys())
def test_a_lazily_loading_package_keeps_its_type_only_imports(make_git_project, init):
    """``from app import Model`` imports ``app.models`` at runtime, through the ``__getattr__`` its
    ``if TYPE_CHECKING:`` import declares: an edit to ``app/models.py`` must still select the test, and
    breaking it breaks the test (at collection: the test module imports ``Model``)."""
    files = {
        "app/__init__.py": init,
        "app/models.py": "class Model:\n    value = 1\n",
        "app/other.py": "",
        "suite/test_lazy.py": "from app import Model\n\ndef test_lazy():\n    assert Model.value == 1\n",
        "suite/test_other.py": "import app.other\n\ndef test_other():\n    pass\n",
    }
    project = make_git_project(files, INI)
    edit(project, "app/models.py")

    run(project, FLAG).assert_outcomes(passed=1, deselected=1)

    (project.path / "app/models.py").write_text("raise RuntimeError('imported')\n")
    run(project, FLAG).assert_outcomes(errors=1, deselected=1)


def test_the_cli_warns_when_an_older_extension_leaves_the_parsing_to_python(project, monkeypatch):
    """An older pytest-impacted-rs cannot leave type-only imports out: the slower parser does, and says so."""
    monkeypatch.setattr(_rust, "RUST_AVAILABLE", True)
    monkeypatch.setattr(_rust, "RUST_SPLITS_TYPE_ONLY", False)
    edit(project, "app/b.py")

    result = cli(project, "--ignore-type-checking-imports")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str((project.path / "suite/test_b.py").resolve())]
    assert "warning: the installed pytest-impacted-rs predates ignoring type-only imports" in result.stderr


FIXTURE_FILES = {
    "app/__init__.py": "",
    "app/models.py": "class User:\n    name = 'u'\n",
    "suite/conftest.py": (
        "import pytest\nfrom app.models import User\n\n@pytest.fixture\ndef user():\n    return User()\n"
    ),
    "suite/test_user.py": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.models import User\n\n"
        "def test_user(user: 'User'):\n"
        "    assert user.name == 'u'\n"
    ),
    "suite/test_other.py": "def test_other():\n    pass\n",
}


def test_a_test_keeps_its_type_only_imports(make_git_project):
    """A test annotating a conftest fixture (``user: "User"``) has no other link to the type's module: test
    code keeps its type-only imports. Nothing imports a test module, so they close no cycle."""
    project = make_git_project(FIXTURE_FILES, INI)
    edit(project, "app/models.py")

    result = run(project, FLAG)

    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines(["*suite/test_user.py::test_user PASSED*"])


PLACEMENT_FILES = {
    "app/__init__.py": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from testing.factories import Factory\n"
        "def make(factory: 'Factory') -> None: ...\n"
    ),
    "testing/__init__.py": "",
    "testing/factories.py": "class Factory:\n    pass\n",
    "suite/conftest.py": (
        "import pytest\nfrom testing.factories import Factory\n\n"
        "@pytest.fixture\ndef factory():\n    return Factory()\n"
    ),
    "suite/test_app.py": "import app\n\ndef test_app():\n    pass\n",
    "suite/test_fixture.py": "def test_fixture(factory):\n    assert factory\n",
}


@pytest.mark.parametrize("flags", [(), (FLAG,)], ids=["default", "ignoring"])
def test_what_an_init_keeps_counts_as_without_the_option(make_git_project, flags):
    """The documented trade-off: an ``__init__``'s type-only import is kept exactly as without the option, so
    ``testing/factories.py``, outside both analysed directories, is application code through it. An edit selects
    the ``__init__``'s importers, and the conftest importing it is left to ``--impacted-conftest-imports``,
    named in the report — with the option as without it."""
    project = make_git_project(PLACEMENT_FILES, INI)
    edit(project, "testing/factories.py")

    result = run(project, *flags)

    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines(["*conftest.py*--impacted-conftest-imports*", "*suite/test_app.py::test_app PASSED*"])
    assert run(project, *flags, "--impacted-conftest-imports").parseoutcomes()["passed"] == 2
