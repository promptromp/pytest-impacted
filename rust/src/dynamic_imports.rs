//! Imports made by name: the literal module and object paths given to the known APIs that import them.
//!
//! Mirrors `pytest_impacted/dynamic_imports.py`, which holds the table (`LOADERS`) and hands it over as data
//! (`TABLE`): one mechanism reads every row, here as there. Nothing is imported or evaluated.

use std::collections::{HashMap, HashSet};

use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprCall, Number, Stmt};

use crate::type_only::identifier_char;

/// What a loader's string argument names (`dynamic_imports.Names`).
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum Names {
    Module,
    Object,
    Attribute,
}

/// One of a call's arguments, passed at its position or by its keyword.
struct Argument {
    position: usize,
    keyword: String,
}

/// A row of the table (`dynamic_imports.Loader`).
struct Loader {
    callee: String,
    name: Argument,
    names: Names,
    package: Option<Argument>,
    fromlist: Option<Argument>,
    level: Option<Argument>,
}

/// A row as Python hands it over: `(callee, position, keyword, names, package, fromlist, level)`, an
/// argument being `(position, keyword)`.
pub type Row = (
    String,
    usize,
    String,
    String,
    Option<(usize, String)>,
    Option<(usize, String)>,
    Option<(usize, String)>,
);

/// What a file's loader calls import: its modules, object paths and attribute paths, each sorted
/// (`dynamic_imports.DynamicImports`).
pub type Found = (Vec<String>, Vec<String>, Vec<String>);

type Bindings = HashMap<String, HashSet<String>>;

/// The table, indexed: the loaders by the last part of their callee, and the names a file calling one spells.
pub struct Table {
    loaders: Vec<Loader>,
    by_last_part: HashMap<String, Vec<usize>>,
    spellings: Vec<String>,
}

impl Table {
    pub fn new(rows: Vec<Row>) -> Result<Self, String> {
        let argument = |arg: Option<(usize, String)>| {
            arg.map(|(position, keyword)| Argument { position, keyword })
        };
        let loaders = rows
            .into_iter()
            .map(
                |(callee, position, keyword, names, package, fromlist, level)| {
                    let names = match names.as_str() {
                        "module" => Names::Module,
                        "object" => Names::Object,
                        "attribute" => Names::Attribute,
                        other => {
                            return Err(format!(
                                "unknown kind of name in the loader table: {other:?}"
                            ))
                        }
                    };
                    Ok(Loader {
                        callee,
                        name: Argument { position, keyword },
                        names,
                        package: argument(package),
                        fromlist: argument(fromlist),
                        level: argument(level),
                    })
                },
            )
            .collect::<Result<Vec<_>, _>>()?;
        let mut by_last_part: HashMap<String, Vec<usize>> = HashMap::new();
        for (index, loader) in loaders.iter().enumerate() {
            by_last_part
                .entry(last_part(&loader.callee).to_owned())
                .or_default()
                .push(index);
        }
        // A file calling a loader spells its name: the last part, or the first callee it is reached through.
        let callees: HashSet<&str> = loaders
            .iter()
            .map(|loader| loader.callee.as_str())
            .collect();
        let spellings = loaders
            .iter()
            .filter(|loader| !callees.contains(parent(&loader.callee)))
            .map(|loader| last_part(&loader.callee).to_owned())
            .collect();
        Ok(Table {
            loaders,
            by_last_part,
            spellings,
        })
    }

    /// The loaders a call to `func` calls: those whose callee the name it calls ends with.
    fn loaders_called(&self, func: &Expr, bindings: &Bindings) -> Vec<&Loader> {
        let last_parts: HashSet<&str> = match func {
            Expr::Attribute(attribute) => HashSet::from([attribute.attr.as_str()]),
            Expr::Name(name) => {
                let mut parts = HashSet::from([name.id.as_str()]);
                if let Some(bound) = bindings.get(name.id.as_str()) {
                    parts.extend(bound.iter().map(|target| last_part(target)));
                }
                parts
            }
            _ => return Vec::new(),
        };
        // By the last part first: most calls call no loader, and resolving their whole name costs more.
        let candidates: Vec<&Loader> = last_parts
            .iter()
            .filter_map(|part| self.by_last_part.get(*part))
            .flatten()
            .map(|&index| &self.loaders[index])
            .collect();
        if candidates.is_empty() {
            return candidates;
        }
        let callees = callees(func, bindings);
        candidates
            .into_iter()
            .filter(|loader| {
                let suffix = format!(".{}", loader.callee);
                callees
                    .iter()
                    .any(|callee| *callee == loader.callee || callee.ends_with(&suffix))
            })
            .collect()
    }
}

/// What `source`'s loader calls import, for the module `module_name` (`dynamic_imports.parse_dynamic_imports`).
pub fn dynamic_imports(source: &str, module_name: &str, is_package: bool, table: &Table) -> Found {
    if !table
        .spellings
        .iter()
        .any(|spelling| source.contains(spelling.as_str()))
    {
        return Found::default(); // cheap pre-filter: no call to a loader without its name
    }
    let Ok(parsed) = ruff_python_parser::parse_module(source) else {
        return Found::default();
    };
    let suite = parsed.into_suite();
    let mut walk = Walk::default();
    for stmt in &suite {
        walk.visit_stmt(stmt);
    }
    let mut found = HashSet::new();
    for call in &walk.calls {
        for loader in table.loaders_called(&call.func, &walk.bindings) {
            named(call, loader, module_name, is_package, &mut found);
        }
    }
    let sorted = |names: Names| {
        let mut paths: Vec<String> = found
            .iter()
            .filter(|(kind, _)| *kind == names)
            .map(|(_, path)| path.clone())
            .collect();
        paths.sort();
        paths
    };
    (
        sorted(Names::Module),
        sorted(Names::Object),
        sorted(Names::Attribute),
    )
}

/// One walk of the module: the names its absolute imports bind anywhere, unioned over scopes (a name bound
/// in two places may call either), and every call passing an argument.
#[derive(Default)]
struct Walk<'a> {
    bindings: Bindings,
    calls: Vec<&'a ExprCall>,
}

impl<'a> Visitor<'a> for Walk<'a> {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        match stmt {
            Stmt::Import(node) => {
                for alias in &node.names {
                    let name = alias.name.as_str();
                    let (bound, target) = match &alias.asname {
                        Some(asname) => (asname.as_str(), name),
                        None => {
                            let first = name.split('.').next().unwrap_or(name);
                            (first, first)
                        }
                    };
                    self.bindings
                        .entry(bound.to_owned())
                        .or_default()
                        .insert(target.to_owned());
                }
            }
            Stmt::ImportFrom(node) if node.level == 0 => {
                if let Some(module) = node.module.as_ref().filter(|module| !module.is_empty()) {
                    for alias in &node.names {
                        let bound = alias.asname.as_ref().unwrap_or(&alias.name);
                        self.bindings
                            .entry(bound.to_string())
                            .or_default()
                            .insert(format!("{module}.{}", alias.name));
                    }
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, stmt);
    }

    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Call(call) = expr {
            if !call.arguments.args.is_empty() || !call.arguments.keywords.is_empty() {
                self.calls.push(call);
            }
        }
        visitor::walk_expr(self, expr);
    }
}

/// The dotted names a call to `func` may call: `unittest.mock.patch.dict` for `mock.patch.dict`. A name no
/// import binds stands for itself; an attribute of anything else, for its attributes alone (`.setattr`).
fn callees(func: &Expr, bindings: &Bindings) -> Vec<String> {
    let mut attributes = Vec::new();
    let mut node = func;
    while let Expr::Attribute(attribute) = node {
        attributes.push(attribute.attr.as_str());
        node = &attribute.value;
    }
    let suffix: String = attributes
        .iter()
        .rev()
        .map(|attribute| format!(".{attribute}"))
        .collect();
    match node {
        Expr::Name(name) => match bindings.get(name.id.as_str()) {
            Some(bound) => bound
                .iter()
                .map(|target| format!("{target}{suffix}"))
                .collect(),
            None => vec![format!("{}{suffix}", name.id)],
        },
        _ => vec![suffix],
    }
}

/// What `call` to `loader` imports, if its argument is a literal naming it.
fn named(
    call: &ExprCall,
    loader: &Loader,
    module_name: &str,
    is_package: bool,
    found: &mut HashSet<(Names, String)>,
) {
    let Some(text) = literal(argument(call, &loader.name)) else {
        return;
    };
    if loader
        .level
        .as_ref()
        .is_some_and(|level| !is_zero(argument(call, level)))
    {
        return;
    }
    if loader.names != Names::Module {
        path(text, loader.names, found);
        return;
    }
    let anchor = loader
        .package
        .as_ref()
        .and_then(|package| anchor(argument(call, package), module_name, is_package));
    let Some(name) = absolute(text, anchor.as_deref()) else {
        return;
    };
    if let Some(fromlist) = &loader.fromlist {
        if let Some(
            Expr::List(ruff_python_ast::ExprList { elts, .. })
            | Expr::Tuple(ruff_python_ast::ExprTuple { elts, .. }),
        ) = argument(call, fromlist)
        {
            for submodule in elts
                .iter()
                .filter_map(|item| literal(Some(item)))
                .filter(|item| is_identifier(item))
            {
                found.insert((Names::Module, format!("{name}.{submodule}")));
            }
        }
    }
    found.insert((Names::Module, name));
}

/// What an object or attribute path names: the module before a `:`; else the path itself. An attribute path
/// of one part names nothing; an object path of one part is a module.
fn path(text: &str, names: Names, found: &mut HashSet<(Names, String)>) {
    if let Some((module, attributes)) = text.split_once(':') {
        if dotted(module) && (attributes.is_empty() || dotted(attributes)) {
            found.insert((Names::Module, module.to_owned()));
        }
    } else if dotted(text) {
        if text.contains('.') {
            found.insert((names, text.to_owned()));
        } else if names == Names::Object {
            found.insert((Names::Module, text.to_owned()));
        }
    }
}

/// The absolute module name `name` means, a relative one resolved against `anchor`; `None` if none.
fn absolute(name: &str, anchor: Option<&str>) -> Option<String> {
    let name = if name.starts_with('.') {
        resolve_name(name, anchor?)?
    } else {
        name.to_owned()
    };
    dotted(&name).then_some(name)
}

/// `importlib.util.resolve_name` for a relative `name`, which is string work alone: `None` where it raises
/// (no package, or beyond the top-level package).
fn resolve_name(name: &str, package: &str) -> Option<String> {
    if package.is_empty() {
        return None;
    }
    let level = name.len() - name.trim_start_matches('.').len();
    let rest = &name[level..];
    // `package.rsplit(".", level - 1)`, whose first piece is the base: rsplitn yields it last.
    let bits: Vec<&str> = package.rsplitn(level, '.').collect();
    if bits.len() < level {
        return None;
    }
    let base = bits[bits.len() - 1];
    Some(if rest.is_empty() {
        base.to_owned()
    } else {
        format!("{base}.{rest}")
    })
}

/// The package a relative name resolves against: a literal, or the calling module's own package or name.
fn anchor(node: Option<&Expr>, module_name: &str, is_package: bool) -> Option<String> {
    if let Some(text) = literal(node) {
        return Some(text.to_owned());
    }
    match node? {
        Expr::Name(name) if name.id.as_str() == "__package__" => {
            Some(package_of(module_name, is_package))
        }
        Expr::Name(name) if name.id.as_str() == "__name__" => Some(module_name.to_owned()),
        Expr::Attribute(attribute) if attribute.attr.as_str() == "parent" => {
            match &*attribute.value {
                Expr::Name(name) if name.id.as_str() == "__spec__" => {
                    Some(package_of(module_name, is_package))
                }
                _ => None,
            }
        }
        _ => None,
    }
}

/// The package a module's relative imports resolve against: itself for a package, else its parent.
fn package_of(module_name: &str, is_package: bool) -> String {
    if is_package {
        module_name.to_owned()
    } else {
        module_name
            .rsplit_once('.')
            .map_or("", |(parent, _)| parent)
            .to_owned()
    }
}

/// The expression `call` passes as `argument`, or `None` if it passes none. Where `*args` or `**kwargs` may
/// pass it, the unpacked expression stands in: it cannot be read, like any computed argument.
fn argument<'a>(call: &'a ExprCall, argument: &Argument) -> Option<&'a Expr> {
    let keywords = &call.arguments.keywords;
    if let Some(keyword) = keywords.iter().find(|keyword| {
        keyword
            .arg
            .as_ref()
            .is_some_and(|arg| arg.as_str() == argument.keyword)
    }) {
        return Some(&keyword.value);
    }
    for (position, arg) in call.arguments.args.iter().enumerate() {
        if matches!(arg, Expr::Starred(_)) || position == argument.position {
            return Some(arg);
        }
    }
    keywords
        .iter()
        .find(|keyword| keyword.arg.is_none())
        .map(|keyword| &keyword.value)
}

fn literal(node: Option<&Expr>) -> Option<&str> {
    match node {
        Some(Expr::StringLiteral(string)) => Some(string.value.to_str()),
        _ => None,
    }
}

/// Whether a `level` argument is absent or a literal 0 (`False` too, as `__import__` takes it).
fn is_zero(node: Option<&Expr>) -> bool {
    match node {
        None => true,
        Some(Expr::NumberLiteral(number)) => {
            matches!(&number.value, Number::Int(int) if int.as_u64() == Some(0))
        }
        Some(Expr::BooleanLiteral(boolean)) => !boolean.value,
        _ => false,
    }
}

/// Whether `text` spells a dotted name, such as a module's.
fn dotted(text: &str) -> bool {
    !text.is_empty() && text.split('.').all(is_identifier)
}

/// Whether `text` is an identifier, any non-ASCII character counting as an identifier's (as in `type_only`).
fn is_identifier(text: &str) -> bool {
    let mut chars = text.chars();
    chars
        .next()
        .is_some_and(|first| identifier_char(first) && !first.is_ascii_digit())
        && chars.all(identifier_char)
}

fn last_part(name: &str) -> &str {
    name.rsplit_once('.').map_or(name, |(_, last)| last)
}

fn parent(name: &str) -> &str {
    name.rsplit_once('.').map_or("", |(parent, _)| parent)
}
