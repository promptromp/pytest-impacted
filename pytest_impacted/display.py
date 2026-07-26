"""Display and logging utilities."""

import logging


logger = logging.getLogger(__name__)


def notify(message: str, session) -> None:
    """Print a message to the console."""
    if session:
        session.config.pluginmanager.getplugin("terminalreporter").write(
            f"\n{message}\n",
            yellow=True,
            bold=True,
        )
    else:
        logger.info("\n%s\n", message)


def warn(message: str, session) -> None:
    """Print a warning message to the console."""
    if session:
        session.config.pluginmanager.getplugin("terminalreporter").write(
            f"\nWARNING: {message}\n",
            yellow=True,
            bold=True,
        )
    else:
        logger.warning("\nWARNING: %s\n", message)
