"""What the module and tests-dir settings may hold: the checks the plugin and the CLI both make, once.

They disagreed before: ``--impacted-module=./app`` failed the plugin (read as ``//app``) and passed the CLI, a
dotted tests dir (``suite.unit`` for ``suite/unit``) passed the CLI only, and only the plugin suggested a fix.
"""

import os
from pathlib import Path

from pytest_impacted.traversal import package_name_to_path, unnameable_tests_dir, walked_directory


def normalized_module(module: str) -> str:
    """*module* as the analysis reads it: a path tidied (``./app/`` → ``app``), a dotted name as it is."""
    return os.path.normpath(module).replace(os.sep, "/") if ("/" in module or os.sep in module) else module


def module_problem(module: str, root: Path, flag: str) -> str | None:
    """Why *module* (given with *flag*) names no package directory under *root*, with a likely fix; ``None`` if it
    does. A path (``src/app``, ``./app``) or a dotted name (``src.app``)."""
    module_dir = package_name_to_path(normalized_module(module))
    # os.path, not Path.is_dir(): inside an unsearchable directory it is missing, rather than raising.
    if os.path.isdir(root / module_dir):
        return None
    if "-" in module and os.path.isdir(root / package_name_to_path(normalized_module(module.replace("-", "_")))):
        suggestion = module.replace("-", "_")
        return (
            f"Module '{module}' not found. Python module names use underscores, not hyphens. "
            f"Did you mean: {flag}={suggestion}"
        )
    if os.path.isdir(root / "src" / module_dir):
        return (
            f"Module '{module}' not found under '{root}', but found at 'src/{module_dir}'. "
            f"For src-layout projects, use: {flag}=src/{module_dir}"
        )
    return (
        f"Module '{module}' not found (no '{module_dir}/' directory under '{root}'). "
        f"Make sure {flag} is a valid Python package name relative to the rootdir."
    )


def tests_dir_problem(tests_dir: str, root: Path, flag: str) -> str | None:
    """Why *tests_dir* (given with *flag*) is no directory the analysis can walk under *root*; ``None`` if it is.
    A path, or a dotted spelling of one (``suite.unit`` for ``suite/unit``)."""
    if not (os.path.isdir(root / tests_dir) or os.path.isdir(root / walked_directory(tests_dir))):
        return f"Tests directory '{tests_dir}' does not exist under '{root}'. Please check the path passed to {flag}."
    return unnameable_tests_dir(tests_dir, root)
