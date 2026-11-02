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
CONFIG_NAMES = (
    *(("pytest.toml", ".pytest.toml") if PYTEST_9 else ()),
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
)
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
        for name in CONFIG_NAMES:
            path = directory / name
            # os.path, not Path.is_file(): that raises inside a listable but unsearchable directory.
            if not os.path.isfile(path):
                continue
            if (config := _load(path)) is not None:
                return config
            # pytest 8.1 made a pyproject.toml without pytest settings the configuration, as a last resort.
            fallback = fallback or (path if name == "pyproject.toml" and PYTEST_8_1 else None)
    return PytestConfig(path=fallback)


def load_pytest_config(path: Path) -> PytestConfig:
    """The configuration of the file named by ``-c``, which pytest reads even without a pytest section."""
    path = Path(os.path.abspath(path))
    # A path that is no regular file (/dev/null) holds no configuration, and is no error.
    if PYTEST_9_2 and path.suffix not in _LOADERS and os.path.isfile(path):
        supported = ", ".join(sorted(_LOADERS))
        raise ValueError(f"Config file '{path}' has an unsupported format. Supported extensions are: {supported}.")
    return _load(path) or PytestConfig(path=path)


def _load(path: Path) -> PytestConfig | None:
    """*path*'s pytest configuration, or ``None`` if it has none (pytest's ``load_config_dict_from_file``)."""
    loader = _LOADERS.get(path.suffix)
    return loader(path) if loader else None  # pytest reads no other kind of file


def _load_ini(path: Path) -> PytestConfig | None:
    sections = iniconfig.IniConfig(str(path))
    if "pytest" in sections:
        return PytestConfig(path, dict(sections["pytest"].items()))
    # A pytest.ini is the configuration even when empty.
    return PytestConfig(path) if path.name in ("pytest.ini", ".pytest.ini") else None


def _load_cfg(path: Path) -> PytestConfig | None:
    sections = iniconfig.IniConfig(str(path))
    if "tool:pytest" in sections:
        return PytestConfig(path, dict(sections["tool:pytest"].items()))
    if "pytest" in sections:
        # pytest fails the run on one, so the search must not go on past it.
        raise ValueError(f"{path}: [pytest] section in setup.cfg files is no longer supported, use [tool:pytest].")
    return None


def _load_toml(path: Path) -> PytestConfig | None:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise ValueError(f"{path}: {err}") from err  # as pytest names the file
    if PYTEST_9 and path.name in ("pytest.toml", ".pytest.toml"):
        # Like a pytest.ini, the configuration even when empty.
        return PytestConfig(path, _pytest_table(document, path) or {}, native=True)
    config = _tool_pytest(document, path)
    if not PYTEST_9_2 or path.name == "pyproject.toml":
        return config
    # Named by -c: since pytest 9.2 read as a pytest.toml when it has no [tool.pytest] tables.
    if config is None:
        table = _pytest_table(document, path)
        return None if table is None else PytestConfig(path, table, native=True)
    if "pytest" in document:
        raise ValueError(f"{path}: Cannot use both [pytest] and [tool.pytest]/[tool.pytest.ini_options] in one file.")
    return config


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


# pytest's loaders by suffix; within one, a file's name (pytest.toml, pyproject.toml) picks its rules.
_LOADERS = {".cfg": _load_cfg, ".ini": _load_ini, ".toml": _load_toml}


def _table(document: dict, key: str, path: Path) -> dict:
    """*document*'s table *key*, empty when absent; a value that is no table is an error."""
    value = document.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{path}: {key} is not a table.")  # noqa: TRY004 — a config error, caught with the rest
    return value
