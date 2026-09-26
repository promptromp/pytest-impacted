"""Display and logging utilities."""

import logging


logger = logging.getLogger(__name__)


def _terminal_reporter(session):
    """The session's terminal reporter, or ``None`` outside pytest or under ``-p no:terminal``."""
    return session.config.pluginmanager.getplugin("terminalreporter") if session else None


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
