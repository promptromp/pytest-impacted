"""Display and logging utilities."""

import logging


def notify(message: str, session) -> None:
    """Print a message to the console."""
    if session:
        session.config.pluginmanager.getplugin("terminalreporter").write(
            f"\n{message}\n",
            yellow=True,
            bold=True,
        )
    else:
        logging.info("\n%s\n", message)


def warn(message: str, session) -> None:
    """Print a warning message to the console."""
    if session:
        session.config.pluginmanager.getplugin("terminalreporter").write(
            f"\nWARNING: {message}\n",
            yellow=True,
            bold=True,
        )
    else:
        logging.warning("\nWARNING: %s\n", message)
