"""The pytest settings the ``impacted-tests`` CLI reads, from the file pytest would read them from.

The CLI must not build a pytest ``Config`` to ask it: that imports the initial conftests,
and analysis never runs project code. So this mirrors the installed pytest's search
(``_pytest.config.findpaths``) instead: the first directory, from the start up, holding a
file with pytest configuration, in pytest's order of names; a ``pyproject.toml`` without
any only if nothing else is found. Where pytest 8.1, 9 and 9.2 changed the rules, a
version gate follows them. ``tests/test_ini.py`` holds it to pytest's own answer.
"""

import os
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import iniconfig
import pytest


# pytest.toml and native TOML (``[tool.pytest]``) are new in pytest 9; pytest 8 reads neither.
PYTEST_9 = pytest.version_tuple >= (9,)
PYTEST_8_1 = pytest.version_tuple >= (8, 1)
# pytest 9.2 refuses a -c file it has no loader for, reads a -c TOML file's [pytest] table,
# and refuses options outside the [pytest] table of a pytest.toml.
PYTEST_9_2 = pytest.version_tuple >= (9, 2)
_TRUE = ("y", "yes", "t", "true", "on", "1")
_FALSE = ("n", "no", "f", "false", "off", "0")


@dataclass(frozen=True)
class PytestConfig:
    """The settings of one pytest configuration file (none: ``path`` is ``None``).

    *native* is pytest 9's TOML mode (``pytest.toml``, ``[tool.pytest]``): values keep
    their TOML types, which pytest checks rather than converts. Otherwise each is
    converted by type, from a string or, in ``[tool.pytest.ini_options]``, any TOML value.
    """

    path: Path | None = None
    values: dict[str, object] = field(default_factory=dict)
    native: bool = False

    def string(self, name: str) -> str | None:
        """A setting the plugin declares untyped, such as ``impacted_module``."""
        if (value := self._get(name, str)) is None:
            return None
        if isinstance(value, list):
            raise ValueError(f"{name} in {self.path}: expected a string, got {value!r}")  # noqa: TRY004 — a config error
        return str(value)

    def flag(self, name: str) -> bool | None:
        """A ``bool`` setting, read as pytest reads one."""
        value = self._get(name, bool)
        if value is None or isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text not in _TRUE + _FALSE:
            raise ValueError(f"{name} in {self.path}: invalid truth value {value!r}")
        return text in _TRUE

    def args(self, name: str) -> list[str] | None:
        """An ``args`` setting: a list, or a string split as a shell would."""
        value = self._get(name, list)
        if isinstance(value, list) and not self.native:
            return [str(item) for item in value]
        if value is None:
            return None
        if isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise ValueError(f"{name} in {self.path}: expected a list of strings, got {value!r}")
            return value
        return shlex.split(str(value))

    def _get(self, name: str, native_type: type) -> object:
        value = self.values.get(name)
        if value is not None and self.native and not isinstance(value, native_type):
            raise ValueError(f"{name} in {self.path}: expected a {native_type.__name__}, got {value!r}")
        return value


def find_pytest_config(start: Path) -> PytestConfig:
    """The configuration pytest finds for a run from *start*."""
    fallback = None
    # abspath, not Path.absolute(): it drops ``..``, so ``b/../sib`` walks up from ``sib``.
    start = Path(os.path.abspath(start))
    for directory in (start, *start.parents):
        for index, (name, load) in enumerate(_BY_NAME.items()):
            path = directory / name
            # os.path, not Path.is_file(): that raises inside a listable but unsearchable directory.
            if not os.path.isfile(path):
                continue
            if (config := load(path)) is not None:
                if PYTEST_9:
                    # pytest 9 loads the later files too, to report them as ignored: their errors fail the run.
                    for later, load_later in list(_BY_NAME.items())[index + 1 :]:
                        if os.path.isfile(directory / later):
                            load_later(directory / later)
                return config
            # pytest 8.1 made a pyproject.toml without pytest settings the configuration, as a last resort.
            fallback = fallback or (path if name == "pyproject.toml" and PYTEST_8_1 else None)
    return PytestConfig(path=fallback)


def load_pytest_config(path: Path) -> PytestConfig:
    """The configuration of the file named by ``-c``, which pytest reads even without a pytest section."""
    path = Path(os.path.abspath(path))
    # By its name, else by its suffix (pytest's ``_get_config_loader``).
    if (load := _BY_NAME.get(path.name) or _BY_SUFFIX.get(path.suffix)) is not None:
        return load(path) or PytestConfig(path=path)
    # pytest 9.2 refuses a file it has no loader for; a path that is no regular file (/dev/null) holds none.
    if PYTEST_9_2 and os.path.isfile(path):
        supported = ", ".join(sorted(_BY_SUFFIX))
        raise ValueError(f"Config file '{path}' has an unsupported format. Supported extensions are: {supported}.")
    return PytestConfig(path=path)


# One loader per pytest's: each returns *path*'s configuration, or ``None`` if it has none.


def _load_pytest_ini(path: Path) -> PytestConfig:
    """A ``pytest.ini``: the configuration even without a ``[pytest]`` section."""
    return _load_ini_file(path) or PytestConfig(path)


def _load_ini_file(path: Path) -> PytestConfig | None:
    sections = iniconfig.IniConfig(str(path))
    return PytestConfig(path, dict(sections["pytest"].items())) if "pytest" in sections else None


def _load_cfg_file(path: Path) -> PytestConfig | None:
    sections = iniconfig.IniConfig(str(path))
    if "tool:pytest" in sections:
        return PytestConfig(path, dict(sections["tool:pytest"].items()))
    if "pytest" in sections:
        # pytest fails the run on one, so the search must not go on past it.
        raise ValueError(f"{path}: [pytest] section in setup.cfg files is no longer supported, use [tool:pytest].")
    return None


def _load_pytest_toml(path: Path) -> PytestConfig:
    """A ``pytest.toml``: its ``[pytest]`` table, in native TOML, the configuration even when empty."""
    return PytestConfig(path, _pytest_table(_parse_toml(path), path) or {}, native=True)


def _load_pyproject_toml(path: Path) -> PytestConfig | None:
    return _tool_pytest(_parse_toml(path), path)


def _load_custom_toml(path: Path) -> PytestConfig | None:
    """A TOML file of another name, named by ``-c``: read as a ``pytest.toml`` if it has no ``[tool.pytest]`` tables."""
    document = _parse_toml(path)
    if (config := _tool_pytest(document, path)) is None:
        table = _pytest_table(document, path)
        return None if table is None else PytestConfig(path, table, native=True)
    if "pytest" in document:
        raise ValueError(
            f"{path}: Cannot use both [pytest] and [tool.pytest]/[tool.pytest.ini_options] in the same file. "
            "Please use [pytest], which is what pytest's own configuration files use; "
            "the [tool.pytest] tables are meant for pyproject.toml."
        )
    return config


# pytest's loaders by the names it searches for, in its order (``CONFIG_LOADERS_BY_NAME``)...
_BY_NAME = {
    **({"pytest.toml": _load_pytest_toml, ".pytest.toml": _load_pytest_toml} if PYTEST_9 else {}),
    "pytest.ini": _load_pytest_ini,
    # pytest 8 passes an empty .pytest.ini over, as it does a tox.ini without [pytest].
    ".pytest.ini": _load_pytest_ini if PYTEST_9 else _load_ini_file,
    "pyproject.toml": _load_pyproject_toml,
    "tox.ini": _load_ini_file,
    "setup.cfg": _load_cfg_file,
}
# ...then by suffix, for a file of another name named by -c (``CONFIG_LOADERS_BY_SUFFIX``).
_BY_SUFFIX = {
    ".cfg": _load_cfg_file,
    ".ini": _load_ini_file,
    # Read as a pyproject.toml before pytest 9.2.
    ".toml": _load_custom_toml if PYTEST_9_2 else _load_pyproject_toml,
}


def _parse_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise ValueError(f"{path}: {err}") from err  # as pytest names the file


def _tool_pytest(document: dict, path: Path) -> PytestConfig | None:
    """The configuration in the ``[tool.pytest]`` tables, or ``None`` (pytest's ``_config_from_tool_pytest``)."""
    tool = _table(_table(document, "tool", path), "pytest", path)
    native = {key: value for key, value in tool.items() if key != "ini_options"} if PYTEST_9 else {}
    ini_mode = tool.get("ini_options")
    if ini_mode is not None and not isinstance(ini_mode, dict):
        raise ValueError(f"{path}: [tool.pytest.ini_options] is not a table.")
    if native and ini_mode:
        raise ValueError(f"{path}: Cannot use both [tool.pytest] and [tool.pytest.ini_options].")
    if native:
        return PytestConfig(path, native, native=True)
    if ini_mode is None:
        return None
    return PytestConfig(path, ini_mode)


def _pytest_table(document: dict, path: Path) -> dict | None:
    """The ``[pytest]`` table, or ``None`` (pytest's ``_config_from_pytest_table``)."""
    if "pytest" in document:
        return _table(document, "pytest", path)
    stray = [key for key, value in document.items() if not isinstance(value, dict)]
    if PYTEST_9_2 and stray:
        # The usual way of getting the table wrong: pytest 9.2 says so, rather than read nothing.
        raise ValueError(
            f"{path}: pytest configuration must be under a [pytest] table (found top-level options: {', '.join(stray)})"
        )
    return None


def _table(document: dict, key: str, path: Path) -> dict:
    """*document*'s table *key*, empty when absent; a value that is no table is an error."""
    value = document.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{path}: {key} is not a table.")  # noqa: TRY004 — a config error, caught with the rest
    return value
