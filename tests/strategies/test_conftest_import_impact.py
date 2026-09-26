"""Unit tests for conftests that import changed code: test code always, application code opt-in.

Each test builds a real project and graph, so node paths and discovery agree as they do in a run.
"""

import logging
import os
from pathlib import Path

import pytest

from pytest_impacted.graph import build_dep_tree
from pytest_impacted.strategies import (
    CompositeImpactStrategy,
    ConftestImportImpactStrategy,
    DependencyFileImpactStrategy,
    InvalidationFileImpactStrategy,
    PytestImpactStrategy,
    get_default_strategies,
)


FIXTURE = "import pytest\nfrom app.helpers import make\n\n@pytest.fixture\ndef db():\n    return make()\n"
APP = {
    "app/__init__.py": "",
    "app/db.py": "def connect():\n    return 'conn'\n",
    "app/utils.py": "def add(a, b):\n    return a + b\n",
    "app/helpers.py": "from app.db import connect\n\ndef make():\n    return connect()\n",
}
SUITE = {
    "suite/db/conftest.py": FIXTURE,
    "suite/db/test_db.py": "def test_db(db):\n    assert db\n",
    "suite/db/deep/test_deep.py": "def test_deep(db):\n    assert db\n",
    "suite/other/test_other.py": "from app.utils import add\n\ndef test_other():\n    assert add(1, 1) == 2\n",
}


def make(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    return root


def find(strategy, root: Path, changed: str, *, tests_package: str | None = "suite", ns_module: str = "app"):
    return strategy.find_impacted_tests(
        changed_files=[changed],
        impacted_modules=[changed.removesuffix(".py").removesuffix("/__init__").replace("/", ".")],
        ns_module=ns_module,
        tests_package=tests_package,
        root_dir=root,
        dep_tree=build_dep_tree(ns_module, tests_package, root_dir=root),
    )


def notices(strategies, root: Path, changed: str, caplog) -> str:
    with caplog.at_level(logging.INFO, logger="pytest_impacted.display"):
        find(CompositeImpactStrategy(strategies), root, changed)
    return caplog.text


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """``app.db`` → ``app.helpers`` → ``suite/db/conftest.py``, beside an unrelated ``suite/other``."""
    return make(tmp_path, {**APP, **SUITE})


class TestConftestImportImpactStrategy:
    def test_a_conftest_importing_changed_application_code_impacts_its_directory_and_below(self, project):
        """Through a helper module too: the conftest imports ``app.db`` only transitively."""
        assert find(ConftestImportImpactStrategy(), project, "app/db.py") == [
            "suite.db.deep.test_deep",
            "suite.db.test_db",
        ]

    def test_a_change_the_conftest_does_not_import_impacts_nothing(self, project):
        """Test modules reached by imports are the AST strategy's business, not this one's."""
        assert find(ConftestImportImpactStrategy(), project, "app/utils.py") == []

    def test_report_only_names_the_conftests_and_selects_nothing(self, project, caplog):
        """Both spellings of the option: the pytest flag, and the ``impacted-tests`` CLI's."""
        with caplog.at_level(logging.INFO, logger="pytest_impacted.display"):
            assert find(ConftestImportImpactStrategy(report_only=True), project, "app/db.py") == []

        assert "suite/db/conftest.py" in caplog.text
        assert "--impacted-conftest-imports" in caplog.text
        assert "--conftest-imports" in caplog.text.replace("--impacted-conftest-imports", "")

    def test_report_only_is_silent_when_no_conftest_is_reached(self, project, caplog):
        with caplog.at_level(logging.INFO, logger="pytest_impacted.display"):
            find(ConftestImportImpactStrategy(report_only=True), project, "app/utils.py")

        assert "conftest" not in caplog.text

    def test_a_package_named_conftest_is_not_a_conftest(self, tmp_path):
        """pytest loads only files named ``conftest.py``; ``app/conftest/__init__.py`` is an ordinary package."""
        root = make(
            tmp_path,
            {
                **APP,
                "app/conftest/__init__.py": "from app.db import connect\n",
                "app/conftest/test_inner.py": "def test_inner():\n    assert True\n",
            },
        )

        assert find(ConftestImportImpactStrategy(), root, "app/db.py", tests_package=None) == []


@pytest.mark.parametrize(
    ("files", "changed", "tests_package"),
    [
        pytest.param(
            {"suite/fixtures.py": FIXTURE, "suite/db/conftest.py": "from suite.fixtures import *  # noqa: F403\n"},
            "suite/fixtures.py",
            "suite",
            id="a_fixture_module_in_the_tests_dir",
        ),
        pytest.param(
            {
                "suite/shared/conftest.py": FIXTURE,
                "suite/db/conftest.py": "from suite.shared.conftest import db  # noqa: F401\n",
            },
            "suite/shared/conftest.py",
            "suite",
            id="another_conftest",
        ),
        pytest.param(
            {
                "app/shared/__init__.py": "",
                "app/shared/conftest.py": FIXTURE,
                "suite/db/conftest.py": "from app.shared.conftest import db  # noqa: F401\n",
            },
            "app/shared/conftest.py",
            "suite",
            id="a_conftest_inside_the_package",
        ),
        pytest.param(
            {
                "app/checks/__init__.py": "",
                "app/checks/fixtures.py": FIXTURE,
                "app/checks/db/conftest.py": "from app.checks.fixtures import *  # noqa: F403\n",
                "app/checks/db/test_db.py": "def test_db(db):\n    assert db\n",
            },
            "app/checks/fixtures.py",
            "app/checks",
            id="a_tests_dir_inside_the_package",
        ),
    ],
)
def test_by_default_a_conftest_importing_changed_test_code_impacts_its_directory(
    tmp_path, files, changed, tests_package
):
    """Fixture modules and other conftests are test code, not the application: they are always followed."""
    root = make(tmp_path, {**APP, **SUITE, **files})

    result = find(PytestImpactStrategy(), root, changed, tests_package=tests_package)

    assert [module for module in result if module.endswith("test_db")], result
    assert not [module for module in result if module.endswith("test_other")], result


def test_by_default_a_conftest_importing_changed_application_code_is_left_to_the_opt_in(project):
    assert find(PytestImpactStrategy(), project, "app/db.py") == []


def test_code_under_a_symlinked_subpackage_is_still_application_code(tmp_path):
    """``app/shared`` links to ``libs/shared``: the file lives outside the package, its module inside it."""
    root = make(
        tmp_path,
        {
            **APP,
            **SUITE,
            "libs/shared/__init__.py": "",
            "libs/shared/x.py": "def value():\n    return 1\n",
            "suite/db/conftest.py": FIXTURE.replace("app.helpers import make", "app.shared.x import value as make"),
        },
    )
    os.symlink(root / "libs/shared", root / "app/shared", target_is_directory=True)

    assert find(PytestImpactStrategy(), root, "app/shared/x.py") == []


@pytest.mark.parametrize(
    ("init", "conftest", "changed"),
    [
        pytest.param("", FIXTURE, "app/db.py", id="a_module"),
        pytest.param(
            "from app.helpers import make\n",
            FIXTURE.replace("from app.helpers import", "from app import"),
            "app/__init__.py",
            id="the_package_init",
        ),
    ],
)
def test_a_tests_dir_holding_the_whole_package_does_not_make_the_application_test_code(
    tmp_path, init, conftest, changed
):
    """``--impacted-tests-dir=app`` cannot tell tests from application code, so it is ignored for this.

    The package's ``__init__.py`` is application code like the rest: a conftest importing it is left to the opt-in.
    """
    root = make(
        tmp_path,
        {
            **APP,
            "app/__init__.py": init,
            "app/conftest.py": conftest,
            "app/test_db.py": "def test_db(db):\n    assert db\n",
        },
    )

    assert find(PytestImpactStrategy(), root, changed, tests_package="app") == []
    assert find(ConftestImportImpactStrategy(), root, changed, tests_package="app") == ["app.test_db"]


def test_a_subclass_need_not_call_the_base_initialiser(project):
    """``PytestImpactStrategy`` takes no configuration, so subclasses that never call ``super().__init__`` work."""

    class Custom(PytestImpactStrategy):
        def __init__(self, extra: int = 1):
            self.extra = extra

    assert find(Custom(), project, "app/utils.py") == ["suite.other.test_other"]


class TestGetDefaultStrategiesWithConftestImports:
    @pytest.mark.parametrize("conftest_imports", [False, True])
    def test_always_present_right_after_the_pytest_strategy(self, conftest_imports):
        strategies = get_default_strategies(conftest_imports=conftest_imports)

        kinds = [type(s) for s in strategies]
        opt_in = strategies[kinds.index(ConftestImportImpactStrategy)]
        assert kinds.index(ConftestImportImpactStrategy) == kinds.index(PytestImpactStrategy) + 1
        assert opt_in.report_only is not conftest_imports

    @pytest.mark.parametrize("watch_dep_files", [True, False])
    def test_independent_of_the_other_switches(self, watch_dep_files):
        strategies = get_default_strategies(
            watch_dep_files=watch_dep_files, invalidate_all_patterns=["*.json"], conftest_imports=True
        )

        kinds = {type(s) for s in strategies}
        assert {ConftestImportImpactStrategy, InvalidationFileImpactStrategy} <= kinds
        assert (DependencyFileImpactStrategy in kinds) is watch_dep_files

    def test_the_default_pipeline_names_the_conftests(self, project, caplog):
        assert "suite/db/conftest.py" in notices(get_default_strategies(), project, "app/db.py", caplog)

    def test_opted_in_nothing_is_said_about_conftests(self, project, caplog):
        """The strategy selects those tests; telling the user to enable it would be wrong."""
        assert "conftest" not in notices(get_default_strategies(conftest_imports=True), project, "app/db.py", caplog)
