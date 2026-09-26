"""Unit tests for fixture_impact: which conftest fixtures depend on changed code."""

import textwrap

import pytest

from pytest_impacted.fixture_impact import affected_fixtures


REACHED = {"app.db"}


def analyse(source: str, reached=REACHED, module_name: str = "tests.conftest"):
    return affected_fixtures(textwrap.dedent(source), module_name, reached)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            """
            import pytest
            from app.db import connect

            @pytest.fixture
            def db():
                return connect()

            @pytest.fixture
            def user():
                return "alice"
            """,
            {"db"},
            id="direct_use",
        ),
        pytest.param(
            """
            import pytest
            import app.db

            @pytest.fixture
            def db():
                return app.db.connect()
            """,
            {"db"},
            id="dotted_import",
        ),
        pytest.param(
            """
            import pytest
            from app import db as database

            @pytest.fixture
            def conn():
                return database.connect()
            """,
            {"conn"},
            id="submodule_imported_from_package",
        ),
        pytest.param(
            """
            import pytest
            from app.db import connect

            def _make():
                return connect()

            @pytest.fixture
            def db():
                return _make()
            """,
            {"db"},
            id="through_a_helper_defined_later",
        ),
        pytest.param(
            """
            import pytest
            from app.db import connect

            ENGINE = connect()

            @pytest.fixture
            def db():
                return ENGINE
            """,
            {"db"},
            id="through_a_module_level_variable",
        ),
        pytest.param(
            """
            import pytest
            from app.db import connect

            class Factory:
                def build(self):
                    return connect()

            @pytest.fixture
            def factory():
                return Factory()
            """,
            {"factory"},
            id="through_a_class",
        ),
        pytest.param(
            """
            import pytest
            from app.db import connect

            @pytest.fixture(name="database")
            def _database():
                return connect()
            """,
            {"database"},
            id="renamed_fixture",
        ),
        pytest.param(
            """
            import pytest
            from app.db import connect

            def helper():
                return connect()

            @pytest.fixture
            def user():
                return "alice"
            """,
            set(),
            id="changed_code_used_by_no_fixture",
        ),
        pytest.param(
            """
            import pytest
            from app.utils import add

            @pytest.fixture
            def total():
                return add(1, 2)
            """,
            set(),
            id="imports_only_unaffected_modules",
        ),
        pytest.param(
            """
            import pytest

            @pytest.fixture
            def db():
                from app.db import connect
                return connect()

            @pytest.fixture
            def other():
                import app.db
                return app.db
            """,
            {"db", "other"},
            id="import_inside_the_fixture",
        ),
        pytest.param(
            """
            import pytest

            def _make():
                from app import db
                return db.connect()

            @pytest.fixture
            def conn():
                return _make()
            """,
            {"conn"},
            id="import_inside_a_helper",
        ),
        pytest.param(
            """
            import pytest
            from .helpers import make

            @pytest.fixture
            def thing():
                return make()
            """,
            {"thing"},
            id="relative_import",
        ),
    ],
)
def test_affected_fixtures(source, expected):
    reached = {*REACHED, "tests.helpers"}
    assert analyse(source, reached) == expected


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("from app.db import *\n", id="star_import"),
        pytest.param(
            "from app.db import connect\n\ndef pytest_configure(config):\n    connect()\n",
            id="affected_hook",
        ),
        pytest.param("from app.db import connect\n\nconnect()\n", id="side_effect_at_import"),
        pytest.param(
            "import os\nfrom app.db import URL\n\nos.environ['DB'] = URL\n",
            id="assignment_side_effect",
        ),
        pytest.param(
            "from app.db import FLAG\n\nif FLAG:\n    pass\n",
            id="conditional_on_changed_code",
        ),
        pytest.param(
            "import pytest\nfrom app.db import connect\n\ndb = pytest.fixture(connect)\n",
            id="fixture_without_a_def",
        ),
        pytest.param("def broken(:\n", id="syntax_error"),
    ],
)
def test_undecidable_means_whole_directory(source):
    assert analyse(source) is None
