"""The ``impacted-tests`` CLI reads the pytest settings pytest itself would: same file, same values.

Each case writes a configuration and checks the reader against ``pytester.parseconfig``,
pytest's own answer, so a format or precedence rule the reader gets wrong fails here.
"""

import os
import sys

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


def test_dot_dot_in_the_start_is_resolved_as_pytest_resolves_it(pytester):
    """``b/../sib`` is ``sib``: ``b``'s configuration is no ancestor's."""
    write(pytester.path, {"b/pytest.ini": "[pytest]\nimpacted_module = wrong\n", "sib/x.py": ""})
    start = pytester.path / "b" / ".." / "sib"

    assert find_pytest_config(start).path == pytester.parseconfig(start).inipath


def test_a_named_file_of_another_kind_has_no_settings(pytester):
    """pytest reads a ``-c`` file only by its suffix: ``ci.conf`` holds nothing it reads."""
    write(pytester.path, {"ci.conf": "[pytest]\nimpacted_module = app\n"})

    ours = load_pytest_config(pytester.path / "ci.conf").string("impacted_module")

    assert ours == pytester.parseconfig("-c", "ci.conf").getini("impacted_module")


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
    with pytest.raises(TypeError):
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
