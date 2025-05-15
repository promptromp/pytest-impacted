"""Python code parsing (AST) utilities."""

import inspect
import logging
import os
import astroid


def should_silently_ignore_oserror(file_path: str) -> bool:
    """Check if the file should be silently ignored."""
    # Nb. __init__ files often have zero bytes in which case inspect.getsource()
    # raises an OSError. we ignore those cases as well as any other file thats explicitly
    # zero bytes in size.
    return any((os.stat(file_path).st_size == 0,))


def parse_module_imports(module):
    """Parse the module to find all import statements."""
    # Get the source code of the module
    source = None
    try:
        source = inspect.getsource(module)
    except OSError:
        if should_silently_ignore_oserror(module.__file__):
            return []
        else:
            logging.error(
                "Exception raised while trying to get source code for module %s", module
            )
            raise

    if not source:
        return []

    # Parse the source code into an AST
    tree = astroid.parse(source)

    # Find all import statements in the AST
    imports = []
    for node in tree.body:
        if isinstance(node, astroid.Import):
            for name in node.names:
                imports.append(name[0])
        elif isinstance(node, astroid.ImportFrom):
            imports.append(node.modname)

    return imports


def is_test_module(module_name):
    """Check if a module is a test module using a battery of heuristics.

    Currently this simply looks at file / modul name conventions, but
    could be extended to look at the contents of the module and use
    static analysis (AST) to determine if the module is a test module.

    """
    module_name_chunks = module_name.split(".")

    match module_name_chunks:
        case _ if module_name_chunks[-1].startswith("test_"):
            is_test = True

        case _ if module_name_chunks[-1].endswith("_test"):
            is_test = True

        case _ if "test" in module_name_chunks:
            is_test = True

        case _ if "tests" in module_name_chunks:
            is_test = True

        case _:
            is_test = False

    logging.debug("Module %s is a test module: %s", module_name, is_test)

    return is_test
