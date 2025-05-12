# pytest-affected

[![PyPI version](https://img.shields.io/pypi/v/pytest-affected.svg)](https://pypi.org/project/pytest-affected)
[![Python versions](https://img.shields.io/pypi/pyversions/pytest-affected.svg)](https://pypi.org/project/pytest-affected)
[![See Build Status on GitHub Actions](https://github.com/promptromp/pytest-affected/actions/workflows/main.yml/badge.svg)](https://github.com/promptromp/pytest-affected/actions/workflows/main.yml)

A pytest plugin that selectively runs tests affected by codechanges via git introspection, ASL parsing, and dependency graph analysis.

----

This `pytest`plugin was generated with `Cookiecutter`_ along with `@hackebrot`_'s `cookiecutter-pytest-plugin`_ template.


## Features

* Configurable to meet your demands for both local and CI-driven invocations.
* Built using a modern, best-of-breed Python stack, using [astroid](https://pylint.pycqa.org/projects/astroid/en/latest/) for
  Python code AST, [NetworkX](https://networkx.org/documentation/stable/index.html) for dependency graph analysis, and [GitPython](https://github.com/gitpython-developers/GitPython) for interacting with git repositories.


## Requirements

* All requirements are purely Pythonic and are installed as part of the
  Installation method described below.

## Installation

You can install "pytest-affected" via `pip`from `PyPI`:

    $ pip install pytest-affected

## Usage

Use as a pytest plugin. Examples for invocation:

    $ pytest --affected --affected-git-mode=unstaged

This will run all unit-tests affected by changes to files which have unstaged
modifications in the current active git repository.

    $ pytest --affected --affected-git-mode=branch --affected-base-branch=main

this will run all unit-tests affected by changes to files which have been
modified via any existing commits to the current active branch, as compared to
the base branch passed in the `--affected-base-branch` parameter.

## Contributing

Contributions are very welcome. Tests can be run with `tox`_, please ensure
the coverage at least stays the same before you submit a pull request.

## License

Distributed under the terms of the `MIT`_ license, "pytest-affected" is free and open source software
