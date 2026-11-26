//! The statement bodies nested in a compound statement: the one recursion both statement walks share.

use ruff_python_ast::{ExceptHandler, Stmt};

/// Which nested bodies `for_each_body` hands on.
#[derive(Clone, Copy)]
pub enum Bodies {
    /// Every nested body, function and class bodies included: every statement in the source.
    All,
    /// The blocks that run when the module is imported: no function or class body.
    ModuleLevel,
}

/// Calls `f` with each statement body nested directly in `stmt`, in source order: an `if`'s body and its
/// `elif`/`else` clauses; a `try`'s body, handlers, `else` and `finally`; a `with`'s body; a `for`'s or a
/// `while`'s body and `else`; each `match` case; and, under `Bodies::All`, a function's or a class's body.
/// Async variants and `try`/`except*` are flags on the same nodes. Any other statement has none.
pub fn for_each_body<'a>(stmt: &'a Stmt, bodies: Bodies, f: &mut impl FnMut(&'a [Stmt])) {
    match stmt {
        Stmt::If(node) => {
            f(&node.body);
            for clause in &node.elif_else_clauses {
                f(&clause.body);
            }
        }
        Stmt::Try(node) => {
            f(&node.body);
            for handler in &node.handlers {
                let ExceptHandler::ExceptHandler(h) = handler;
                f(&h.body);
            }
            f(&node.orelse);
            f(&node.finalbody);
        }
        Stmt::With(node) => f(&node.body),
        Stmt::For(node) => {
            f(&node.body);
            f(&node.orelse);
        }
        Stmt::While(node) => {
            f(&node.body);
            f(&node.orelse);
        }
        Stmt::Match(node) => {
            for case in &node.cases {
                f(&case.body);
            }
        }
        Stmt::FunctionDef(node) if matches!(bodies, Bodies::All) => f(&node.body),
        Stmt::ClassDef(node) if matches!(bodies, Bodies::All) => f(&node.body),
        _ => {}
    }
}
