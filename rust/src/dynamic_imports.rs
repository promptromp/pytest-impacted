//! Literal dynamic imports: syntax only, with conservative alias unions.
use std::collections::{HashMap, HashSet};

use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprCall, Number, Stmt};

type Bindings = HashMap<String, HashSet<String>>;

#[derive(Default)]
struct Aliases(Bindings);

impl<'a> Visitor<'a> for Aliases {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        match stmt {
            Stmt::Import(node) => {
                for alias in &node.names {
                    let first = alias.name.split('.').next().unwrap_or_default();
                    let (bound, name) = match &alias.asname {
                        Some(bound) => (bound.as_str(), alias.name.as_str()),
                        None => (first, first),
                    };
                    self.0
                        .entry(bound.to_owned())
                        .or_default()
                        .insert(name.to_owned());
                }
            }
            Stmt::ImportFrom(node) if node.level == 0 => {
                for alias in &node.names {
                    let bound = alias.asname.as_ref().unwrap_or(&alias.name);
                    let module = node.module.as_ref().map_or("", |name| name.as_str());
                    self.0
                        .entry(bound.to_string())
                        .or_default()
                        .insert(format!("{module}.{}", alias.name));
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, stmt);
    }
}

fn names(expr: &Expr, bindings: &Bindings) -> HashSet<String> {
    match expr {
        Expr::Name(node) => bindings.get(node.id.as_str()).cloned().unwrap_or_default(),
        Expr::Attribute(node) => names(&node.value, bindings)
            .into_iter()
            .map(|name| format!("{name}.{}", node.attr))
            .collect(),
        _ => HashSet::new(),
    }
}

fn argument<'a>(call: &'a ExprCall, position: usize, name: &str) -> Option<&'a Expr> {
    call.arguments
        .keywords
        .iter()
        .find(|kw| kw.arg.as_ref().is_some_and(|arg| arg.as_str() == name))
        .map(|kw| &kw.value)
        .or_else(|| call.arguments.args.get(position))
}

fn string(expr: Option<&Expr>) -> Option<&str> {
    match expr {
        Some(Expr::StringLiteral(node)) => Some(node.value.to_str()),
        _ => None,
    }
}

fn relative(name: &str, package: Option<&str>) -> Option<String> {
    let package = package.filter(|value| !value.is_empty())?;
    let level = name.len() - name.trim_start_matches('.').len();
    let parts: Vec<&str> = package.split('.').collect();
    if level > parts.len() {
        return None;
    }
    let mut resolved = parts[..parts.len() - level + 1].join(".");
    if !name[level..].is_empty() {
        resolved.push('.');
        resolved.push_str(&name[level..]);
    }
    Some(resolved)
}

struct Calls<'a> {
    bindings: &'a Bindings,
    package: &'a str,
    found: HashSet<String>,
}

impl<'a> Visitor<'a> for Calls<'_> {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Call(call) = expr {
            let functions = names(&call.func, self.bindings);
            if let Some(name) = string(argument(call, 0, "name")) {
                if functions.contains("importlib.import_module") {
                    let absolute = if name.starts_with('.') {
                        let arg = argument(call, 1, "package");
                        let package = match arg {
                            Some(Expr::Name(node)) if node.id == "__package__" => {
                                Some(self.package)
                            }
                            _ => string(arg),
                        };
                        relative(name, package)
                    } else {
                        Some(name.to_owned())
                    };
                    if let Some(value) = absolute.filter(|value| !value.is_empty()) {
                        self.found.insert(value);
                    }
                }
                if functions.contains("builtins.__import__")
                    && !name.is_empty()
                    && !name.starts_with('.')
                {
                    let absolute = match argument(call, 4, "level") {
                        None => true,
                        Some(Expr::NumberLiteral(node)) => {
                            matches!(&node.value, Number::Int(value) if value.as_u64() == Some(0))
                        }
                        Some(Expr::BooleanLiteral(node)) => !node.value,
                        _ => false,
                    };
                    if absolute {
                        self.found.insert(name.to_owned());
                        let values = match argument(call, 3, "fromlist") {
                            Some(Expr::List(node)) => Some(&node.elts),
                            Some(Expr::Tuple(node)) => Some(&node.elts),
                            _ => None,
                        };
                        if let Some(values) = values {
                            for value in values
                                .iter()
                                .filter_map(|value| string(Some(value)))
                                .filter(|value| !value.is_empty() && *value != "*")
                            {
                                self.found.insert(format!("{name}.{value}"));
                            }
                        }
                    }
                }
            }
        }
        visitor::walk_expr(self, expr);
    }
}

pub fn literal_imports(suite: &[Stmt], package: &str) -> HashSet<String> {
    let mut aliases = Aliases::default();
    aliases
        .0
        .entry("__import__".to_owned())
        .or_default()
        .insert("builtins.__import__".to_owned());
    for stmt in suite {
        aliases.visit_stmt(stmt);
    }
    let mut calls = Calls {
        bindings: &aliases.0,
        package,
        found: HashSet::new(),
    };
    for stmt in suite {
        calls.visit_stmt(stmt);
    }
    calls.found
}
