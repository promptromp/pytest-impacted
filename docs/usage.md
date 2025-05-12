# Usage Guide: pytest-impacted

`pytest-impacted` is a pytest plugin that selectively runs only the tests impacted by code changes, using git introspection, AST parsing, and dependency graph analysis.

## Basic Usage

Activate the plugin by passing the --impacted flag to pytest. This will run only the tests affected by your recent code changes.

**Example**: Run tests impacted by unstaged changes

```shell
pytest --impacted --impacted-git-mode=unstaged
```

This command will run all unit tests impacted by files with unstaged modifications in your current git repository.

**Example**: Run tests impacted by all cumulative changes to current branch relative to given base branch

```shell
pytest --impacted --impacted-git-mode=branch --impacted-base-branch=main
```

This command will run all unit tests impacted by files changed in commits on your current branch, compared to the main branch.

## Typical Workflows

* **Local Development:** Quickly run only the tests affected by your current changes before committing.

* **Continuous Integration:** Speed up CI pipelines by running only the tests impacted by changes in a pull request.
