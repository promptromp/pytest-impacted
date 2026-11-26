"""Display and logging utilities."""

import logging


logger = logging.getLogger(__name__)


def _terminal_reporter(session):
    """The session's terminal reporter, or ``None`` outside pytest or under ``-p no:terminal``."""
    return session.config.pluginmanager.getplugin("terminalreporter") if session else None


def describe(err: BaseException) -> str:
    """*err* as ``Type: message`` on one line, for a warning — never raising, whatever its ``__str__`` does.

    A third party's exception may span lines (a validation error listing fields), or fail to print at all; the
    warning reporting it must still be one line, and must never be what crashes the run.
    """
    try:
        text = str(err)
    except Exception:  # noqa: BLE001 — the exception being reported is the error, not its text
        text = "<unprintable>"
    text = " ".join(text.split())
    return f"{type(err).__name__}: {text}" if text else type(err).__name__


def notify(message: str, session) -> None:
    """Print a message to the console."""
    if reporter := _terminal_reporter(session):
        reporter.write(f"\n{message}\n", yellow=True, bold=True)
    else:
        logger.info("\n%s\n", message)


def warn(message: str, session) -> None:
    """Print a warning message to the console."""
    if reporter := _terminal_reporter(session):
        reporter.write(f"\nWARNING: {message}\n", yellow=True, bold=True)
    else:
        logger.warning("\nWARNING: %s\n", message)
