"""Python code parsing (AST) utilities."""

import inspect
import logging

import astroid


def parse_module_imports(module):
    """Parse the module to find all import statements."""
    # Get the source code of the module
    source = inspect.getsource(module)
    if not source:
        return []

    # Parse the source code into an AST
    tree = astroid.parse(source)

    # Find all import statements in the AST
    imports = []
    for node in tree.body:
        if isinstance(node, astroid.Import):
            for name in node.names:
                logging.debug("parse_module_imports: adding Import: %s", name[0])
                imports.append(name[0])
        elif isinstance(node, astroid.ImportFrom):
            logging.debug("parse_module_imports: adding ImportFrom: %s", node.modname)
            imports.append(node.modname)

    return imports


def is_test_module(module_name):
    """Check if a module is a test module using a battery of heuristics.

    Currently this simply looks at file / modul name conventions, but
    could be extended to look at the contents of the module and use
    static analysis (AST) to determine if the module is a test module.

    """
    if module_name.startswith("test_"):
        return True

    elif module_name.endswith("_test"):
        return True

    elif ".tests." in module_name:
        return True

    return False
