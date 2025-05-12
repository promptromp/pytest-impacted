# pytest-impacted

[![PyPI version](https://img.shields.io/pypi/v/pytest-impacted.svg)](https://pypi.org/project/pytest-impacted)
[![Python versions](https://img.shields.io/pypi/pyversions/pytest-impacted.svg)](https://pypi.org/project/pytest-impacted)
[![See Build Status on GitHub Actions](https://github.com/promptromp/pytest-impacted/actions/workflows/main.yml/badge.svg)](https://github.com/promptromp/pytest-impacted/actions/workflows/main.yml)

A pytest plugin that selectively runs tests impacted by codechanges via git introspection, ASL parsing, and dependency graph analysis.

----


## Features

* Configurable to meet your demands for both local and CI-driven invocations.
* Built using a modern, best-of-breed Python stack, using [astroid](https://pylint.pycqa.org/projects/astroid/en/latest/) for
  Python code AST, [NetworkX](https://networkx.org/documentation/stable/index.html) for dependency graph analysis, and [GitPython](https://github.com/gitpython-developers/GitPython) for interacting with git repositories.


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

## Contributing

Contributions are very welcome. Tests can be run with `pytest`:

    uv run pytest tests/
