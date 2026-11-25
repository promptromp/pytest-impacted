"""Literal dynamic imports, read syntactically without evaluating a loader."""

from collections import defaultdict

from astroid import nodes


def literal_imports(tree: nodes.Module, package: str) -> set[str]:
    """Collect candidate modules from importlib.import_module and builtin __import__.

    Bindings are a union across scopes: ambiguous/shadowed aliases can add edges,
    never remove a genuine loader's edge. Computed names are not evaluated.
    """
    bindings: dict[str, set[str]] = defaultdict(set)
    bindings["__import__"].add("builtins.__import__")
    for node in tree.nodes_of_class((nodes.Import, nodes.ImportFrom)):
        if isinstance(node, nodes.Import):
            for name, alias in node.names:
                bindings[alias or name.split(".")[0]].add(name if alias else name.split(".")[0])
        elif not node.level:
            for name, alias in node.names:
                bindings[alias or name].add(f"{node.modname}.{name}")
    found: set[str] = set()
    for call in tree.nodes_of_class(nodes.Call):
        functions = _names(call.func, bindings)
        if not functions.intersection({"importlib.import_module", "builtins.__import__"}):
            continue
        name = _string(_argument(call, 0, "name"))
        if name is None:
            continue
        if "importlib.import_module" in functions:
            absolute = name
            if name.startswith("."):
                arg = _argument(call, 1, "package")
                parent = package if isinstance(arg, nodes.Name) and arg.name == "__package__" else _string(arg)
                absolute = _relative(name, parent)
            if absolute:
                found.add(absolute)
        if "builtins.__import__" in functions:
            level = _argument(call, 4, "level")
            if level is not None and not (
                isinstance(level, nodes.Const) and isinstance(level.value, int) and level.value == 0
            ):
                continue
            if name and not name.startswith("."):
                found.add(name)
                fromlist = _argument(call, 3, "fromlist")
                if isinstance(fromlist, (nodes.List, nodes.Tuple)):
                    found.update(
                        f"{name}.{value}" for item in fromlist.elts if (value := _string(item)) and value != "*"
                    )
    return found


def _names(node: nodes.NodeNG, bindings: dict[str, set[str]]) -> set[str]:
    if isinstance(node, nodes.Name):
        return bindings.get(node.name, set())
    if isinstance(node, nodes.Attribute):
        return {f"{name}.{node.attrname}" for name in _names(node.expr, bindings)}
    return set()


def _argument(call: nodes.Call, position: int, name: str) -> nodes.NodeNG | None:
    for keyword in call.keywords or ():
        if keyword.arg == name:
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _string(node: nodes.NodeNG | None) -> str | None:
    return node.value if isinstance(node, nodes.Const) and isinstance(node.value, str) else None


def _relative(name: str, package: str | None) -> str:
    if not package:
        return ""
    level = len(name) - len(name.lstrip("."))
    parts = package.split(".")
    if level > len(parts):
        return ""
    return ".".join([*parts[: len(parts) - level + 1], *([name[level:]] if name[level:] else [])])
