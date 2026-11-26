"""Which changes count: the git modes, apart from the git machinery, so the pytest entry point loads it for free."""

from enum import StrEnum


class GitMode(StrEnum):
    """Git modes for the plugin."""

    UNSTAGED = "unstaged"
    BRANCH = "branch"
