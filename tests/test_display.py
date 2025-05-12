"""Unit tests for the display module."""

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
