//! Which imports under `if TYPE_CHECKING:` are type-only: the names they bind serve annotations alone.
//!
//! Mirrors `pytest_impacted/type_only.py`, which explains the rule: an import there is type-only
//! when the module uses every name it binds, and only in annotations or another such block.

use std::collections::HashSet;

use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Alias, Expr, ExprContext, Stmt};

/// The addresses (see `address`) of the import statements in `suite` whose names only annotations use.
pub fn type_only_imports(suite: &[Stmt]) -> HashSet<usize> {
    let modules = module_names(suite);
    let mut walk = TypeOnlyWalk::new(&modules, HashSet::new());
    walk.visit_body(suite);
    let watched: HashSet<String> = walk
        .guarded
        .iter()
        .filter_map(|(_, names)| names.as_ref())
        .flatten()
        .cloned()
        .collect();
    if watched.is_empty() {
        return HashSet::new();
    }
    let guarded = walk.guarded;
    let mut uses = TypeOnlyWalk::new(&modules, watched);
    uses.visit_body(suite);
    guarded
        .into_iter()
        .filter(|(_, names)| {
            names.as_ref().is_some_and(|names| {
                names
                    .iter()
                    .all(|name| uses.typed.contains(name) && !uses.runtime.contains(name))
            })
        })
        .map(|(address, _)| address)
        .collect()
}

/// A statement's identity within one parse, to tell the type-only imports from the others.
pub fn address(stmt: &Stmt) -> usize {
    std::ptr::from_ref(stmt) as usize
}

/// Whether `text` holds `name` as a whole identifier: `Model` in `"Model | None"`, not in `"Models"`.
/// Mirrors `mentions` in the Python backend.
pub fn mentions(text: &str, name: &str) -> bool {
    text.match_indices(name).any(|(start, _)| {
        let before = text[..start].chars().next_back();
        let after = text[start + name.len()..].chars().next();
        !before.is_some_and(identifier_char) && !after.is_some_and(identifier_char)
    })
}

/// A character an identifier may hold: any non-ASCII one counts, so both backends agree on every script.
pub(crate) fn identifier_char(c: char) -> bool {
    c == '_' || c.is_ascii_alphanumeric() || !c.is_ascii()
}

/// Whether an `if` test is `TYPE_CHECKING` alone, as type checkers read it, or a module's: the name,
/// or the attribute of a module the file imports at module level. Mirrors `is_type_checking` in the
/// Python backend.
fn is_type_checking(test: &Expr, modules: &HashSet<String>) -> bool {
    match test {
        Expr::Name(name) => name.id.as_str() == "TYPE_CHECKING",
        Expr::Attribute(attr) => {
            attr.attr.as_str() == "TYPE_CHECKING"
                && matches!(&*attr.value, Expr::Name(name) if modules.contains(name.id.as_str()))
        }
        _ => false,
    }
}

/// The names module-level `import` statements bind.
fn module_names(suite: &[Stmt]) -> HashSet<String> {
    let mut names = HashSet::new();
    for_module_level(suite, &mut |stmt| {
        if let Stmt::Import(node) = stmt {
            names.extend(node.names.iter().map(bound_by_import));
        }
    });
    names
}

/// Each statement that runs when the module is imported, nested blocks included, but no function or
/// class body. Mirrors `_module_level` in the Python backend.
fn for_module_level<'a, F: FnMut(&'a Stmt)>(stmts: &'a [Stmt], f: &mut F) {
    for stmt in stmts {
        f(stmt);
        match stmt {
            Stmt::If(node) => {
                for_module_level(&node.body, f);
                for clause in &node.elif_else_clauses {
                    for_module_level(&clause.body, f);
                }
            }
            Stmt::Try(node) => {
                for_module_level(&node.body, f);
                for handler in &node.handlers {
                    let ruff_python_ast::ExceptHandler::ExceptHandler(h) = handler;
                    for_module_level(&h.body, f);
                }
                for_module_level(&node.orelse, f);
                for_module_level(&node.finalbody, f);
            }
            Stmt::With(node) => for_module_level(&node.body, f),
            Stmt::For(node) => {
                for_module_level(&node.body, f);
                for_module_level(&node.orelse, f);
            }
            Stmt::While(node) => {
                for_module_level(&node.body, f);
                for_module_level(&node.orelse, f);
            }
            Stmt::Match(node) => {
                for case in &node.cases {
                    for_module_level(&case.body, f);
                }
            }
            _ => {}
        }
    }
}

/// The name `import a.b` (`a`) or `import a.b as c` (`c`) binds.
fn bound_by_import(alias: &Alias) -> String {
    match &alias.asname {
        Some(asname) => asname.to_string(),
        None => alias
            .name
            .as_str()
            .split('.')
            .next()
            .unwrap_or_default()
            .to_string(),
    }
}

/// One walk over the module: the imports inside `if TYPE_CHECKING:` bodies, with the names they bind
/// (`None` for a star import, whose names cannot be known), and where the `watched` names are used.
struct TypeOnlyWalk<'m> {
    modules: &'m HashSet<String>,
    watched: HashSet<String>,
    /// Inside how many annotations and `if TYPE_CHECKING:` bodies: no code runs there.
    depth: usize,
    guarded: Vec<(usize, Option<Vec<String>>)>,
    typed: HashSet<String>,
    runtime: HashSet<String>,
}

impl<'m> TypeOnlyWalk<'m> {
    fn new(modules: &'m HashSet<String>, watched: HashSet<String>) -> Self {
        TypeOnlyWalk {
            modules,
            watched,
            depth: 0,
            guarded: Vec::new(),
            typed: HashSet::new(),
            runtime: HashSet::new(),
        }
    }

    fn visit_block(&mut self, type_only: bool, body: &[Stmt]) {
        self.depth += usize::from(type_only);
        self.visit_body(body);
        self.depth -= usize::from(type_only);
    }

    fn record(&mut self, names: Vec<String>) {
        let into = if self.depth > 0 {
            &mut self.typed
        } else {
            &mut self.runtime
        };
        into.extend(names);
    }
}

impl<'a> Visitor<'a> for TypeOnlyWalk<'_> {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        match stmt {
            Stmt::If(node) => {
                self.visit_expr(&node.test);
                self.visit_block(is_type_checking(&node.test, self.modules), &node.body);
                // An `elif TYPE_CHECKING:` too: astroid nests it as an `if` in the `orelse`.
                for clause in &node.elif_else_clauses {
                    if let Some(test) = &clause.test {
                        self.visit_expr(test);
                    }
                    let type_only = clause
                        .test
                        .as_ref()
                        .is_some_and(|test| is_type_checking(test, self.modules));
                    self.visit_block(type_only, &clause.body);
                }
            }
            // No code reads a bare string, such as a docstring (astroid keeps those out of its tree).
            Stmt::Expr(node) if matches!(*node.value, Expr::StringLiteral(_)) => {}
            Stmt::Import(node) if self.depth > 0 => {
                let names = node.names.iter().map(bound_by_import).collect();
                self.guarded.push((address(stmt), Some(names)));
            }
            Stmt::ImportFrom(node) if self.depth > 0 => {
                let star = node.names.iter().any(|alias| alias.name.as_str() == "*");
                let names = (!star).then(|| {
                    node.names
                        .iter()
                        .map(|alias| alias.asname.as_ref().unwrap_or(&alias.name).to_string())
                        .collect()
                });
                self.guarded.push((address(stmt), names));
            }
            _ => visitor::walk_stmt(self, stmt),
        }
    }

    fn visit_annotation(&mut self, expr: &'a Expr) {
        self.depth += 1;
        self.visit_expr(expr);
        self.depth -= 1;
    }

    fn visit_expr(&mut self, expr: &'a Expr) {
        match expr {
            Expr::Name(name)
                if matches!(name.ctx, ExprContext::Load)
                    && self.watched.contains(name.id.as_str()) =>
            {
                self.record(vec![name.id.to_string()]);
            }
            // A string counts wherever it may be read: an annotation's, or one the code uses.
            Expr::StringLiteral(string) => {
                let text = string.value.to_str();
                let named: Vec<String> = self
                    .watched
                    .iter()
                    .filter(|name| mentions(text, name))
                    .cloned()
                    .collect();
                self.record(named);
            }
            _ => {}
        }
        visitor::walk_expr(self, expr);
    }
}
