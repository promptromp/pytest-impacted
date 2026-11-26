"""Import the installed pytest-impacted-rs wheel and call each function pytest-impacted uses, on one file.

Run by publish-rust-wheels.yml on each wheel its runner can load, before any wheel is published: a wheel that
builds but cannot load (a missing symbol, a wrong ABI tag, a stripped file dyld refuses), or lacks a function,
would otherwise reach PyPI, and pytest-impacted would fall back to the slower Python parser without a word
(``pytest_impacted/_rust.py`` imports each function behind ``ImportError``).
"""

import pathlib
import tempfile

import pytest_impacted_rs


with tempfile.TemporaryDirectory() as directory:
    probe = pathlib.Path(directory, "probe.py")
    probe.write_text("import os\nfrom . import sibling\nimportlib.import_module('pkg.lazy')\n")
    modules = [(str(probe), "pkg.probe", False)]
    found = {
        "parse_imports_each": pytest_impacted_rs.parse_imports_each(modules, False),
        "parse_all_imports": pytest_impacted_rs.parse_all_imports(modules),
        "parse_all_imports_split": pytest_impacted_rs.parse_all_imports_split(modules),
    }
    # The by-name table is pytest-impacted's (dynamic_imports.TABLE); an empty one still proves the call works.
    found["parse_all_dynamic_imports"] = pytest_impacted_rs.parse_all_dynamic_imports(modules, [])

imports = ["os", "pkg", "pkg.sibling"]
assert found["parse_imports_each"] == [(imports, [])], found
assert sorted(found["parse_all_imports"]["pkg.probe"]) == imports, found
assert [sorted(found["parse_all_imports_split"]["pkg.probe"][0]), found["parse_all_imports_split"]["pkg.probe"][1]] == [
    imports,
    [],
], found
assert len(found["parse_all_dynamic_imports"]) == 1, found
print("pytest-impacted-rs loads, and each function parses:", found)
