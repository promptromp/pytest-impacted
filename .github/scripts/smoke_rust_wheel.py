"""Import the installed pytest-impacted-rs wheel and parse one file with it, as pytest-impacted does.

Run by publish-rust-wheels.yml on each wheel its runner can load, before any wheel is published: a wheel that
builds but cannot load (a missing symbol, a wrong ABI tag, a stripped file dyld refuses) would otherwise reach PyPI,
and pytest-impacted would fall back to the slower Python parser without a word.
"""

import pathlib
import tempfile

import pytest_impacted_rs


with tempfile.TemporaryDirectory() as directory:
    probe = pathlib.Path(directory, "probe.py")
    probe.write_text("import os\nfrom . import sibling\n")
    found = pytest_impacted_rs.parse_imports_each([(str(probe), "pkg.probe", False)], False)

assert found == [(["os", "pkg", "pkg.sibling"], [])], found
print("pytest-impacted-rs loads and parses:", found)
