#!/usr/bin/env python3
"""Benchmark: Rust vs Python import parsing.

Compares the performance of the Rust extension (ruff parser + rayon parallelism)
against the pure-Python implementation (astroid) for parsing imports from all
modules in the pytest-impacted codebase.

Usage:
    python benchmarks/bench_parsing.py
    python benchmarks/bench_parsing.py --module /path/to/large/package
"""

import argparse
import os
import time

from pytest_impacted.traversal import discover_submodules


def bench_python_sequential(submodules: dict[str, str]) -> dict[str, list[str]]:
    """Parse all modules using the Python (astroid) implementation."""
    from pytest_impacted.parsing import parse_file_imports  # noqa: PLC0415

    results = {}
    for name, file_path in submodules.items():
        is_pkg = file_path.endswith("__init__.py")
        results[name] = parse_file_imports(file_path, name, is_package=is_pkg)
    return results


def bench_rust_single(submodules: dict[str, str]) -> dict[str, list[str]]:
    """Parse all modules using the Rust extension (sequential, one at a time)."""
    from pytest_impacted_rs import parse_file_imports  # noqa: PLC0415

    results = {}
    for name, file_path in submodules.items():
        is_pkg = file_path.endswith("__init__.py")
        results[name] = parse_file_imports(file_path, name, is_pkg)
    return results


def bench_rust_parallel(submodules: dict[str, str]) -> dict[str, list[str]]:
    """Parse all modules using the Rust extension (parallel batch via rayon)."""
    from pytest_impacted_rs import parse_all_imports  # noqa: PLC0415

    modules_info = [(path, name, path.endswith("__init__.py")) for name, path in submodules.items()]
    return parse_all_imports(modules_info)


def time_fn(fn, *args, iterations=3):
    """Run a function multiple times and return the best time."""
    times = []
    result = None
    for _ in range(iterations):
        start = time.perf_counter()
        result = fn(*args)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    return min(times), result


def main():
    parser = argparse.ArgumentParser(description="Benchmark Rust vs Python import parsing")
    parser.add_argument("--module", default="pytest_impacted", help="Module to benchmark (default: pytest_impacted)")
    parser.add_argument("--tests-dir", default="tests", help="Tests directory (default: tests)")
    parser.add_argument("--iterations", type=int, default=5, help="Number of iterations per benchmark")
    args = parser.parse_args()

    # Discover modules
    submodules = discover_submodules(args.module, require_init=True)
    if args.tests_dir and os.path.isdir(args.tests_dir):
        test_submodules = discover_submodules(args.tests_dir, require_init=False)
        submodules = {**submodules, **test_submodules}

    print(f"Benchmarking import parsing for {len(submodules)} modules")
    print(f"Module: {args.module}, Tests dir: {args.tests_dir}")
    print(f"Iterations: {args.iterations}")
    print()

    # Check Rust availability
    try:
        import pytest_impacted_rs  # noqa: F401, PLC0415

        rust_available = True
    except ImportError:
        rust_available = False
        print("WARNING: Rust extension not available. Install with: maturin develop --release")
        print()

    # Benchmark Python (astroid)
    print("Running Python (astroid) sequential benchmark...")
    python_time, python_results = time_fn(bench_python_sequential, submodules, iterations=args.iterations)
    print(f"  Python (astroid):      {python_time * 1000:8.2f} ms")

    if rust_available:
        # Benchmark Rust sequential
        print("Running Rust sequential benchmark...")
        rust_seq_time, rust_seq_results = time_fn(bench_rust_single, submodules, iterations=args.iterations)
        print(f"  Rust (sequential):     {rust_seq_time * 1000:8.2f} ms")

        # Benchmark Rust parallel
        print("Running Rust parallel benchmark...")
        rust_par_time, rust_par_results = time_fn(bench_rust_parallel, submodules, iterations=args.iterations)
        print(f"  Rust (parallel/rayon): {rust_par_time * 1000:8.2f} ms")

        print()
        print(f"Speedup (Rust seq vs Python):      {python_time / rust_seq_time:.1f}x")
        print(f"Speedup (Rust parallel vs Python):  {python_time / rust_par_time:.1f}x")

        # Correctness check: Rust results should be a superset of Python results
        # (Rust returns more because it doesn't use is_module_path() filtering)
        print()
        print("Correctness check (Rust results should be superset of Python):")
        all_match = True
        for name in python_results:
            python_set = set(python_results.get(name, []))
            rust_set = set(rust_par_results.get(name, []))
            missing = python_set - rust_set
            if missing:
                print(f"  MISMATCH in {name}: Python found {missing} not in Rust results")
                all_match = False
        if all_match:
            print("  All Python imports are present in Rust results.")
    else:
        print()
        print("Skipping Rust benchmarks (extension not available)")


if __name__ == "__main__":
    main()
