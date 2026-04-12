---
description: Upgrade all dependencies, pre-commit hooks, and GitHub Actions to latest versions, then verify everything still works.
---

Perform a comprehensive across-the-board upgrade of the current project and verify nothing broke. Execute the steps below in order. Use parallel tool calls where independent.

## 1. Upgrade Python dependencies

Detect the dependency manager and upgrade the lockfile to latest compatible versions:

- If `uv.lock` exists → `uv lock --upgrade`, then `uv sync --all-extras --dev` (fall back to `uv sync --dev` if `--all-extras` fails)
- Else if `poetry.lock` exists → `poetry update`
- Else if `Pipfile.lock` exists → `pipenv update`
- Else if `requirements*.txt` with `pip-tools` is in use (look for `requirements.in`) → `pip-compile --upgrade` for each `.in` file
- Else report that no recognized Python lockfile was found and skip this step

Report which packages were upgraded (name + old → new version).

## 2. Upgrade pre-commit hooks

If `.pre-commit-config.yaml` exists:

- Run `pre-commit autoupdate` to bump each hook repo to its latest tag
- Report which hook repos were bumped (repo + old → new rev)
- Otherwise skip this step

## 3. Upgrade GitHub Actions

If `.github/workflows/` exists:

- Collect every distinct `uses: owner/repo@vN` reference across all workflow files (ignore local `./` actions)
- For each, query the latest release tag via `gh api repos/{owner}/{repo}/releases/latest --jq .tag_name` — parallelize these calls in a single message
- When the latest **major** version is ahead of what's pinned, update the pin to the new major (e.g. `@v7` → `@v8`). Do **not** downgrade. Do **not** change pins that are already at or ahead of the latest major. Preserve existing pin style (tag vs. full SHA — if SHAs are used, warn the user and skip rather than blindly rewriting)
- Apply edits with Edit tool, using `replace_all` when a version appears multiple times
- Report which actions were bumped (action + old → new version), and list any that were already current

Pay attention to unusual pins like `pypa/gh-action-pypi-publish@release/v1` — leave those alone since they track a rolling branch.

## 4. Verify nothing broke

Run the project's standard checks. Pick whichever apply:

- Python: `uv run pytest` (or `pytest` / `poetry run pytest`) — prefer excluding slow markers if the project uses them (e.g. `-m 'not slow'`)
- Pre-commit: `pre-commit run --all-files`
- Type check / lint: whatever the project's CLAUDE.md or README documents (e.g. `uv run mypy <pkg>`, `ruff check`)

If any step fails, **stop and report the failure** with enough detail for the user to decide whether to pin back, investigate, or accept. Don't attempt risky rollbacks unprompted.

## 5. Summarize

End with a compact report:

- Dependencies upgraded (count + notable bumps)
- Pre-commit hooks upgraded
- GitHub Actions upgraded
- Verification results (tests, pre-commit, lint/type)
- Anything skipped or warning the user should know about

Do **not** commit the changes unless the user explicitly asks for a commit afterwards.
