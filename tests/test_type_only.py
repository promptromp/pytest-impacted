"""Which imports under ``if TYPE_CHECKING:`` are type-only (``pytest_impacted.type_only``), in both backends.

The rule: such an import is left out only when the module uses every name it binds, and only in annotations
or another such block. No code here names a lazy-loading idiom: each is kept because of how it uses (or
does not use) the names, which is the point.
"""

import pytest

from pytest_impacted import parsing
from pytest_impacted.type_only import mentions

from .test_parsing import PARITY_SOURCE


GUARD = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg.lazy import Thing\n"


def parse(tmp_path, source, *, ignore=True, module="my_package.sub.mod", is_package=False):
    path = tmp_path / ("__init__.py" if is_package else "mod.py")
    path.write_text(source)
    return parsing.parse_file_imports(str(path), module, is_package=is_package, ignore_type_checking_imports=ignore)


def both_backends(tmp_path, source, module="my_package.sub.mod"):
    """Python's runtime and type-only candidates, and Rust's, for the same file."""
    rust = pytest.importorskip("pytest_impacted_rs")
    path = tmp_path / "mod.py"
    path.write_text(source)
    python = parsing.split_file_imports(str(path), module)
    return python, tuple(rust.parse_all_imports_split([(str(path), module, False)])[module])


# Every name is used in annotations alone, so which `if` bodies count is all that decides.
BLOCKS = """\
import typing
import typing as t
import typing_extensions as te
from typing import TYPE_CHECKING
from typing import TYPE_CHECKING as TC
if TYPE_CHECKING:
    from pkg.by_name import ByName
if typing.TYPE_CHECKING:
    from pkg.by_module import ByModule
if t.TYPE_CHECKING:
    from pkg.by_alias import ByAlias
if te.TYPE_CHECKING:
    from pkg.by_other_module import ByOtherModule
if (TYPE_CHECKING):
    from pkg.parenthesised import Parenthesised
if TYPE_CHECKING:
    from pkg.before_else import BeforeElse
else:
    from pkg.in_else import InElse
if FLAG:
    from pkg.in_if import InIf
elif TYPE_CHECKING:
    from pkg.in_elif import InElif
elif OTHER:
    from pkg.in_other_elif import InOtherElif
else:
    from pkg.in_final_else import InFinalElse
if not TYPE_CHECKING:
    from pkg.negated import Negated
if TYPE_CHECKING or FLAG:
    from pkg.combined import Combined
if TC:
    from pkg.aliased_name import AliasedName
if settings.TYPE_CHECKING:
    from pkg.another_object import AnotherObject
if TYPE_CHECKING:
    if FLAG:
        from pkg.nested import Nested
def f():
    if TYPE_CHECKING:
        from pkg.in_function import InFunction
class C:
    if TYPE_CHECKING:
        from . import relative_thing


def annotated(
    a: ByName, b: ByModule, c: ByAlias, d: ByOtherModule, e: Parenthesised, g: BeforeElse, h: InElse,
    i: InIf, j: InElif, k: InOtherElif, m: InFinalElse, n: Negated, o: Combined, p: AliasedName,
    q: AnotherObject, r: Nested, s: "InFunction", u: "relative_thing",
) -> None: ...
"""
TYPE_ONLY_BLOCKS = {
    *(f"pkg.{module}" for module in ("by_name", "by_module", "by_alias", "by_other_module", "parenthesised")),
    *(f"pkg.{module}" for module in ("before_else", "in_elif", "nested", "in_function")),
    *("pkg.by_name.ByName", "pkg.by_module.ByModule", "pkg.by_alias.ByAlias"),
    *("pkg.by_other_module.ByOtherModule", "pkg.parenthesised.Parenthesised", "pkg.before_else.BeforeElse"),
    *("pkg.in_elif.InElif", "pkg.nested.Nested", "pkg.in_function.InFunction"),
    "my_package.sub",  # what ``from . import relative_thing`` imports from
    "my_package.sub.relative_thing",
}


def test_type_only_imports_are_kept_by_default(tmp_path):
    """Off by default: a type-only import is an edge, as it always was (false positives over missed tests)."""
    assert set(parse(tmp_path, BLOCKS, ignore=False)) >= TYPE_ONLY_BLOCKS


def test_only_the_body_of_an_if_type_checking_is_type_only(tmp_path):
    """The name ``TYPE_CHECKING``, or a module's (``typing.``, through an alias too), alone as the test: an
    ``else`` or later ``elif`` runs, as do ``not TYPE_CHECKING``, a combined test, an alias of the name, and
    another object's ``TYPE_CHECKING`` (a project's flag may be true)."""
    everything = set(parse(tmp_path, BLOCKS, ignore=False))

    assert everything - set(parse(tmp_path, BLOCKS)) == TYPE_ONLY_BLOCKS


ANNOTATIONS_ONLY = [
    pytest.param("def f(x: Thing) -> None: ...\n", id="parameter"),
    pytest.param("def f(x) -> 'Thing': ...\n", id="quoted_return"),
    pytest.param("async def f() -> Thing: ...\n", id="async_return"),
    pytest.param("def f(x: list['Thing']): ...\n", id="quoted_inside"),
    pytest.param("def f(x: 'Thing.Part'): ...\n", id="attribute_in_string"),
    pytest.param("def f(a: Thing, /): ...\n", id="positional_only"),
    pytest.param("def f(*args: Thing): ...\n", id="var_positional"),
    pytest.param("def f(*, key: Thing): ...\n", id="keyword_only"),
    pytest.param("def f(**kwargs: Thing): ...\n", id="var_keyword"),
    pytest.param("value: Thing = make()\n", id="variable"),
    pytest.param("class C:\n    attr: 'Thing | None' = None\n", id="class_variable"),
    pytest.param("def f():\n    local: Thing = make()\n", id="local_variable"),
    pytest.param("if TYPE_CHECKING:\n    Alias = list[Thing]\n", id="another_type_checking_block"),
    pytest.param("def f(x: Thing): ...\nThing = make()\n", id="rebound_at_runtime"),
    pytest.param("def f(x: Thing):\n    '''Returns a Thing.'''\n    return x\n", id="docstring"),
    pytest.param("'''A Thing.'''\ndef f(x: Thing): ...\n", id="module_docstring"),
    pytest.param("def f(x: Thing):\n    'Thing'\n", id="bare_string"),
]


@pytest.mark.parametrize("use", ANNOTATIONS_ONLY)
def test_an_import_whose_names_only_annotations_use_is_type_only(tmp_path, use):
    """The issue's case: ``def f(x: "X")`` needs ``X`` only while type checking. A bare string, such as a
    docstring naming the class, is read by no code."""
    assert "pkg.lazy" not in parse(tmp_path, GUARD + use)


RUNTIME_USES = [
    pytest.param("isinstance(value, Thing)\n", id="name"),
    pytest.param("def f(x: Thing):\n    return Thing()\n", id="annotation_and_call"),
    pytest.param("value: Thing = Thing()\n", id="annotated_value"),
    pytest.param("def f(x=Thing): ...\n", id="default"),
    pytest.param("@Thing\ndef f(): ...\n", id="decorator"),
    pytest.param("class C(Thing): ...\n", id="base"),
    # astroid keeps a metaclass out of its child walk; ruff, and the ``ast`` module, among the keywords.
    pytest.param("def f(x: Thing): ...\nclass C(metaclass=Thing): ...\n", id="metaclass"),
    pytest.param("def f(x: Thing): ...\nclass C(metaclass=make(lambda: 'Thing')): ...\n", id="metaclass_string"),
    pytest.param("label = f'{Thing}'\n", id="f_string_field"),
    pytest.param("match value:\n    case Thing():\n        pass\n", id="class_pattern"),
    pytest.param("if FLAG:\n    pass\nelse:\n    Thing.setup()\n", id="else_of_another_if"),
    # A string the code reads may name it for something to resolve at runtime.
    pytest.param("__all__ = ['Thing']\n", id="all"),
    pytest.param("_lazy = {'Thing': '.lazy'}\n", id="loader_table"),
    pytest.param("found = getattr(module, 'Thing')\n", id="getattr"),
    pytest.param("value = cast('Thing', raw)\n", id="cast"),
    pytest.param("def f(x: Thing):\n    'Thing' in registry\n", id="string_in_expression"),
    pytest.param("match value:\n    case 'Thing':\n        pass\n", id="string_pattern"),
]


@pytest.mark.parametrize("use", RUNTIME_USES)
def test_an_import_whose_names_code_uses_is_kept(tmp_path, use):
    """Used at runtime, the name must come from somewhere then, which static analysis cannot rule out."""
    assert "pkg.lazy" in parse(tmp_path, GUARD + use)


# Every lazy-loading idiom two review rounds found, as it appears: none names ``Thing`` outside the guard.
LAZY_IDIOMS = [
    pytest.param("def __getattr__(name):\n    return 1\n", id="def"),
    pytest.param("async def __getattr__(name):\n    return 1\n", id="async_def"),
    pytest.param("__getattr__ = make_getattr(__name__)\n", id="assigned"),
    pytest.param("__getattr__, __dir__, __all__ = lazy.attach(__name__)\n", id="unpacked"),
    pytest.param("[*__getattr__] = loaders\n", id="starred"),
    pytest.param("__getattr__: object = make_getattr(__name__)\n", id="annotated"),
    pytest.param("from pkg._lazy import __getattr__\n", id="imported"),
    pytest.param("from pkg._lazy import loader as __getattr__\n", id="imported_as"),
    pytest.param("import x as __getattr__\n", id="import_as"),
    pytest.param("import sys\nsys.modules[__name__] = LazyModule(__name__)\n", id="sys_modules"),
    pytest.param("import sys\nsys.modules[__name__].__class__ = LazyModule\n", id="sys_modules_class"),
    pytest.param("from sys import modules\nmodules[__name__] = LazyModule(__name__)\n", id="from_sys_modules"),
    pytest.param("import sys as _sys\n_sys.modules[__name__] = LazyModule(__name__)\n", id="sys_alias"),
    pytest.param("import sys\n_module = sys.modules[__name__]\n_module.__class__ = LazyModule\n", id="via_name"),
    pytest.param("import sys\nsys.modules.update({__name__: LazyModule(__name__)})\n", id="update"),
    pytest.param("import sys\nsetattr(sys.modules[__name__], '__class__', LazyModule)\n", id="setattr_class"),
    pytest.param("globals()['__getattr__'] = make_getattr(__name__)\n", id="globals_item"),
    pytest.param("globals().update(__getattr__=make_getattr(__name__))\n", id="keyword"),
    pytest.param("def install():\n    global __getattr__\n    __getattr__ = make_getattr(__name__)\n", id="global"),
    pytest.param("this_module.__getattr__ = make_getattr(__name__)\n", id="attribute_assigned"),
    pytest.param("from pkg._lazy import *\n", id="star_import"),
    pytest.param("if (__getattr__ := make_lazy(__name__)):\n    pass\n", id="walrus"),
    pytest.param("for __getattr__ in loaders:\n    pass\n", id="for_target"),
    pytest.param("with make() as __getattr__:\n    pass\n", id="with_target"),
    pytest.param("try:\n    pass\nexcept ImportError as __getattr__:\n    pass\n", id="except_name"),
    pytest.param("'''__getattr__'''\n", id="docstring"),
]


@pytest.mark.parametrize("idiom", LAZY_IDIOMS)
def test_a_name_the_module_never_uses_keeps_its_import(tmp_path, idiom):
    """Declared under ``TYPE_CHECKING`` and used nowhere, the name is there for others to import from this
    module (a lazy ``__init__``): the module provides it at runtime some other way, so ``from pkg import
    Thing`` loads ``pkg.lazy`` after all — however the module does it."""
    assert "pkg.lazy" in parse(tmp_path, GUARD + idiom, module="pkg", is_package=True)


MODULE_IMPORTS = [
    pytest.param(
        "try:\n    import typing as tx\nexcept ImportError:\n    tx = None\n"
        "if tx.TYPE_CHECKING:\n    from pkg.lazy import Thing\ndef f(x: Thing): ...\n",
        True,
        id="module_imported_in_a_try",
    ),
    pytest.param(
        "try:\n    import typing_extensions as tx\nexcept ImportError:\n    import typing as tx\n"
        "if tx.TYPE_CHECKING:\n    from pkg.lazy import Thing\ndef f(x: Thing): ...\n",
        True,
        id="module_imported_in_a_block",
    ),
    pytest.param(
        "def setup():\n    import typing as tx\n"
        "if tx.TYPE_CHECKING:\n    from pkg.lazy import Thing\ndef f(x: Thing): ...\n",
        False,
        id="module_imported_in_a_function",
    ),
]


@pytest.mark.parametrize(("source", "type_only"), MODULE_IMPORTS)
def test_a_module_s_type_checking_is_one_imported_at_module_level(tmp_path, source, type_only):
    """``tx`` names a module everywhere only if a module-level ``import`` binds it."""
    assert ("pkg.lazy" not in parse(tmp_path, source)) is type_only


def test_a_lazy_module_annotating_with_a_name_it_never_mentions_at_runtime_drops_it(tmp_path):
    """The rule's known limit, pinned: a lazily loading module that also annotates with the name, and never
    names it where code runs (no ``__all__``, no loader table), reads as type-only."""
    source = GUARD + "def __getattr__(name):\n    return load(name)\ndef make() -> Thing: ...\n"

    assert "pkg.lazy" not in parse(tmp_path, source, module="pkg", is_package=True)


def test_a_lazy_module_annotating_with_a_name_it_exports_keeps_it(tmp_path):
    """The usual lazy ``__init__`` lists what it exports, or maps it to its module: a string the code reads."""
    source = GUARD + "__all__ = ['Thing']\ndef __getattr__(name):\n    return load(name)\ndef make() -> Thing: ...\n"

    assert "pkg.lazy" in parse(tmp_path, source, module="pkg", is_package=True)


BINDINGS = [
    pytest.param("from pkg.lazy import Thing, Other", "def f(a: Thing, b: Other): ...", True, id="all_annotated"),
    pytest.param("from pkg.lazy import Thing, Other", "def f(a: Thing): return Other", False, id="one_at_runtime"),
    pytest.param("from pkg.lazy import Thing, Other", "def f(a: Thing): ...", False, id="one_unused"),
    pytest.param("from pkg.lazy import Thing as T", "def f(a: T): ...", True, id="alias"),
    pytest.param("from pkg.lazy import *", "def f(a: Thing): ...", False, id="star_names_unknown"),
    # No name a string can show used: ``*`` is no name it binds.
    pytest.param("from pkg.lazy import *", "def f(mode: Literal['*']): ...", False, id="star_in_a_literal"),
    pytest.param("import pkg.lazy", "def f(a: 'pkg.lazy.Thing'): ...", True, id="dotted"),
    pytest.param("import pkg.lazy", "def f(a: 'pkg.lazy.Thing'): ...\npkg.other.run()", False, id="dotted_used"),
    pytest.param("import pkg.lazy as lz", "def f(a: lz.Thing): ...", True, id="dotted_alias"),
]


@pytest.mark.parametrize(("guard", "use", "type_only"), BINDINGS)
def test_every_name_an_import_binds_must_be_type_only(tmp_path, guard, use, type_only):
    source = f"from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    {guard}\n{use}\n"

    assert ("pkg.lazy" not in parse(tmp_path, source)) is type_only


@pytest.mark.parametrize(
    ("text", "name", "expected"),
    [
        ("Thing", "Thing", True),
        ("Thing | None", "Thing", True),
        ("list[Thing]", "Thing", True),
        ("pkg.Thing", "Thing", True),
        ("Things", "Thing", False),
        ("AThing", "Thing", False),
        ("_Thing", "Thing", False),
        ("Thing2", "Thing", False),
        ("Thingé", "Thing", False),
        ("Chose", "Chosé", False),
        ("Chosé | None", "Chosé", True),
        ("ThingThing Thing", "Thing", True),
        ("", "Thing", False),
    ],
)
def test_a_string_mentions_a_name_as_a_whole_identifier(text, name, expected):
    assert mentions(text, name) is expected


PARITY_CASES = [
    BLOCKS,
    BLOCKS + PARITY_SOURCE,
    *(GUARD + case.values[0] for case in (*ANNOTATIONS_ONLY, *RUNTIME_USES, *LAZY_IDIOMS)),
    *(
        f"from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    {guard}\n{use}\n"
        for guard, use, _ in (c.values for c in BINDINGS)
    ),
    GUARD + "label = f'Thing'\n",  # an f-string's text: no string of its own, to either parser
    GUARD + "def f(x: Thing): ...\nlabel = f'Thing'\n",
    GUARD + "def f(x: Thing): ...\nfrom pkg.lazy import Other\n",  # pkg.lazy: a runtime candidate too
    *(case.values[0] for case in MODULE_IMPORTS),
    GUARD + "def f(x: 'Thingé'): ...\n",
    GUARD + "def f(x: 'Chosé'): ...\nif TYPE_CHECKING:\n    from pkg.other import Chosé\n",
    GUARD + "def f(x: '''Thing''' 'Thing'): ...\n",
    GUARD + "def f():\n    '''Thing'''\n    return Thing\n",
]


@pytest.mark.parametrize("source", PARITY_CASES)
def test_both_backends_split_alike(tmp_path, source):
    """Which imports are type-only must not depend on whether the ``fast`` extra is installed."""
    python, rust = both_backends(tmp_path, source)

    assert python == rust


def test_by_default_every_candidate_is_a_runtime_one_in_both_backends(tmp_path):
    rust = pytest.importorskip("pytest_impacted_rs")
    path = tmp_path / "mod.py"
    path.write_text(BLOCKS + PARITY_SOURCE)

    python = parsing.parse_file_imports(str(path), "my_package.sub.mod")

    assert python == rust.parse_file_imports(str(path), "my_package.sub.mod", False)
    assert python == rust.parse_all_imports([(str(path), "my_package.sub.mod", False)])["my_package.sub.mod"]
    assert parsing.split_file_imports(str(path), "my_package.sub.mod", find_type_only=False) == (python, [])
