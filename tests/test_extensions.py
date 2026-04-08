"""Unit tests for the extension system."""

from unittest.mock import MagicMock, patch

import pytest

from pytest_impacted.extensions import (
    ConfigOption,
    ExtensionMetadata,
    StrategyProtocol,
    _coerce_value,
    _instantiate_strategy,
    _validate_strategy_class,
    build_strategy_with_extensions,
    clear_extension_cache,
    discover_extension_metadata,
    get_ext_cli_flag,
    get_ext_ini_name,
    load_extensions,
)
from pytest_impacted.strategies import (
    ASTImpactStrategy,
    CompositeImpactStrategy,
    DependencyFileImpactStrategy,
    ImpactStrategy,
    PytestImpactStrategy,
)


# -- Test fixtures and helpers --


class SimpleStrategy(ImpactStrategy):
    """A simple test strategy with no config."""

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return ["tests.test_simple"]


class ConfigurableStrategy(ImpactStrategy):
    """A strategy that accepts configuration."""

    config_options = [
        ConfigOption(name="threshold", help="Score threshold", type=int, default=80),
        ConfigOption(name="data_file", help="Path to data file", default=".data"),
    ]
    priority = 50

    def __init__(self, threshold: int = 80, data_file: str = ".data"):
        self.threshold = threshold
        self.data_file = data_file

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return ["tests.test_configurable"]


class DuckTypedStrategy:
    """A strategy that uses duck typing (no ImpactStrategy inheritance)."""

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return ["tests.test_duck"]


class InvalidStrategy:
    """A class that does not implement find_impacted_tests."""

    pass


class HighPriorityStrategy(ImpactStrategy):
    """A strategy with high priority (low number = runs first)."""

    priority = 10

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return ["tests.test_high_priority"]


class LowPriorityStrategy(ImpactStrategy):
    """A strategy with low priority."""

    priority = 200

    def find_impacted_tests(self, changed_files, impacted_modules, ns_module, **kwargs):
        return ["tests.test_low_priority"]


def _make_mock_entry_point(name: str, cls: type) -> MagicMock:
    """Create a mock entry point that loads the given class."""
    ep = MagicMock()
    ep.name = name
    ep.value = f"{cls.__module__}:{cls.__qualname__}"
    ep.load.return_value = cls
    return ep


# -- Tests --


class TestConfigOption:
    """Test the ConfigOption dataclass."""

    def test_defaults(self):
        opt = ConfigOption(name="flag", help="A flag")
        assert opt.name == "flag"
        assert opt.help == "A flag"
        assert opt.type is str
        assert opt.default is None
        assert opt.required is False

    def test_custom_values(self):
        opt = ConfigOption(name="threshold", help="Min score", type=int, default=80, required=True)
        assert opt.type is int
        assert opt.default == 80
        assert opt.required is True

    def test_frozen(self):
        opt = ConfigOption(name="x", help="y")
        with pytest.raises(AttributeError):
            opt.name = "z"  # type: ignore[misc]


class TestExtensionMetadata:
    """Test the ExtensionMetadata dataclass."""

    def test_defaults(self):
        meta = ExtensionMetadata(name="test", strategy_class=SimpleStrategy)
        assert meta.config_options == []
        assert meta.priority == 100


class TestStrategyProtocol:
    """Test the StrategyProtocol runtime checking."""

    def test_abc_strategy_is_protocol_compliant(self):
        assert isinstance(SimpleStrategy(), StrategyProtocol)

    def test_duck_typed_strategy_is_protocol_compliant(self):
        assert isinstance(DuckTypedStrategy(), StrategyProtocol)

    def test_invalid_strategy_is_not_protocol_compliant(self):
        assert not isinstance(InvalidStrategy(), StrategyProtocol)


class TestNamingHelpers:
    """Test config naming functions."""

    def test_get_ext_ini_name(self):
        assert get_ext_ini_name("coverage", "threshold") == "impacted_ext_coverage_threshold"

    def test_get_ext_cli_flag(self):
        assert get_ext_cli_flag("coverage", "threshold") == "--impacted-ext-coverage-threshold"

    def test_get_ext_cli_flag_with_underscores(self):
        assert get_ext_cli_flag("my_ext", "data_file") == "--impacted-ext-my-ext-data-file"


class TestValidateStrategyClass:
    """Test strategy class validation."""

    def test_valid_abc_class(self):
        assert _validate_strategy_class("test", SimpleStrategy) is True

    def test_valid_duck_typed_class(self):
        assert _validate_strategy_class("test", DuckTypedStrategy) is True

    def test_invalid_class_no_method(self):
        assert _validate_strategy_class("test", InvalidStrategy) is False

    def test_non_class(self):
        assert _validate_strategy_class("test", "not a class") is False

    def test_instance_not_class(self):
        assert _validate_strategy_class("test", SimpleStrategy()) is False


class TestCoerceValue:
    """Test config value coercion."""

    def test_none_passthrough(self):
        assert _coerce_value(None, str) is None

    def test_already_correct_type(self):
        assert _coerce_value(42, int) == 42
        assert _coerce_value("hello", str) == "hello"

    def test_str_to_int(self):
        assert _coerce_value("42", int) == 42

    def test_str_to_float(self):
        assert _coerce_value("3.14", float) == pytest.approx(3.14)

    def test_str_to_bool_true(self):
        for val in ("true", "True", "TRUE", "1", "yes", "Yes"):
            assert _coerce_value(val, bool) is True

    def test_str_to_bool_false(self):
        for val in ("false", "False", "0", "no", ""):
            assert _coerce_value(val, bool) is False


class TestInstantiateStrategy:
    """Test strategy instantiation with config."""

    def test_no_config(self):
        ext = ExtensionMetadata(name="test", strategy_class=SimpleStrategy)
        instance = _instantiate_strategy(ext, {})
        assert isinstance(instance, SimpleStrategy)

    def test_with_config(self):
        ext = ExtensionMetadata(name="test", strategy_class=ConfigurableStrategy)
        instance = _instantiate_strategy(ext, {"threshold": 90, "data_file": "/tmp/data"})
        assert instance.threshold == 90
        assert instance.data_file == "/tmp/data"

    def test_partial_config(self):
        ext = ExtensionMetadata(name="test", strategy_class=ConfigurableStrategy)
        instance = _instantiate_strategy(ext, {"threshold": 50})
        assert instance.threshold == 50
        assert instance.data_file == ".data"  # default

    def test_extra_config_ignored(self):
        ext = ExtensionMetadata(name="test", strategy_class=ConfigurableStrategy)
        instance = _instantiate_strategy(ext, {"threshold": 50, "unknown_param": "ignored"})
        assert instance.threshold == 50


class TestDiscoverExtensionMetadata:
    """Test extension discovery."""

    def setup_method(self):
        clear_extension_cache()

    def teardown_method(self):
        clear_extension_cache()

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_no_extensions(self, mock_entry_points):
        mock_entry_points.return_value = []
        result = discover_extension_metadata()
        assert result == ()

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_discover_simple_strategy(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("simple", SimpleStrategy)]
        result = discover_extension_metadata()
        assert len(result) == 1
        assert result[0].name == "simple"
        assert result[0].strategy_class is SimpleStrategy
        assert result[0].config_options == []
        assert result[0].priority == 100

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_discover_configurable_strategy(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("conf", ConfigurableStrategy)]
        result = discover_extension_metadata()
        assert len(result) == 1
        assert result[0].name == "conf"
        assert len(result[0].config_options) == 2
        assert result[0].priority == 50

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_discover_duck_typed_strategy(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("duck", DuckTypedStrategy)]
        result = discover_extension_metadata()
        assert len(result) == 1
        assert result[0].name == "duck"

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_invalid_class_skipped(self, mock_entry_points):
        mock_entry_points.return_value = [
            _make_mock_entry_point("valid", SimpleStrategy),
            _make_mock_entry_point("invalid", InvalidStrategy),
        ]
        result = discover_extension_metadata()
        assert len(result) == 1
        assert result[0].name == "valid"

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_import_error_skipped(self, mock_entry_points):
        broken_ep = MagicMock()
        broken_ep.name = "broken"
        broken_ep.value = "nonexistent:Strategy"
        broken_ep.load.side_effect = ImportError("No module named 'nonexistent'")

        mock_entry_points.return_value = [
            broken_ep,
            _make_mock_entry_point("valid", SimpleStrategy),
        ]
        result = discover_extension_metadata()
        assert len(result) == 1
        assert result[0].name == "valid"

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_discovery_is_cached(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("simple", SimpleStrategy)]
        result1 = discover_extension_metadata()
        result2 = discover_extension_metadata()
        assert result1 is result2
        mock_entry_points.assert_called_once()

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_cache_clear(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("simple", SimpleStrategy)]
        discover_extension_metadata()
        clear_extension_cache()
        discover_extension_metadata()
        assert mock_entry_points.call_count == 2

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_multiple_extensions(self, mock_entry_points):
        mock_entry_points.return_value = [
            _make_mock_entry_point("simple", SimpleStrategy),
            _make_mock_entry_point("conf", ConfigurableStrategy),
            _make_mock_entry_point("duck", DuckTypedStrategy),
        ]
        result = discover_extension_metadata()
        assert len(result) == 3
        names = {e.name for e in result}
        assert names == {"simple", "conf", "duck"}


class TestLoadExtensions:
    """Test extension loading and instantiation."""

    def setup_method(self):
        clear_extension_cache()

    def teardown_method(self):
        clear_extension_cache()

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_load_no_extensions(self, mock_entry_points):
        mock_entry_points.return_value = []
        instances = load_extensions()
        assert instances == []

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_load_simple_extension(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("simple", SimpleStrategy)]
        instances = load_extensions()
        assert len(instances) == 1
        assert isinstance(instances[0], SimpleStrategy)

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_load_with_config_namespaced(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("conf", ConfigurableStrategy)]
        instances = load_extensions(
            ext_config={"impacted_ext_conf_threshold": "90", "impacted_ext_conf_data_file": "/tmp/x"}
        )
        assert len(instances) == 1
        assert instances[0].threshold == 90
        assert instances[0].data_file == "/tmp/x"

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_load_with_config_raw_names(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("conf", ConfigurableStrategy)]
        instances = load_extensions(ext_config={"threshold": "75"})
        assert len(instances) == 1
        assert instances[0].threshold == 75

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_load_disabled_extension(self, mock_entry_points):
        mock_entry_points.return_value = [
            _make_mock_entry_point("a", SimpleStrategy),
            _make_mock_entry_point("b", DuckTypedStrategy),
        ]
        instances = load_extensions(disabled=("a",))
        assert len(instances) == 1

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_load_instantiation_error(self, mock_entry_points):
        """A strategy that fails during __init__ is skipped."""

        class BrokenInit(ImpactStrategy):
            def __init__(self):
                raise RuntimeError("boom")

            def find_impacted_tests(self, *args, **kwargs):
                return []

        mock_entry_points.return_value = [
            _make_mock_entry_point("broken", BrokenInit),
            _make_mock_entry_point("valid", SimpleStrategy),
        ]
        instances = load_extensions()
        assert len(instances) == 1
        assert isinstance(instances[0], SimpleStrategy)


class TestBuildStrategyWithExtensions:
    """Test the full strategy builder."""

    def setup_method(self):
        clear_extension_cache()

    def teardown_method(self):
        clear_extension_cache()

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_no_extensions_returns_defaults(self, mock_entry_points):
        mock_entry_points.return_value = []
        strategy = build_strategy_with_extensions()
        assert isinstance(strategy, CompositeImpactStrategy)
        assert len(strategy.strategies) == 3
        assert isinstance(strategy.strategies[0], ASTImpactStrategy)
        assert isinstance(strategy.strategies[1], PytestImpactStrategy)
        assert isinstance(strategy.strategies[2], DependencyFileImpactStrategy)

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_no_extensions_no_dep_files(self, mock_entry_points):
        mock_entry_points.return_value = []
        strategy = build_strategy_with_extensions(watch_dep_files=False)
        assert len(strategy.strategies) == 2

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_with_extension(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("simple", SimpleStrategy)]
        strategy = build_strategy_with_extensions()
        assert len(strategy.strategies) == 4
        # Last strategy is the extension
        assert isinstance(strategy.strategies[3], SimpleStrategy)

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_with_disabled_extension(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("simple", SimpleStrategy)]
        strategy = build_strategy_with_extensions(disabled=("simple",))
        assert len(strategy.strategies) == 3  # Only built-ins

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_extension_priority_ordering(self, mock_entry_points):
        mock_entry_points.return_value = [
            _make_mock_entry_point("low", LowPriorityStrategy),
            _make_mock_entry_point("high", HighPriorityStrategy),
        ]
        strategy = build_strategy_with_extensions()
        # Built-ins come first (3), then extensions sorted by priority
        ext_strategies = strategy.strategies[3:]
        assert isinstance(ext_strategies[0], HighPriorityStrategy)  # priority=10
        assert isinstance(ext_strategies[1], LowPriorityStrategy)  # priority=200

    @patch("pytest_impacted.extensions.importlib.metadata.entry_points")
    def test_with_extension_config(self, mock_entry_points):
        mock_entry_points.return_value = [_make_mock_entry_point("conf", ConfigurableStrategy)]
        strategy = build_strategy_with_extensions(
            ext_config={"impacted_ext_conf_threshold": "95"},
        )
        ext = strategy.strategies[3]
        assert isinstance(ext, ConfigurableStrategy)
        assert ext.threshold == 95


class TestImpactStrategyClassVars:
    """Test that ImpactStrategy has the expected ClassVars for extensions."""

    def test_default_config_options(self):
        assert ImpactStrategy.config_options == []

    def test_default_priority(self):
        assert ImpactStrategy.priority == 100

    def test_builtin_strategies_inherit_defaults(self):
        assert ASTImpactStrategy.config_options == []
        assert ASTImpactStrategy.priority == 100
        assert PytestImpactStrategy.config_options == []
        assert DependencyFileImpactStrategy.config_options == []
