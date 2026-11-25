use std::collections::{HashMap, HashSet};
use std::fs;

use pyo3::prelude::*;
use rayon::prelude::*;
use ruff_python_ast::{Stmt, StmtImport, StmtImportFrom};

mod dynamic_imports;
mod type_only;

/// Resolve a relative import to its absolute module path.
///
/// Mirrors the Python `_package_of()` + `_resolve_relative_import()` logic:
/// - level=1: same package (single dot)
/// - level>1: go up (level-1) packages
fn resolve_relative_import(
    module_name: &str,
    is_package: bool,
    level: u32,
    modname: Option<&str>,
) -> String {
    // Determine the package context (mirrors _package_of)
    let package = if is_package {
        module_name.to_string()
    } else if let Some(pos) = module_name.rfind('.') {
        module_name[..pos].to_string()
    } else {
        String::new()
    };

    // Calculate base package based on level
    let base_package = if level == 1 {
        package.clone()
    } else {
        let parts: Vec<&str> = package.split('.').collect();
        let levels_up = (level - 1) as usize;
        if parts.len() >= levels_up {
            parts[..parts.len() - levels_up].join(".")
        } else {
            String::new()
        }
    };

    // Resolve the module name
    match modname {
        Some(name) if !name.is_empty() => {
            if base_package.is_empty() {
                name.to_string()
            } else {
                format!("{}.{}", base_package, name)
            }
        }
        _ => base_package,
    }
}

/// Extract imports from a single `import x, y` statement.
fn extract_from_import(node: &StmtImport) -> Vec<String> {
    node.names
        .iter()
        .map(|alias| alias.name.to_string())
        .collect()
}

/// Extract imports from a single `from x import y` statement.
///
/// For `from pkg.mod import name`, we return both `pkg.mod.name` and `pkg.mod`
/// since we cannot determine at parse time whether `name` is a submodule or a
/// symbol. The caller (graph builder) keeps only the discovered modules and aliases.
/// `from pkg import *` contributes only `pkg`: `pkg.*` is not a module name.
fn extract_from_import_from(
    node: &StmtImportFrom,
    module_name: &str,
    is_package: bool,
) -> Vec<String> {
    let mut imports = Vec::new();

    // Resolve the base module name
    let resolved_modname = if node.level > 0 {
        resolve_relative_import(
            module_name,
            is_package,
            node.level,
            node.module.as_ref().map(|id| id.as_str()),
        )
    } else {
        node.module
            .as_ref()
            .map(|id| id.to_string())
            .unwrap_or_default()
    };

    for alias in &node.names {
        let name = alias.name.as_str();
        if name == "*" {
            if !resolved_modname.is_empty() && !imports.contains(&resolved_modname) {
                imports.push(resolved_modname.clone());
            }
            continue;
        }
        // Return both the full path and the base module
        // The graph builder keeps only the discovered modules and aliases
        if !resolved_modname.is_empty() {
            let full_name = format!("{}.{}", resolved_modname, name);
            imports.push(full_name);
            // Also add the base module itself
            if !imports.contains(&resolved_modname) {
                imports.push(resolved_modname.clone());
            }
        } else {
            imports.push(name.to_string());
        }
    }

    imports
}

/// A source's runtime and type-only import candidates, each sorted.
type Split = (Vec<String>, Vec<String>);

/// Where the walk puts each import's candidates, and the context relative imports resolve in.
struct Walk<'a> {
    module_name: &'a str,
    is_package: bool,
    /// The addresses of the type-only import statements (see `type_only`).
    type_only: HashSet<usize>,
    runtime: HashSet<String>,
    typed: HashSet<String>,
}

impl Walk<'_> {
    fn add(&mut self, stmt: &Stmt, candidates: Vec<String>) {
        let into = if self.type_only.contains(&type_only::address(stmt)) {
            &mut self.typed
        } else {
            &mut self.runtime
        };
        into.extend(candidates);
    }
}

/// Recursively collect import statements from a list of statements.
///
/// Python imports can appear inside if/try/with/for/while/match/function/class bodies,
/// so we must recurse into all compound statement bodies.
fn collect_imports_from_stmts(stmts: &[Stmt], walk: &mut Walk) {
    for stmt in stmts {
        match stmt {
            Stmt::Import(node) => walk.add(stmt, extract_from_import(node)),
            Stmt::ImportFrom(node) => {
                let candidates = extract_from_import_from(node, walk.module_name, walk.is_package);
                walk.add(stmt, candidates);
            }
            // Recurse into compound statement bodies
            Stmt::If(node) => {
                collect_imports_from_stmts(&node.body, walk);
                for clause in &node.elif_else_clauses {
                    collect_imports_from_stmts(&clause.body, walk);
                }
            }
            Stmt::Try(node) => {
                collect_imports_from_stmts(&node.body, walk);
                for handler in &node.handlers {
                    let ruff_python_ast::ExceptHandler::ExceptHandler(h) = handler;
                    collect_imports_from_stmts(&h.body, walk);
                }
                collect_imports_from_stmts(&node.orelse, walk);
                collect_imports_from_stmts(&node.finalbody, walk);
            }
            Stmt::With(node) => {
                collect_imports_from_stmts(&node.body, walk);
            }
            Stmt::For(node) => {
                collect_imports_from_stmts(&node.body, walk);
                collect_imports_from_stmts(&node.orelse, walk);
            }
            Stmt::While(node) => {
                collect_imports_from_stmts(&node.body, walk);
                collect_imports_from_stmts(&node.orelse, walk);
            }
            Stmt::FunctionDef(node) => {
                collect_imports_from_stmts(&node.body, walk);
            }
            Stmt::ClassDef(node) => {
                collect_imports_from_stmts(&node.body, walk);
            }
            Stmt::Match(node) => {
                for case in &node.cases {
                    collect_imports_from_stmts(&case.body, walk);
                }
            }
            _ => {}
        }
    }
}

/// The runtime and the type-only import candidates of a source, each sorted.
///
/// Every candidate is a runtime one unless `find_type_only` asks for the type-only ones: those of a
/// type-only import (see `type_only`) and of no other. Mirrors `split_file_imports` in the Python
/// backend; used by both PyO3 functions and the parallel batch functions.
fn parse_file_imports_core(
    source: &str,
    module_name: &str,
    is_package: bool,
    find_type_only: bool,
) -> Split {
    if source.trim().is_empty() {
        return (Vec::new(), Vec::new());
    }

    // Parse with ruff's parse_module (returns Parsed<ModModule> directly)
    let parsed = match ruff_python_parser::parse_module(source) {
        Ok(p) => p,
        Err(_) => return (Vec::new(), Vec::new()),
    };

    let suite = parsed.into_suite();
    let mut walk = Walk {
        module_name,
        is_package,
        type_only: if find_type_only {
            type_only::type_only_imports(&suite)
        } else {
            HashSet::new()
        },
        runtime: HashSet::new(),
        typed: HashSet::new(),
    };
    collect_imports_from_stmts(&suite, &mut walk);
    let package = if is_package {
        module_name
    } else {
        module_name
            .rsplit_once('.')
            .map_or("", |(parent, _)| parent)
    };
    walk.runtime
        .extend(dynamic_imports::literal_imports(&suite, package));

    let mut runtime: Vec<String> = walk.runtime.iter().cloned().collect();
    runtime.sort();
    let mut typed: Vec<String> = walk.typed.difference(&walk.runtime).cloned().collect();
    typed.sort();
    (runtime, typed)
}

/// Parse imports from a Python source file.
///
/// Returns a sorted list of imported module names, mirroring the Python
/// `parse_file_imports()` function.
#[pyfunction]
fn parse_file_imports(
    file_path: &str,
    module_name: &str,
    is_package: bool,
) -> PyResult<Vec<String>> {
    let source = match fs::read_to_string(file_path) {
        Ok(s) => s,
        Err(_) => return Ok(Vec::new()),
    };

    Ok(parse_file_imports_core(&source, module_name, is_package, false).0)
}

/// Parse each `(file_path, module_name, is_package)` in parallel, via rayon.
fn parse_all(modules: &[(String, String, bool)], find_type_only: bool) -> Vec<(String, Split)> {
    modules
        .par_iter()
        .map(|(file_path, module_name, is_package)| {
            let source = fs::read_to_string(file_path).unwrap_or_default();
            let split = parse_file_imports_core(&source, module_name, *is_package, find_type_only);
            (module_name.clone(), split)
        })
        .collect()
}

/// Parse imports from multiple Python source files in parallel.
///
/// Takes a list of (file_path, module_name, is_package) tuples and returns
/// a dict mapping module_name -> list of imports.
///
/// This is the primary performance win — parallel file I/O + parsing via rayon.
#[pyfunction]
fn parse_all_imports(
    modules: Vec<(String, String, bool)>,
) -> PyResult<HashMap<String, Vec<String>>> {
    Ok(parse_all(&modules, false)
        .into_iter()
        .map(|(name, (runtime, _))| (name, runtime))
        .collect())
}

/// `parse_all_imports`, with each module's candidates split into runtime and type-only ones
/// (see `type_only`): the graph links the runtime ones, and counts both as evidence of how the
/// project imports its modules. Mirrors `split_file_imports` in the Python backend.
#[pyfunction]
fn parse_all_imports_split(
    modules: Vec<(String, String, bool)>,
) -> PyResult<HashMap<String, Split>> {
    Ok(parse_all(&modules, true).into_iter().collect())
}

/// Python module definition.
#[pymodule]
fn pytest_impacted_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("LITERAL_DYNAMIC_IMPORTS", true)?;
    m.add_function(wrap_pyfunction!(parse_file_imports, m)?)?;
    m.add_function(wrap_pyfunction!(parse_all_imports, m)?)?;
    m.add_function(wrap_pyfunction!(parse_all_imports_split, m)?)?;
    Ok(())
}
