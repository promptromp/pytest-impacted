"""The ``impacted-tests`` CLI reads the pytest settings pytest itself would: same file, same values.

Each case writes a configuration and checks the reader against ``pytester.parseconfig``,
pytest's own answer, so a format or precedence rule the reader gets wrong fails here.
"""

import pytest

from pytest_impacted.ini import PytestConfig, find_pytest_config, load_pytest_config


PYTEST_9 = int(pytest.__version__.split(".")[0]) >= 9
needs_pytest_9 = pytest.mark.skipif(not PYTEST_9, reason="pytest.toml and [tool.pytest] are new in pytest 9")

# The same three settings, one per type the plugin declares: a string, a bool and "args".
INI_LINES = (
    "impacted_module = app\nimpacted_conftest_imports = true\nimpacted_invalidate_all = *.json 'my dir/*.yaml'\n"
)
TOML_INI_MODE = (
    'impacted_module = "app"\nimpacted_conftest_imports = true\nimpacted_invalidate_all = ["*.json", "my dir/*.yaml"]\n'
)
TOML_NATIVE = TOML_INI_MODE
FORMATS = [
    pytest.param({"pytest.ini": "[pytest]\n" + INI_LINES}, id="pytest.ini"),
    pytest.param({".pytest.ini": "[pytest]\n" + INI_LINES}, id=".pytest.ini"),
    pytest.param({"tox.ini": "[pytest]\n" + INI_LINES}, id="tox.ini"),
    pytest.param({"setup.cfg": "[tool:pytest]\n" + INI_LINES}, id="setup.cfg"),
    pytest.param({"pyproject.toml": "[tool.pytest.ini_options]\n" + TOML_INI_MODE}, id="pyproject-ini_options"),
    pytest.param({"pyproject.toml": "[tool.pytest]\n" + TOML_NATIVE}, id="pyproject-native", marks=needs_pytest_9),
    pytest.param({"pytest.toml": "[pytest]\n" + TOML_NATIVE}, id="pytest.toml", marks=needs_pytest_9),
    pytest.param({".pytest.toml": "[pytest]\n" + TOML_NATIVE}, id=".pytest.toml", marks=needs_pytest_9),
]
SETTINGS = [
    ("impacted_module", PytestConfig.string),
    ("impacted_conftest_imports", PytestConfig.flag),
    ("impacted_invalidate_all", PytestConfig.args),
]


def write(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.mark.parametrize("files", FORMATS)
def test_each_format_reads_as_pytest_reads_it(pytester, files):
    write(pytester.path, files)

    ours = find_pytest_config(pytester.path)
    theirs = pytester.parseconfig(pytester.path)

    assert ours.path == theirs.inipath
    for name, read in SETTINGS:
        assert read(ours, name) == theirs.getini(name), name


@pytest.mark.parametrize(
    ("files", "start"),
    [
        pytest.param(
            {"pytest.ini": "[pytest]\n", "pyproject.toml": "[tool.pytest.ini_options]\n"}, ".", id="pytest.ini_first"
        ),
        pytest.param({"tox.ini": "[pytest]\n", "setup.cfg": "[tool:pytest]\n"}, ".", id="tox_before_setup.cfg"),
        pytest.param({"tox.ini": "[tox]\n", "setup.cfg": "[tool:pytest]\n"}, ".", id="tox.ini_without_pytest"),
        pytest.param({"pytest.ini": "", "tox.ini": "[pytest]\n"}, ".", id="an_empty_pytest.ini_counts"),
        pytest.param({"pytest.ini": "[pytest]\n", "sub/tox.ini": "[pytest]\n"}, "sub", id="the_nearest_directory"),
        pytest.param(
            {"pytest.ini": "[pytest]\n", "sub/pyproject.toml": "[project]\nname = 'x'\n"},
            "sub",
            id="a_pyproject_without_pytest",
        ),
        pytest.param({"sub/pyproject.toml": "[project]\nname = 'x'\n"}, "sub", id="only_a_pyproject_without_pytest"),
        pytest.param({"setup.cfg": "[metadata]\nname = x\n"}, ".", id="no_configuration"),
    ],
)
def test_the_file_found_is_the_one_pytest_finds(pytester, files, start):
    write(pytester.path, files)
    directory = pytester.path / start
    directory.mkdir(exist_ok=True)

    assert find_pytest_config(directory).path == pytester.parseconfig(directory).inipath


def test_an_unset_setting_is_none(pytester):
    write(pytester.path, {"pytest.ini": "[pytest]\n"})

    config = find_pytest_config(pytester.path)

    assert (config.string("impacted_module"), config.flag("impacted_module"), config.args("x")) == (None, None, None)


def test_a_named_file_is_read_whatever_its_place(pytester):
    """``-c``: pytest reads the file named, even one no search would find."""
    write(
        pytester.path,
        {"pytest.ini": "[pytest]\nimpacted_module = near\n", "ci/impacted.ini": "[pytest]\nimpacted_module = ci\n"},
    )

    config = load_pytest_config(pytester.path / "ci/impacted.ini")

    assert config.string("impacted_module") == pytester.parseconfig("-c", "ci/impacted.ini").getini("impacted_module")


@pytest.mark.parametrize("value", ["yes", "On", "1", "t", "FALSE", "off", "0", "n"])
def test_booleans_read_as_pytest_reads_them(pytester, value):
    write(pytester.path, {"pytest.ini": f"[pytest]\nimpacted_conftest_imports = {value}\n"})

    ours = find_pytest_config(pytester.path).flag("impacted_conftest_imports")

    assert ours == pytester.parseconfig(pytester.path).getini("impacted_conftest_imports")


def test_a_value_that_is_no_boolean_is_an_error(pytester):
    write(pytester.path, {"pytest.ini": "[pytest]\nimpacted_conftest_imports = maybe\n"})
    config = find_pytest_config(pytester.path)

    with pytest.raises(ValueError, match=r"impacted_conftest_imports.*pytest.ini.*'maybe'"):
        config.flag("impacted_conftest_imports")


@needs_pytest_9
def test_both_pyproject_tables_are_an_error_as_in_pytest(pytester):
    both = "[tool.pytest]\nminversion = '9'\n[tool.pytest.ini_options]\nimpacted_module = 'app'\n"
    write(pytester.path, {"pyproject.toml": both})

    with pytest.raises(ValueError, match="Cannot use both"):
        find_pytest_config(pytester.path)
    with pytest.raises(pytest.UsageError, match="Cannot use both"):
        pytester.parseconfig(pytester.path)


@needs_pytest_9
@pytest.mark.parametrize(
    ("line", "read"),
    [
        ('impacted_conftest_imports = "true"', PytestConfig.flag),
        ('impacted_invalidate_all = "*.json"', PytestConfig.args),
        ("impacted_module = 3", PytestConfig.string),
    ],
    ids=["flag", "args", "string"],
)
def test_native_toml_values_must_have_their_type_as_in_pytest(pytester, line, read):
    """pytest's TOML mode checks types rather than converting strings."""
    write(pytester.path, {"pytest.toml": f"[pytest]\n{line}\n"})
    name = line.split(" = ")[0]

    with pytest.raises(ValueError, match="expected a"):
        read(find_pytest_config(pytester.path), name)
    with pytest.raises(TypeError):
        pytester.parseconfig(pytester.path).getini(name)
