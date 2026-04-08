"""Extension system for third-party strategy plugins.

Third-party packages can register custom impact analysis strategies via Python entry points.
Strategies are discovered at runtime and composed into the analysis pipeline.

Entry point group: ``pytest_impacted.strategies``

Example third-party pyproject.toml::

    [project.entry-points."pytest_impacted.strategies"]
    my_strategy = "my_package.strategy:MyCustomStrategy"
"""

from __future__ import annotations
import importlib.metadata
import inspect
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Sequence, runtime_checkable


if TYPE_CHECKING:
    from pytest_impacted.strategies import ImpactStrategy

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "pytest_impacted.strategies"


@dataclass(frozen=True)
class ConfigOption:
    """Declares a configuration option for a strategy extension.

    Extension authors define these as a class-level ``config_options`` list on their
    strategy class. Each option is automatically registered as a CLI flag and ini setting.

    Example::

        class MyStrategy(ImpactStrategy):
            config_options = [
                ConfigOption(name="threshold", help="Min score to consider", type=int, default=80),
            ]

            def __init__(self, threshold: int = 80):
                self.threshold = threshold
    """

    name: str
    """Option name (used as constructor kwarg and config key suffix)."""

    help: str
    """Help text shown in ``--help`` output."""

    type: type = str
    """Python type for the option value (str, bool, int, float)."""

    default: Any = None
    """Default value when not specified by the user."""

    required: bool = False
    """Whether the option must be explicitly set."""


@dataclass
class ExtensionMetadata:
    """Metadata about a discovered extension (before instantiation)."""

    name: str
    """Entry point name (user-facing identifier for enable/disable)."""

    strategy_class: type
    """The loaded strategy class."""

    config_options: list[ConfigOption] = field(default_factory=list)
    """Configuration options declared by the strategy."""

    priority: int = 100
    """Ordering weight (lower = runs earlier, default = 100)."""


@runtime_checkable
class StrategyProtocol(Protocol):
    """Protocol for duck-typed strategy implementations.

    Extensions can implement this protocol instead of inheriting from
    :class:`~pytest_impacted.strategies.ImpactStrategy`. This enables
    zero-dependency extensions that don't need to import pytest-impacted.
    """

    def find_impacted_tests(
        self,
        changed_files: list[str],
        impacted_modules: list[str],
        ns_module: str,
        tests_package: str | None = ...,
        root_dir: Path | None = ...,
        session: Any = ...,
    ) -> list[str]: ...


def get_ext_ini_name(ext_name: str, opt_name: str) -> str:
    """Return the pytest ini/config key for an extension option.

    >>> get_ext_ini_name("coverage", "threshold")
    'impacted_ext_coverage_threshold'
    """
    return f"impacted_ext_{ext_name}_{opt_name}"


def get_ext_cli_flag(ext_name: str, opt_name: str) -> str:
    """Return the CLI flag name for an extension option.

    >>> get_ext_cli_flag("coverage", "threshold")
    '--impacted-ext-coverage-threshold'
    """
    return f"--impacted-ext-{ext_name.replace('_', '-')}-{opt_name.replace('_', '-')}"


def _validate_strategy_class(name: str, cls: Any) -> bool:
    """Check whether a loaded class conforms to the strategy interface."""
    if not isinstance(cls, type):
        logger.warning("Extension '%s': entry point resolved to %r, expected a class. Skipping.", name, type(cls))
        return False

    if not hasattr(cls, "find_impacted_tests") or not callable(cls.find_impacted_tests):
        logger.warning(
            "Extension '%s': class %s.%s does not implement find_impacted_tests(). Skipping.",
            name,
            cls.__module__,
            cls.__qualname__,
        )
        return False

    return True


@lru_cache(maxsize=1)
def discover_extension_metadata() -> tuple[ExtensionMetadata, ...]:
    """Discover and load metadata for all registered strategy extensions.

    Uses ``importlib.metadata.entry_points`` to scan the ``pytest_impacted.strategies``
    group. Each entry point is loaded, validated, and its class-level metadata
    (``config_options``, ``priority``) is extracted.

    Returns a tuple (for hashability/caching) of :class:`ExtensionMetadata` instances.
    Errors in individual extensions are logged and skipped.
    """
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        logger.exception("Failed to query entry points for group '%s'.", ENTRY_POINT_GROUP)
        return ()

    extensions: list[ExtensionMetadata] = []

    for ep in eps:
        try:
            cls = ep.load()
        except Exception:
            logger.exception("Extension '%s': failed to load entry point '%s'. Skipping.", ep.name, ep.value)
            continue

        if not _validate_strategy_class(ep.name, cls):
            continue

        config_options = list(getattr(cls, "config_options", []))
        priority = getattr(cls, "priority", 100)

        extensions.append(
            ExtensionMetadata(
                name=ep.name,
                strategy_class=cls,
                config_options=config_options,
                priority=priority,
            )
        )
        logger.debug(
            "Discovered extension '%s' -> %s.%s (priority=%d)", ep.name, cls.__module__, cls.__qualname__, priority
        )

    return tuple(extensions)


def clear_extension_cache() -> None:
    """Clear the extension discovery cache.

    Useful for testing or when extensions change at runtime.
    """
    discover_extension_metadata.cache_clear()


def _coerce_value(value: Any, target_type: type) -> Any:
    """Coerce a config value to the target type."""
    if value is None:
        return None
    if isinstance(value, target_type):
        return value
    if target_type is bool:
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    return target_type(value)


def _instantiate_strategy(ext: ExtensionMetadata, config: dict[str, Any]) -> Any:
    """Instantiate a strategy class, passing config values to matching constructor params."""
    cls = ext.strategy_class
    sig = inspect.signature(cls)
    kwargs: dict[str, Any] = {}

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if param_name in config:
            kwargs[param_name] = config[param_name]

    return cls(**kwargs)


def load_extensions(
    *,
    disabled: Sequence[str] = (),
    ext_config: dict[str, Any] | None = None,
) -> list[Any]:
    """Discover and instantiate all registered strategy extensions.

    Args:
        disabled: Extension names to skip.
        ext_config: Flat dict of config values. Keys can be either raw option names
            (e.g. ``"threshold"``) or namespaced ini names
            (e.g. ``"impacted_ext_coverage_threshold"``).

    Returns:
        List of instantiated strategy objects.
    """
    config = ext_config or {}
    instances: list[Any] = []

    for ext in discover_extension_metadata():
        if ext.name in disabled:
            logger.info("Extension '%s' is disabled by configuration.", ext.name)
            continue

        # Extract config for this extension: try namespaced keys first, then raw names
        ext_cfg: dict[str, Any] = {}
        for opt in ext.config_options:
            ini_name = get_ext_ini_name(ext.name, opt.name)
            if ini_name in config:
                ext_cfg[opt.name] = _coerce_value(config[ini_name], opt.type)
            elif opt.name in config:
                ext_cfg[opt.name] = _coerce_value(config[opt.name], opt.type)
            elif opt.default is not None:
                ext_cfg[opt.name] = opt.default

        try:
            instance = _instantiate_strategy(ext, ext_cfg)
        except Exception:
            logger.exception("Extension '%s': failed to instantiate. Skipping.", ext.name)
            continue

        instances.append(instance)
        logger.debug("Loaded extension '%s' with config: %s", ext.name, ext_cfg)

    return instances


def build_strategy_with_extensions(
    *,
    watch_dep_files: bool = True,
    disabled: Sequence[str] = (),
    ext_config: dict[str, Any] | None = None,
) -> "ImpactStrategy":
    """Build a composite strategy combining built-in and extension strategies.

    This is the main entry point for constructing the full strategy pipeline.
    Built-in strategies come first, followed by extensions sorted by priority.

    Args:
        watch_dep_files: Whether to include DependencyFileImpactStrategy.
        disabled: Extension names to exclude.
        ext_config: Configuration values for extensions.

    Returns:
        A CompositeImpactStrategy wrapping all strategies.
    """
    from pytest_impacted.strategies import CompositeImpactStrategy, get_default_strategies  # noqa: PLC0415

    builtin_strategies = get_default_strategies(watch_dep_files=watch_dep_files)
    ext_strategies = load_extensions(disabled=disabled, ext_config=ext_config)

    # Sort extensions by priority (built-ins keep their fixed order)
    ext_strategies.sort(key=lambda s: getattr(s, "priority", 100))

    all_strategies = builtin_strategies + ext_strategies
    return CompositeImpactStrategy(all_strategies)
