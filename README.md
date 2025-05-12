# pytest-impacted

[![CI](https://github.com/promptromp/pytest-impacted/actions/workflows/ci.yml/badge.svg)](https://github.com/promptromp/pytest-impacted/actions/workflows/ci.yml)
[![GitHub License](https://img.shields.io/github/license/promptromp/pytest-impacted)](https://github.com/promptromp/pytest-impacted/blob/main/LICENSE)
[![PyPI - Version](https://img.shields.io/pypi/v/pytest-impacted)](https://pypi.org/project/pytest-impacted/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pytest-impacted)](https://pypi.org/project/pytest-impacted/)

----

A pytest plugin that selectively runs tests impacted by codechanges via git introspection, ASL parsing, and dependency graph analysis.

* Configurable to meet your demands for both local and CI-driven invocations. :shipit:
* Built using a modern, best-of-breed Python stack, using [astroid](https://pylint.pycqa.org/projects/astroid/en/latest/) for
  Python code AST, [NetworkX](https://networkx.org/documentation/stable/index.html) for dependency graph analysis, and [GitPython](https://github.com/gitpython-developers/GitPython) for interacting with git repositories. :rocket:

## Installation

You can install "pytest-impacted" via `pip`from `PyPI`:

    $ pip install pytest-impacted

## Usage

Use as a pytest plugin. Examples for invocation:

    $ pytest --impacted --impacted-git-mode=unstaged

This will run all unit-tests impacted by changes to files which have unstaged
modifications in the current active git repository.

    $ pytest --impacted --impacted-git-mode=branch --impacted-base-branch=main

this will run all unit-tests impacted by changes to files which have been
modified via any existing commits to the current active branch, as compared to
the base branch passed in the `--impacted-base-branch` parameter.

## Testing

Invoke unit-tests with:

    uv run python -m pytest

Linting, formatting, static type checks etc. are all managed via [pre-commit](https://pre-commit.com/) hooks. These will run automatically on every commit. You can invoke these manually on all files with:

    pre-commit run --all-files
