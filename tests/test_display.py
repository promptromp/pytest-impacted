"""Unit tests for the display module."""

import logging
from unittest.mock import MagicMock

import pytest

from pytest_impacted import display
from pytest_impacted.display import describe


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


def test_messages_fall_back_to_logging_without_a_terminal(caplog):
    """Under ``-p no:terminal`` there is no reporter; the fail-open notice must not crash."""
    session, _ = make_mock_session()
    session.config.pluginmanager.getplugin.return_value = None

    with caplog.at_level(logging.INFO, logger=display.logger.name):
        display.notify("Hello", session)
        display.warn("Danger!", session)

    assert [r.getMessage() for r in caplog.records] == ["\nHello\n", "\nWARNING: Danger!\n"]


class _Unprintable(Exception):
    def __str__(self):
        raise AttributeError("never set")


@pytest.mark.parametrize(
    ("err", "described"),
    [
        (ValueError("bad value"), "ValueError: bad value"),
        (ValueError("3 errors:\n  a: missing\n  b: missing"), "ValueError: 3 errors: a: missing b: missing"),
        (RuntimeError(), "RuntimeError"),
        (_Unprintable(), "_Unprintable: <unprintable>"),
    ],
    ids=["one line", "several lines", "no message", "a __str__ that raises"],
)
def test_describe_is_one_line_and_never_raises(err, described):
    assert describe(err) == described
