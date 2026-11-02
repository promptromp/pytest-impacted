"""The ``impacted-tests`` CLI reads the pytest settings pytest itself would: same file, same values.

Each case writes a configuration and checks the reader against ``pytester.parseconfig``,
pytest's own answer, so a format or precedence rule the reader gets wrong fails here.
"""

import os
import sys

import pytest

from pytest_impacted.ini import PytestConfig, find_pytest_config, load_pytest_config


# The tests' own version gates, not ini.py's: a gate moved there by mistake must fail a test here.
PYTEST_9 = pytest.version_tuple >= (9,)
needs_pytest_9 = pytest.mark.skipif(not PYTEST_9, reason="pytest.toml and [tool.pytest] are new in pytest 9")
PYTEST_9_2 = pytest.version_tuple >= (9, 2)
needs_pytest_9_2 = pytest.mark.skipif(not PYTEST_9_2, reason="pytest 9.2 changed how -c files and pytest.toml read")
before_pytest_9_2 = pytest.mark.skipif(PYTEST_9_2, reason="pytest 9.2 changed how -c files and pytest.toml read")
# What pytest's getini raises for a value of the wrong type: a usage error since pytest 9.2.
PYTEST_REJECTS = pytest.UsageError if PYTEST_9_2 else TypeError

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


@pytest.mark.parametrize("files", [pytest.param(*case.values, id=case.id) for case in FORMATS])
def test_each_format_is_found_as_the_installed_pytest_finds_it(pytester, files):
    """pytest 8 reads no pytest.toml, nor ``[tool.pytest]``; pytest 9 reads both."""
    write(pytester.path, files)

    assert find_pytest_config(pytester.path).path == pytester.parseconfig(pytester.path).inipath


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


@needs_pytest_9
@pytest.mark.parametrize(
    ("files", "error"),
    [
        pytest.param({"pyproject.toml": "this is = not [toml"}, pytest.UsageError, id="unparsable_pyproject"),
        pytest.param(
            {"pyproject.toml": "[tool.pytest]\nminversion = '9'\n[tool.pytest.ini_options]\nminversion = '9'\n"},
            pytest.UsageError,
            id="both_pyproject_tables",
        ),
        pytest.param({"setup.cfg": "[pytest]\nimpacted_module = x\n"}, pytest.fail.Exception, id="setup.cfg_pytest"),
        pytest.param(
            {".pytest.toml": "impacted_module = 'x'\n"},
            pytest.UsageError,
            id="top_level_options",
            marks=needs_pytest_9_2,
        ),
    ],
)
def test_an_error_in_a_file_after_the_one_read_is_an_error_as_in_pytest(pytester, files, error):
    """pytest 9 loads the directory's later files too, to report them as ignored: their errors fail the run."""
    write(pytester.path, {"pytest.toml": "[pytest]\nimpacted_module = 'app'\n", **files})

    with pytest.raises(ValueError, match=next(iter(files))):
        find_pytest_config(pytester.path)
    with pytest.raises(error):
        pytester.parseconfig(pytester.path)


@pytest.mark.skipif(PYTEST_9, reason="pytest 9 loads a directory's later files too")
def test_a_file_after_the_one_read_is_not_read_before_pytest_9(pytester):
    write(pytester.path, {"pytest.ini": "[pytest]\nimpacted_module = app\n", "pyproject.toml": "this is = not [toml"})

    assert find_pytest_config(pytester.path).path == pytester.parseconfig(pytester.path).inipath


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
    with pytest.raises(PYTEST_REJECTS):
        pytester.parseconfig(pytester.path).getini(name)


def test_dot_dot_in_the_start_is_resolved_as_pytest_resolves_it(pytester):
    """``b/../sib`` is ``sib``: ``b``'s configuration is no ancestor's."""
    write(pytester.path, {"b/pytest.ini": "[pytest]\nimpacted_module = wrong\n", "sib/x.py": ""})
    start = pytester.path / "b" / ".." / "sib"

    assert find_pytest_config(start).path == pytester.parseconfig(start).inipath


@before_pytest_9_2
def test_a_named_file_of_another_kind_has_no_settings(pytester):
    """pytest reads a ``-c`` file only by its suffix: ``ci.conf`` holds nothing it reads."""
    write(pytester.path, {"ci.conf": "[pytest]\nimpacted_module = app\n"})

    ours = load_pytest_config(pytester.path / "ci.conf").string("impacted_module")

    assert ours == pytester.parseconfig("-c", "ci.conf").getini("impacted_module")


@needs_pytest_9_2
def test_a_named_file_of_another_kind_is_an_error_as_in_pytest(pytester):
    write(pytester.path, {"ci.conf": "[pytest]\nimpacted_module = app\n"})

    with pytest.raises(ValueError, match=r"ci.conf' has an unsupported format"):
        load_pytest_config(pytester.path / "ci.conf")
    with pytest.raises(pytest.UsageError, match="unsupported format"):
        pytester.parseconfig("-c", "ci.conf")


@pytest.mark.skipif(sys.platform == "win32", reason="needs /dev/null")
def test_a_named_path_that_is_no_regular_file_has_no_settings(pytester):
    """``-c /dev/null`` is how to ask pytest for no configuration: no error, even in pytest 9.2."""
    ours = load_pytest_config(os.devnull)
    theirs = pytester.parseconfig("-c", os.devnull)

    assert (ours.path, ours.values) == (theirs.inipath, {})


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("[tool.pytest.ini_options]\n" + TOML_INI_MODE, id="tool.pytest.ini_options"),
        pytest.param("[tool.pytest]\n" + TOML_NATIVE, id="tool.pytest", marks=needs_pytest_9),
        pytest.param("[pytest]\n" + TOML_NATIVE, id="pytest", marks=needs_pytest_9_2),
    ],
)
def test_a_named_toml_file_reads_as_pytest_reads_it(pytester, text):
    """A ``-c`` TOML file of another name than pytest's own: its ``[pytest]`` table too, since pytest 9.2."""
    write(pytester.path, {"ci.toml": text})

    ours = load_pytest_config(pytester.path / "ci.toml")
    theirs = pytester.parseconfig("-c", "ci.toml")

    assert ours.path == theirs.inipath
    for name, read in SETTINGS:
        assert read(ours, name) == theirs.getini(name), name


@needs_pytest_9_2
def test_a_named_toml_file_s_pytest_table_has_native_types_as_in_pytest(pytester):
    """Read as a ``pytest.toml`` is: a string where a bool belongs is an error, not converted."""
    write(pytester.path, {"ci.toml": '[pytest]\nimpacted_conftest_imports = "true"\n'})

    with pytest.raises(ValueError, match="expected a bool"):
        load_pytest_config(pytester.path / "ci.toml").flag("impacted_conftest_imports")
    with pytest.raises(pytest.UsageError, match="expects a bool"):
        pytester.parseconfig("-c", "ci.toml").getini("impacted_conftest_imports")


@needs_pytest_9_2
@pytest.mark.parametrize(
    "tables",
    ["[tool.pytest]\nminversion = '9'\n", "[tool.pytest.ini_options]\nminversion = '9'\n"],
    ids=["tool.pytest", "tool.pytest.ini_options"],
)
def test_a_named_toml_file_with_both_styles_is_an_error_as_in_pytest(pytester, tables):
    write(pytester.path, {"ci.toml": "[pytest]\nimpacted_module = 'app'\n" + tables})

    with pytest.raises(ValueError, match=r"Cannot use both \[pytest\].* in the same file"):
        load_pytest_config(pytester.path / "ci.toml")
    with pytest.raises(pytest.UsageError, match="Cannot use both"):
        pytester.parseconfig("-c", "ci.toml")


def test_a_pyproject_named_reads_no_pytest_table_as_in_pytest(pytester):
    """A file's name wins over its suffix: ``-c pyproject.toml`` reads only the ``[tool.pytest]`` tables."""
    write(pytester.path, {"ci/pyproject.toml": "[pytest]\nimpacted_module = 'app'\n"})

    ours = load_pytest_config(pytester.path / "ci/pyproject.toml").string("impacted_module")

    assert ours == pytester.parseconfig("-c", "ci/pyproject.toml").getini("impacted_module")


@needs_pytest_9_2
@pytest.mark.parametrize(
    ("files", "args"),
    [
        pytest.param({"pytest.toml": "impacted_module = 'app'\n"}, (), id="pytest.toml"),
        pytest.param({".pytest.toml": "impacted_module = 'app'\n"}, (), id=".pytest.toml"),
        pytest.param({"ci.toml": "impacted_module = 'app'\n"}, ("-c", "ci.toml"), id="named"),
    ],
)
def test_options_outside_the_pytest_table_are_an_error_as_in_pytest(pytester, files, args):
    """The usual way of getting the table wrong; pytest 9.2 says so rather than read nothing."""
    write(pytester.path, files)
    path = pytester.path / next(iter(files))

    with pytest.raises(ValueError, match=r"must be under a \[pytest\] table.*impacted_module"):
        load_pytest_config(path) if args else find_pytest_config(pytester.path)
    with pytest.raises(pytest.UsageError, match="must be under a"):
        pytester.parseconfig(*args, pytester.path)


@needs_pytest_9
@before_pytest_9_2
@pytest.mark.parametrize(
    ("files", "args"),
    [
        pytest.param({"pytest.toml": "impacted_module = 'app'\n"}, (), id="pytest.toml_top_level"),
        pytest.param({"ci.toml": "impacted_module = 'app'\n"}, ("-c", "ci.toml"), id="named_top_level"),
        pytest.param({"ci.toml": "[pytest]\nimpacted_module = 'app'\n"}, ("-c", "ci.toml"), id="named_pytest_table"),
    ],
)
def test_a_toml_file_s_options_outside_tool_pytest_are_not_read_before_pytest_9_2(pytester, files, args):
    write(pytester.path, files)
    path = pytester.path / next(iter(files))

    ours = (load_pytest_config(path) if args else find_pytest_config(pytester.path)).string("impacted_module")

    assert ours == pytester.parseconfig(*args, pytester.path).getini("impacted_module")


@pytest.mark.parametrize("name", ["pytest.toml", "ci.toml"])
def test_a_toml_file_with_other_tables_only_has_no_settings_as_in_pytest(pytester, name):
    """Only top-level *options* are refused: a table pytest does not read is no mistake."""
    write(pytester.path, {name: "[tool.other]\nx = 1\n"})

    ours = load_pytest_config(pytester.path / name)

    assert ours.values == {}
    assert ours.path == pytester.parseconfig("-c", name).inipath


def test_a_pytest_section_in_setup_cfg_is_an_error_as_in_pytest(pytester):
    write(pytester.path, {"setup.cfg": "[pytest]\nimpacted_module = app\n"})

    with pytest.raises(ValueError, match=r"\[pytest\].*setup.cfg"):
        find_pytest_config(pytester.path)
    with pytest.raises(pytest.fail.Exception, match="no longer supported"):
        pytester.parseconfig(pytester.path)


@needs_pytest_9
def test_a_native_list_of_other_than_strings_is_an_error_as_in_pytest(pytester):
    write(pytester.path, {"pytest.toml": "[pytest]\nimpacted_invalidate_all = [1]\n"})

    with pytest.raises(ValueError, match="list of strings"):
        find_pytest_config(pytester.path).args("impacted_invalidate_all")
    with pytest.raises(PYTEST_REJECTS):
        pytester.parseconfig(pytester.path).getini("impacted_invalidate_all")


def test_an_ini_options_scalar_for_a_list_is_split_as_in_pytest(pytester):
    write(pytester.path, {"pyproject.toml": "[tool.pytest.ini_options]\nimpacted_disable_ext = 5\n"})

    ours = find_pytest_config(pytester.path).args("impacted_disable_ext")

    assert ours == pytester.parseconfig(pytester.path).getini("impacted_disable_ext") == ["5"]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_a_listable_but_unsearchable_directory_holds_no_configuration(tmp_path):
    """``Path.is_file()`` raises inside one on Python 3.11–3.13; ``os.path.isfile`` says False."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o644)
    try:
        assert find_pytest_config(locked).path is None
    finally:
        locked.chmod(0o755)
