"""pytest-impacted: selectively run tests impacted by code changes."""

from pytest_impacted.extensions import ConfigOption, StrategyProtocol
from pytest_impacted.strategies import ImpactStrategy


__all__ = ["ConfigOption", "ImpactStrategy", "StrategyProtocol"]
