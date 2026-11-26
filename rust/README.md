# pytest-impacted-rs

The optional Rust extension of [pytest-impacted](https://github.com/promptromp/pytest-impacted): it parses a
project's import statements with [ruff's parser](https://github.com/astral-sh/ruff), in parallel with
[rayon](https://github.com/rayon-rs/rayon), about 35-50x faster than pytest-impacted's pure-Python parser on a
large codebase.

Install it through pytest-impacted's `fast` extra, which needs no Rust toolchain (the wheels are pre-built):

```bash
pip install "pytest-impacted[fast]"
```

pytest-impacted finds it at runtime; nothing is configured. Both parsers find the same imports, so installing it
changes how fast tests are selected, never which. Upgrade the two together: the report header and the
`impacted-tests` CLI say when the installed extension is too old for the options in use. See the
[documentation](https://promptromp.github.io/pytest-impacted/usage/#performance-rust-acceleration).
