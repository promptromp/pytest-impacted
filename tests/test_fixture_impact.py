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
    """Every affected fixture is a candidate; no unaffected fixture is.

    Candidates may also hold helper names (``connect``, ``ENGINE``): a name that is not
    a fixture matches no test's fixtures, so only the fixture names are asserted.
    """
    reached = {*REACHED, "tests.helpers"}
    result = analyse(source, reached)
    assert result is not None
    assert expected <= result
    assert not ({"user", "total"} - expected) & result


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
        pytest.param("def broken(:\n", id="syntax_error"),
    ],
)
def test_undecidable_means_whole_directory(source):
    assert analyse(source) is None


# --- review of PR #82: every one of these skipped tests that should have run ---


@pytest.mark.parametrize(
    ("source", "must_include"),
    [
        pytest.param(
            "from app.db import db_session, user_factory  # noqa: F401\n",
            {"db_session", "user_factory"},
            id="fixtures_re_exported_by_import",
        ),
        pytest.param(
            """
            import pytest
            from app.db import connect

            def make(url):
                @pytest.fixture
                def _f():
                    return connect(url)
                return _f

            db = make("sqlite://")
            """,
            {"db"},
            id="fixture_made_by_a_factory",
        ),
        pytest.param(
            "import pytest\nfrom app.db import connect\n\ndb = pytest.fixture(connect)\n",
            {"db"},
            id="fixture_without_a_def",
        ),
        pytest.param(
            """
            import app.db.models
            from app import db
            import pytest

            @pytest.fixture
            def thing():
                return db.models.Thing()
            """,
            {"thing"},
            id="changed_submodule_reached_through_an_attribute",
        ),
    ],
)
def test_affected_names_include_every_way_a_fixture_can_be_bound(source, must_include):
    # app.db.models changed, and app.db's __init__ does not import it: only the ancestor rule links `db`.
    reached = {"app.db.models"} if "models" in source else {"app.db"}
    result = analyse(source, reached)
    assert result is not None and must_include <= result


def test_an_aliased_fixture_decorator_still_counts():
    """``@fx`` may be pytest.fixture under another name: the function's name must be a candidate."""
    source = "from pytest import fixture as fx\nfrom app.db import connect\n\n@fx\ndef db():\n    return connect()\n"
    result = analyse(source)
    assert result is None or "db" in result


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import pytest\nfrom app.db import connect\n\n"
            "@pytest.hookimpl(specname='pytest_collection_modifyitems')\ndef reorder(items):\n    connect()\n",
            id="hook_registered_under_another_name",
        ),
        pytest.param(
            "import pytest\nfrom app.db import connect\nNAME = 'database'\n\n"
            "@pytest.fixture(name=NAME)\ndef db():\n    return connect()\n",
            id="computed_fixture_name",
        ),
    ],
)
def test_affected_code_whose_role_is_unknowable_is_undecidable(source):
    """A hook under another name affects any test; a computed ``name=`` hides the fixture's real name."""
    assert analyse(source) is None
