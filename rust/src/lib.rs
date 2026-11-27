use std::collections::{HashMap, HashSet};
use std::fs;
use std::sync::OnceLock;

use pyo3::prelude::*;
use rayon::prelude::*;
use ruff_python_ast::{Stmt, StmtImport, StmtImportFrom};

mod bodies;
mod dynamic_imports;
mod type_only;

use bodies::{for_each_body, Bodies};

/// The stack of each thread a batch runs on. Freeing a parsed tree recurses down an expression's left spine
/// (`a + b + ...`, `x.a.b...`, `f()()...`: ruff parses those in a loop, so its nesting limit does not apply),
/// about 64 bytes a term: on rayon's 2 MiB default a 33,000-term chain overflowed it, and the host process died
/// with no traceback. The stack is reserved, not committed, until a thread uses it.
const STACK: usize = 256 << 20;

/// The largest source `parse_imports_each` and `parse_all_dynamic_imports` parse: a file of N bytes holds at
/// most N/2 terms, so freeing its tree needs at most 32·N bytes of stack — half of `STACK`, leaving a margin for a
/// costlier drop in another ruff release. A larger file (generated code) is handed back to the Python side.
const LARGEST_SOURCE: u64 = 4 << 20;

/// The largest source the older dict functions parse: they read every file, an older pytest-impacted having no
/// way to read one handed back, but a file over this (generated code) imports nothing there, rather than risk
/// freeing a tree deeper than `STACK` holds (a 16 MiB chain did).
const OLDER_LARGEST_SOURCE: u64 = 6 << 20;

/// The pool every batch runs in, with `STACK`-sized threads; `None` when the pool cannot be built (threads
/// refused, as under strict memory overcommit), and then rayon's global pool, as before.
fn pool() -> Option<&'static rayon::ThreadPool> {
    static POOL: OnceLock<Option<rayon::ThreadPool>> = OnceLock::new();
    POOL.get_or_init(|| {
        rayon::ThreadPoolBuilder::new()
            .stack_size(STACK)
            .thread_name(|index| format!("pytest-impacted-rs-{index}"))
            .build()
            .ok()
    })
    .as_ref()
}

/// Run `work` in the pool (see `pool`): its parallel iterators, and freeing every tree they build, on its stacks.
fn in_pool<T: Send>(work: impl FnOnce() -> T + Send) -> T {
    match pool() {
        Some(pool) => pool.install(work),
        None => work(),
    }
}

/// A source the hand-back functions parse: readable, UTF-8, no larger than `LARGEST_SOURCE`. `None` otherwise,
/// for the Python side to read (it decodes a PEP 263 encoding, and reports an unreadable file).
fn source_of(file_path: &str) -> Option<String> {
    let size = fs::metadata(file_path).ok()?.len();
    if size > LARGEST_SOURCE {
        return None;
    }
    fs::read_to_string(file_path).ok()
}

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
/// so we must recurse into all compound statement bodies (`Bodies::All`).
fn collect_imports_from_stmts(stmts: &[Stmt], walk: &mut Walk) {
    for stmt in stmts {
        match stmt {
            Stmt::Import(node) => walk.add(stmt, extract_from_import(node)),
            Stmt::ImportFrom(node) => {
                let candidates = extract_from_import_from(node, walk.module_name, walk.is_package);
                walk.add(stmt, candidates);
            }
            _ => for_each_body(stmt, Bodies::All, &mut |body| {
                collect_imports_from_stmts(body, walk);
            }),
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
    parsed_imports(source, module_name, is_package, find_type_only).unwrap_or_default()
}

/// A source's runtime and type-only candidates (see `parse_file_imports_core`), or `None` for one ruff rejects.
///
/// ruff refuses sources CPython compiles: nested deeper than 202 levels (`a if c else ...` ×203), or with a
/// compiler error the parser leaves to the compiler (`f(a=1, a=2)`). Those are the Python backend's.
fn parsed_imports(
    source: &str,
    module_name: &str,
    is_package: bool,
    find_type_only: bool,
) -> Option<Split> {
    if source.trim().is_empty() {
        return Some((Vec::new(), Vec::new()));
    }
    let parsed = ruff_python_parser::parse_module(source).ok()?;

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

    let mut runtime: Vec<String> = walk.runtime.iter().cloned().collect();
    runtime.sort();
    let mut typed: Vec<String> = walk.typed.difference(&walk.runtime).cloned().collect();
    typed.sort();
    Some((runtime, typed))
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

/// Parse each `(file_path, module_name, is_package)` in parallel, via rayon, in the pool (see `pool`). A file up
/// to `OLDER_LARGEST_SOURCE` is read, past the hand-back cap: an older pytest-impacted calls these, and has no
/// way to read one handed back.
fn parse_all(modules: &[(String, String, bool)], find_type_only: bool) -> Vec<(String, Split)> {
    in_pool(|| {
        modules
            .par_iter()
            .map(|(file_path, module_name, is_package)| {
                let fits = fs::metadata(file_path).is_ok_and(|m| m.len() <= OLDER_LARGEST_SOURCE);
                let source = if fits {
                    fs::read_to_string(file_path).unwrap_or_default()
                } else {
                    String::new()
                };
                let split =
                    parse_file_imports_core(&source, module_name, *is_package, find_type_only);
                (module_name.clone(), split)
            })
            .collect()
    })
}

/// Each `(file_path, module_name, is_package)`'s runtime and type-only import candidates, in the order given
/// and in parallel; `None` for a file the Python backend must read: one ruff rejects, one not UTF-8, one over
/// `LARGEST_SOURCE`, one that cannot be read. So the extension never finds fewer imports than the Python backend would.
/// Without `find_type_only` every candidate is a runtime one.
#[pyfunction]
fn parse_imports_each(
    py: Python<'_>,
    modules: Vec<(String, String, bool)>,
    find_type_only: bool,
) -> PyResult<Vec<Option<Split>>> {
    Ok(py.detach(|| {
        in_pool(|| {
            modules
                .par_iter()
                .map(|(file_path, module_name, is_package)| {
                    let source = source_of(file_path)?;
                    parsed_imports(&source, module_name, *is_package, find_type_only)
                })
                .collect()
        })
    }))
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

/// What each `(file_path, module_name, is_package)` imports by name, in parallel via rayon: its modules, object
/// paths and attribute paths, in the order given; `None` for a file the stdlib pass must read (see
/// `dynamic_imports::dynamic_imports`). `loaders` is the table (`dynamic_imports.TABLE`), read by one
/// mechanism. Mirrors `parse_dynamic_imports` in `pytest_impacted/dynamic_imports.py`.
#[pyfunction]
fn parse_all_dynamic_imports(
    modules: Vec<(String, String, bool)>,
    loaders: Vec<dynamic_imports::Row>,
) -> PyResult<Vec<Option<dynamic_imports::Found>>> {
    let table =
        dynamic_imports::Table::new(loaders).map_err(pyo3::exceptions::PyValueError::new_err)?;
    Ok(in_pool(|| {
        modules
            .par_iter()
            .map(|(file_path, module_name, is_package)| {
                // A file the batch will not parse is the stdlib pass's, as one ruff rejects is.
                let source = source_of(file_path)?;
                dynamic_imports::dynamic_imports(&source, module_name, *is_package, &table)
            })
            .collect()
    }))
}

/// Python module definition.
#[pymodule]
fn pytest_impacted_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_file_imports, m)?)?;
    m.add_function(wrap_pyfunction!(parse_all_imports, m)?)?;
    m.add_function(wrap_pyfunction!(parse_all_imports_split, m)?)?;
    m.add_function(wrap_pyfunction!(parse_all_dynamic_imports, m)?)?;
    m.add_function(wrap_pyfunction!(parse_imports_each, m)?)?;
    Ok(())
}
