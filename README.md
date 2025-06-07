# pytest-impacted

[![CI](https://github.com/promptromp/pytest-impacted/actions/workflows/ci.yml/badge.svg)](https://github.com/promptromp/pytest-impacted/actions/workflows/ci.yml)
[![GitHub License](https://img.shields.io/github/license/promptromp/pytest-impacted)](https://github.com/promptromp/pytest-impacted/blob/main/LICENSE)
[![PyPI - Version](https://img.shields.io/pypi/v/pytest-impacted)](https://pypi.org/project/pytest-impacted/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pytest-impacted)](https://pypi.org/project/pytest-impacted/)

----

A pytest plugin that selectively runs tests impacted by codechanges via git introspection, ASL parsing, and dependency graph analysis.

* Configurable to meet your demands for both local and CI-driven invocations. :dromedary_camel:
* Built using a modern, best-of-breed Python stack, using [astroid](https://pylint.pycqa.org/projects/astroid/en/latest/) for
  Python code AST, [NetworkX](https://networkx.org/documentation/stable/index.html) for dependency graph analysis, and [GitPython](https://github.com/gitpython-developers/GitPython) for interacting with git repositories. :rocket:
* Modular codebase with high unit-test coverage to ensure solid, reliable performance in CI and production environments. :muscle:

> [!CAUTION]
> This project is still currently in alpha development phase. Do not use it in mission critical applications without close supervision of its output and performance. Please report bugs via the Issues tab.

## Overview

Sometimes code repositories can become encumbered with a large codebase and a large unit-test codebase to match. In those cases often CI builds become slow and painful due to the need to run all the unit-tests on every CI build. Existing solutions include parallelizing or splitting the tests (e.g. via [pytest-split](https://pypi.org/project/pytest-split/) or [pytest-xdist](https://github.com/pytest-dev/pytest-xdist)), however these often run into trouble too when tests rely on resources such as databases that cannot be easily "shared" between concurrent runs. Moreover, when using solutions such as pytest-split or pytest-xdist, even with multiple database instances, it is often the case that each thread / split of tests takes a long time to run, on top of the overhead introduced by spawning N many databases.

An alternative solution is to try and selectively mark tests that have been affected by recent changes, e.g. as relative to a base branch when we are on a feature branch. This plugin takes this approach. It uses a combination of static analysis (parsing the AST for python modules in a package and building a dependency graph of imports) and git introspection to flag tests that should be re-run.

The philosophy is to err on the side of caution; we currently do not attempt to isolate changes on a line-by-line basis, but rather favor 'false positives' by simply following the chain of dependencies from any file that was modified in any way according to the git history, all the way to any unit-test file that imports it directly or transitively.

### Why another such plugin?

We originally looked for such a plugin to already exist. For completeness we mention these here and our impression:


* [pytest-testmon](https://testmon.org/) is probably the most popular alternative. This may be a fine choice - they are still actively maintained at the time of writing, and go beyond what `pytest-impacted` does by isolating more granular changes to mark affected tests, based on logic used by the [coverage](https://github.com/nedbat/coveragepy) package. In our attempts to use it we ran into various errors we could not easily figure out when using it in conjunction with other plugins such as `coverage` and `pytest-split`, but YMMV - definitely give it a try if you want to look at other options.
* [pytest-affected](https://pypi.org/project/pytest-affected/0.1.6/) - no homepage / repo, seems unmaintained.
* [pytest-picked](https://github.com/anapaulagomes/pytest-picked) - seems more recently maintained, however only seems to run tests from files that were directly modified rather than perform any static analysis to transitively identify tests based on updated source.
## Installation

You can install "pytest-impacted" via `pip`from `PyPI`:

    $ pip install pytest-impacted

## Usage

### Local unstaged changes

Use as a pytest plugin. Examples for invocation:

    $ pytest --impacted --impacted-git-mode=unstaged --impacted-module=<my_root_module_name>

This will run all unit-tests impacted by changes to files which have unstaged
modifications in the current active git repository.


### Changes committed to current git branch

    $ pytest --impacted --impacted-git-mode=branch --impacted-base-branch=main --impacted-module=<my_root_module_name>

this will run all unit-tests impacted by changes to files which have been
modified via any existing commits to the current active branch, as compared to
the base branch passed in the `--impacted-base-branch` parameter.

As an added bonus, note that you can pass git expressions to the base branch parameter as would be permissible when using git diff - e.g.:

 $ pytest --impacted --impacted-git-mode=branch --impacted-base-branch="HEAD~4" --impacted-module=<my_root_module_name>

This can be useful in some scenarios as well.

### External tests directory

As another common use case, In some projects the tests directory exists outside of the namespace package. In those cases you can use the `--impacted-tests-dir` option to make sure those test files are included in the dependency tree and correctly considered for impact analysis:

    $ pytest --impacted --impacted-git-mode=unstaged --impacted-module=<my_root_module_name> --impacted-tests-dir=tests/

### CI Integration

When using this plugin in CI, it is sometimes desirable to generate the list of impacted test files in one stage where we have access to the git CLI (and perhaps required credentials),
and then invoke running these in a separate step later in the CI pipeline. This can be achieved with the `impacted-tests` CLI included with the plugin, which supports the same arguments
as the plugin itself:

    $ impacted-tests --module=<my_root_module_name> --git-mode=branch --base-branch=main > impacted_tests.txt

In some later step of your CI can then run:

    $ pytest @impacted_tests.txt

## Testing

Invoke unit-tests with:

    uv run python -m pytest

Linting, formatting, static type checks etc. are all managed via [pre-commit](https://pre-commit.com/) hooks. These will run automatically on every commit. You can invoke these manually on all files with:

    pre-commit run --all-files
