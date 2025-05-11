===============
pytest-affected
===============

.. image:: https://img.shields.io/pypi/v/pytest-affected.svg
    :target: https://pypi.org/project/pytest-affected
    :alt: PyPI version

.. image:: https://img.shields.io/pypi/pyversions/pytest-affected.svg
    :target: https://pypi.org/project/pytest-affected
    :alt: Python versions

.. image:: https://github.com/adamhadani/pytest-affected/actions/workflows/main.yml/badge.svg
    :target: https://github.com/adamhadani/pytest-affected/actions/workflows/main.yml
    :alt: See Build Status on GitHub Actions

A pytest plugin that selectively runs tests affected by codechanges via git introspection, ASL parsing, and dependency graph analysis.

----

This `pytest`_ plugin was generated with `Cookiecutter`_ along with `@hackebrot`_'s `cookiecutter-pytest-plugin`_ template.


Features
--------

* Configurable to meet your demands for both local and CI-driven invocations.
* Built using a modern, best-of-breed Python stack, using [astroid](https://pylint.pycqa.org/projects/astroid/en/latest/) for
  Python code ASL, [NetworkX](https://networkx.org/documentation/stable/index.html) for dependency graph analysis, and [GitPython](https://github.com/gitpython-developers/GitPython) for interacting with git repositories.


Requirements
------------

* All requirements are purely Pythonic and are installed as part of the
  Installation method described below.


Installation
------------

You can install "pytest-affected" via `pip`_ from `PyPI`_::

    $ pip install pytest-affected


Usage
-----

Use as a pytest plugin:

  $ pytest -p affected my_package/

Contributing
------------
Contributions are very welcome. Tests can be run with `tox`_, please ensure
the coverage at least stays the same before you submit a pull request.

License
-------

Distributed under the terms of the `MIT`_ license, "pytest-affected" is free and open source software
