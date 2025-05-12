"""Display and logging utilities."""


def notify(message: str, session) -> None:
    """Print a message to the console."""
    session.config.pluginmanager.getplugin("terminalreporter").write(
        f"\n{message}\n",
        yellow=True,
        bold=True,
    )


def warn(message: str, session) -> None:
    """Print a warning message to the console."""
    session.config.pluginmanager.getplugin("terminalreporter").write(
        f"\nWARNING: {message}\n",
        yellow=True,
        bold=True,
    )
