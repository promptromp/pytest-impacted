"""Unit tests for the display module."""

import logging
from unittest.mock import MagicMock, patch

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


def test_notify_without_session():
    """Test notify function when session is None."""
    with patch.object(logging, "info") as mock_info:
        display.notify("Hello, world!", None)
        mock_info.assert_called_once_with("\n%s\n", "Hello, world!")


def test_warn_without_session():
    """Test warn function when session is None."""
    with patch.object(logging, "warning") as mock_warning:
        display.warn("Danger!", None)
        mock_warning.assert_called_once_with("\nWARNING: %s\n", "Danger!")
