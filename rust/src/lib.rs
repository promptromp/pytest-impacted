use std::collections::HashMap;
use std::fs;

use pyo3::prelude::*;
use rayon::prelude::*;
use ruff_python_ast::{Expr, Stmt, StmtImport, StmtImportFrom};

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

/// Whether an `if` test is `TYPE_CHECKING` or `<name>.TYPE_CHECKING` alone, false at runtime (PEP 484).
///
/// Mirrors `_is_type_checking` in the Python backend: anything else (`not TYPE_CHECKING`,
/// `TYPE_CHECKING or X`, an alias of the name) may be true at runtime.
fn is_type_checking(test: &Expr) -> bool {
    match test {
        Expr::Name(name) => name.id.as_str() == "TYPE_CHECKING",
        Expr::Attribute(attr) => {
            attr.attr.as_str() == "TYPE_CHECKING" && matches!(*attr.value, Expr::Name(_))
        }
        _ => false,
    }
}

/// What the walk leaves out, and the context relative imports resolve in.
struct Walk<'a> {
    module_name: &'a str,
    is_package: bool,
    /// Skip the body of an `if TYPE_CHECKING:` (or `elif`), which never runs.
    ignore_type_checking: bool,
}

/// Recursively collect import statements from a list of statements.
///
/// Python imports can appear inside if/try/with/for/while/match/function/class bodies,
/// so we must recurse into all compound statement bodies.
fn collect_imports_from_stmts(
    stmts: &[Stmt],
    walk: &Walk,
    imports: &mut std::collections::HashSet<String>,
) {
    let (module_name, is_package) = (walk.module_name, walk.is_package);
    let skips = |test: &Expr| walk.ignore_type_checking && is_type_checking(test);
    for stmt in stmts {
        match stmt {
            Stmt::Import(node) => {
                for imp in extract_from_import(node) {
                    imports.insert(imp);
                }
            }
            Stmt::ImportFrom(node) => {
                for imp in extract_from_import_from(node, module_name, is_package) {
                    imports.insert(imp);
                }
            }
            // Recurse into compound statement bodies
            Stmt::If(node) => {
                if !skips(&node.test) {
                    collect_imports_from_stmts(&node.body, walk, imports);
                }
                // An `elif TYPE_CHECKING:` too: astroid nests it as an `if` in the `orelse`.
                for clause in &node.elif_else_clauses {
                    if !clause.test.as_ref().is_some_and(skips) {
                        collect_imports_from_stmts(&clause.body, walk, imports);
                    }
                }
            }
            Stmt::Try(node) => {
                collect_imports_from_stmts(&node.body, walk, imports);
                for handler in &node.handlers {
                    let ruff_python_ast::ExceptHandler::ExceptHandler(h) = handler;
                    collect_imports_from_stmts(&h.body, walk, imports);
                }
                collect_imports_from_stmts(&node.orelse, walk, imports);
                collect_imports_from_stmts(&node.finalbody, walk, imports);
            }
            Stmt::With(node) => {
                collect_imports_from_stmts(&node.body, walk, imports);
            }
            Stmt::For(node) => {
                collect_imports_from_stmts(&node.body, walk, imports);
                collect_imports_from_stmts(&node.orelse, walk, imports);
            }
            Stmt::While(node) => {
                collect_imports_from_stmts(&node.body, walk, imports);
                collect_imports_from_stmts(&node.orelse, walk, imports);
            }
            Stmt::FunctionDef(node) => {
                collect_imports_from_stmts(&node.body, walk, imports);
            }
            Stmt::ClassDef(node) => {
                collect_imports_from_stmts(&node.body, walk, imports);
            }
            Stmt::Match(node) => {
                for case in &node.cases {
                    collect_imports_from_stmts(&case.body, walk, imports);
                }
            }
            _ => {}
        }
    }
}

/// Core import extraction logic used by both the PyO3 function and the parallel batch function.
fn parse_file_imports_core(
    source: &str,
    module_name: &str,
    is_package: bool,
    ignore_type_checking: bool,
) -> Vec<String> {
    if source.trim().is_empty() {
        return Vec::new();
    }

    // Parse with ruff's parse_module (returns Parsed<ModModule> directly)
    let parsed = match ruff_python_parser::parse_module(source) {
        Ok(p) => p,
        Err(_) => return Vec::new(),
    };

    let mut imports = std::collections::HashSet::new();
    let suite = parsed.into_suite();
    let walk = Walk {
        module_name,
        is_package,
        ignore_type_checking,
    };
    collect_imports_from_stmts(&suite, &walk, &mut imports);

    let mut result: Vec<String> = imports.into_iter().collect();
    result.sort();
    result
}

/// Parse imports from a Python source file.
///
/// Returns a sorted list of imported module names, mirroring the Python
/// `parse_file_imports()` function.
#[pyfunction]
#[pyo3(signature = (file_path, module_name, is_package, *, ignore_type_checking_imports=false))]
fn parse_file_imports(
    file_path: &str,
    module_name: &str,
    is_package: bool,
    ignore_type_checking_imports: bool,
) -> PyResult<Vec<String>> {
    let source = match fs::read_to_string(file_path) {
        Ok(s) => s,
        Err(_) => return Ok(Vec::new()),
    };

    Ok(parse_file_imports_core(
        &source,
        module_name,
        is_package,
        ignore_type_checking_imports,
    ))
}

/// Parse imports from multiple Python source files in parallel.
///
/// Takes a list of (file_path, module_name, is_package) tuples and returns
/// a dict mapping module_name -> list of imports.
///
/// This is the primary performance win — parallel file I/O + parsing via rayon.
/// `ignore_type_checking_imports` leaves out the imports under `if TYPE_CHECKING:`;
/// keyword-only, and off by default, so a caller that predates it reads as before.
#[pyfunction]
#[pyo3(signature = (modules, *, ignore_type_checking_imports=false))]
fn parse_all_imports(
    modules: Vec<(String, String, bool)>,
    ignore_type_checking_imports: bool,
) -> PyResult<HashMap<String, Vec<String>>> {
    let results: Vec<(String, Vec<String>)> = modules
        .par_iter()
        .map(|(file_path, module_name, is_package)| {
            let source = fs::read_to_string(file_path).unwrap_or_default();
            let imports = parse_file_imports_core(
                &source,
                module_name,
                *is_package,
                ignore_type_checking_imports,
            );
            (module_name.clone(), imports)
        })
        .collect();

    Ok(results.into_iter().collect())
}

/// Python module definition.
#[pymodule]
fn pytest_impacted_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_file_imports, m)?)?;
    m.add_function(wrap_pyfunction!(parse_all_imports, m)?)?;
    Ok(())
}
