"""The pytest settings the ``impacted-tests`` CLI reads, from the file pytest would read them from.

The CLI must not build a pytest ``Config`` to ask it: that imports the initial conftests,
and analysis never runs project code. So this mirrors pytest 9's search
(``_pytest.config.findpaths``) instead: the first directory, from the start up, holding a
file with pytest configuration, in pytest's order of names; a ``pyproject.toml`` without
any only if nothing else is found. ``tests/test_ini.py`` holds it to pytest's own answer.
"""

import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import iniconfig


CONFIG_NAMES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
_TRUE = ("y", "yes", "t", "true", "on", "1")
_FALSE = ("n", "no", "f", "false", "off", "0")


@dataclass(frozen=True)
class PytestConfig:
    """The settings of one pytest configuration file (none: ``path`` is ``None``).

    *native* is pytest 9's TOML mode (``pytest.toml``, ``[tool.pytest]``): values keep
    their TOML types, which pytest checks rather than converts. Otherwise they are
    strings, or lists from a ``[tool.pytest.ini_options]`` table, converted by type.
    """

    path: Path | None = None
    values: dict[str, object] = field(default_factory=dict)
    native: bool = False

    def string(self, name: str) -> str | None:
        """A setting the plugin declares untyped, such as ``impacted_module``."""
        return None if (value := self._get(name, str)) is None else str(value)

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
        return shlex.split(value) if isinstance(value, str) else value  # type: ignore[return-value]

    def _get(self, name: str, native_type: type) -> object:
        value = self.values.get(name)
        if value is not None and self.native and not isinstance(value, native_type):
            raise ValueError(f"{name} in {self.path}: expected a {native_type.__name__}, got {value!r}")
        return value


def find_pytest_config(start: Path) -> PytestConfig:
    """The configuration pytest finds for a run from *start*."""
    fallback = None
    for directory in (start.absolute(), *start.absolute().parents):
        for name in CONFIG_NAMES:
            path = directory / name
            if not path.is_file():
                continue
            if (config := _load(path)) is not None:
                return config
            fallback = fallback or (path if name == "pyproject.toml" else None)
    return PytestConfig(path=fallback)


def load_pytest_config(path: Path) -> PytestConfig:
    """The configuration of the file named by ``-c``, which pytest reads even without a pytest section."""
    return _load(path.absolute()) or PytestConfig(path=path.absolute())


def _load(path: Path) -> PytestConfig | None:
    """*path*'s pytest configuration, or ``None`` if it has none (pytest's ``load_config_dict_from_file``)."""
    if path.suffix == ".toml":
        return _load_toml(path)
    sections = iniconfig.IniConfig(str(path))
    section = "tool:pytest" if path.suffix == ".cfg" else "pytest"
    if section in sections:
        return PytestConfig(path, dict(sections[section].items()))
    # A pytest.ini is the configuration even when empty.
    return PytestConfig(path) if path.name in ("pytest.ini", ".pytest.ini") else None


def _load_toml(path: Path) -> PytestConfig | None:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    if path.name in ("pytest.toml", ".pytest.toml"):
        # Like a pytest.ini, the configuration even when empty.
        return PytestConfig(path, document.get("pytest", {}), native=True)
    tool = document.get("tool", {}).get("pytest", {})
    native = {key: value for key, value in tool.items() if key != "ini_options"}
    ini_mode = tool.get("ini_options")
    if native and ini_mode:
        raise ValueError(f"{path}: Cannot use both [tool.pytest] and [tool.pytest.ini_options].")
    if native:
        return PytestConfig(path, native, native=True)
    if ini_mode is None:
        return None
    return PytestConfig(
        path, {key: value if isinstance(value, list) else str(value) for key, value in ini_mode.items()}
    )
