"""Unit tests for the display module."""

import logging
from unittest.mock import MagicMock

from pytest_impacted import display


def make_mock_session():
    mock_terminalreporter = MagicMock()
    mock_pluginmanager = MagicMock()
    mock_pluginmanager.getplugin.return_value = mock_terminalreporter
    mock_config = MagicMock()
    mock_config.pluginmanager = mock_pluginmanager
    mock_session = MagicMock()
    mock_session.config = mock_config
    return mock_session, mock_terminalreporter


def test_notify():
    session, terminalreporter = make_mock_session()
    display.notify("Hello, world!", session)
    terminalreporter.write.assert_called_once()
    args, kwargs = terminalreporter.write.call_args
    assert "Hello, world!" in args[0]
    assert kwargs.get("yellow") is True
    assert kwargs.get("bold") is True


def test_warn():
    session, terminalreporter = make_mock_session()
    display.warn("Danger!", session)
    terminalreporter.write.assert_called_once()
    args, kwargs = terminalreporter.write.call_args
    assert "WARNING: Danger!" in args[0]
    assert kwargs.get("yellow") is True
    assert kwargs.get("bold") is True


def test_notify_without_session(caplog):
    """Test notify function when session is None."""
    with caplog.at_level(logging.INFO, logger=display.logger.name):
        display.notify("Hello, world!", None)

    record = caplog.records[-1]
    assert record.name == "pytest_impacted.display"
    assert record.levelno == logging.INFO
    assert record.getMessage() == "\nHello, world!\n"


def test_warn_without_session(caplog):
    """Test warn function when session is None."""
    with caplog.at_level(logging.WARNING, logger=display.logger.name):
        display.warn("Danger!", None)

    record = caplog.records[-1]
    assert record.name == "pytest_impacted.display"
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "\nWARNING: Danger!\n"
