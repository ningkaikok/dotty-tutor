"""Run the backend suite, or selected test modules, across parallel worker processes.

Usage (from ``apps/api``):
    uv run python -m tests.postgres_test_runner                    # every tests/test_*.py
    uv run python -m tests.postgres_test_runner tests.test_app     # selected modules
    uv run python -m tests.postgres_test_runner --workers 1        # serial, easier to debug

Each worker owns its own runtime database (cloned from the migrated template) and its
own ``DOTTY_DATA_DIR``, so workers never share mutable state; ``PostgresTestCase``
classes already clone a private database per class. Modules are handed out from a
shared queue, largest file first, which balances workers without stored timings.

Without ``DOTTY_TEST_POSTGRES_ADMIN_URL`` no database is created and the database
tests skip themselves, exactly as with plain ``unittest discover``.
"""

from __future__ import annotations

import argparse
import io
import multiprocessing
import os
import queue
import sys
import tempfile
import time
import traceback
import unittest
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.postgres_test_support import (
    TEST_ADMIN_ENV,
    PostgresTestDatabase,
    migrated_template,
    postgres_tests_enabled,
)

TESTS_DIR = Path(__file__).resolve().parent
WORKERS_ENV = "DOTTY_TEST_WORKERS"
# GitHub-hosted runners have 4 vCPUs; more workers mostly add PostgreSQL contention.
DEFAULT_MAX_WORKERS = 4


@dataclass
class ModuleResult:
    """Outcome of one test module.

    It crosses the worker boundary as a plain dict: under ``coverage run -m`` the
    parent's ``__main__`` is coverage itself, so a class pickled by reference from
    this module would not resolve there.
    """

    module: str
    tests_run: int
    failures: int
    errors: int
    skipped: int
    successful: bool
    seconds: float
    output: str


def _run_module(module: str) -> ModuleResult:
    stream = io.StringIO()
    started = time.perf_counter()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(module)
        result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    except Exception:  # an import error must fail the run, not kill the worker
        return ModuleResult(module, 0, 0, 1, 0, False, time.perf_counter() - started,
                            traceback.format_exc())
    return ModuleResult(
        module=module,
        tests_run=result.testsRun,
        failures=len(result.failures) + len(result.unexpectedSuccesses),
        errors=len(result.errors),
        skipped=len(result.skipped),
        successful=result.wasSuccessful(),
        seconds=time.perf_counter() - started,
        output=stream.getvalue(),
    )


def _worker(tasks: Any, results: Any, use_postgres: bool) -> None:
    with ExitStack() as stack:
        if use_postgres:
            data_root = stack.enter_context(tempfile.TemporaryDirectory(prefix="dotty-pg-tests-"))
            os.environ["DOTTY_DATA_DIR"] = data_root
            database = stack.enter_context(PostgresTestDatabase.create(template=migrated_template()))
            os.environ["DATABASE_URL"] = database.database_url
        while (module := tasks.get()) is not None:
            results.put(asdict(_run_module(module)))


def _all_modules() -> list[str]:
    return [f"tests.{path.stem}" for path in TESTS_DIR.glob("test_*.py")]


def _module_size(module: str) -> int:
    path = TESTS_DIR / f"{module.rsplit('.', 1)[-1]}.py"
    return path.stat().st_size if path.exists() else 0


def _default_workers() -> int:
    configured = os.getenv(WORKERS_ENV)
    if configured:
        return int(configured)
    return min(DEFAULT_MAX_WORKERS, os.cpu_count() or 1)


def _collect(results: Any, processes: list[Any], expected: list[str]) -> list[ModuleResult]:
    """Gather results; a worker that dies mid-module turns its module into an error."""
    collected: list[ModuleResult] = []
    pending = set(expected)
    while pending:
        try:
            item = ModuleResult(**results.get(timeout=2))
        except queue.Empty:
            if any(process.is_alive() for process in processes):
                continue
            for module in sorted(pending):
                collected.append(ModuleResult(module, 0, 0, 1, 0, False, 0.0,
                                              "worker process exited before reporting this module\n"))
            break
        pending.discard(item.module)
        collected.append(item)
        status = "ok" if item.successful else "FAIL"
        skipped = f", {item.skipped} skipped" if item.skipped else ""
        print(f"{status:<4} {item.module} ({item.tests_run} tests{skipped}, {item.seconds:.1f}s)", flush=True)
        if not item.successful:
            print(item.output, flush=True)
    return collected


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("modules", nargs="*", help="dotted test modules, e.g. tests.test_app; default: all")
    parser.add_argument("--workers", type=int, default=_default_workers(),
                        help=f"worker processes (default: ${WORKERS_ENV} or min({DEFAULT_MAX_WORKERS}, CPUs))")
    options = parser.parse_args(argv)

    modules = sorted(set(options.modules or _all_modules()), key=_module_size, reverse=True)
    if not modules:
        print("No test modules selected.")
        return 0
    use_postgres = postgres_tests_enabled()
    if use_postgres:
        migrated_template()  # build once up front instead of every worker queueing on the lock
    else:
        print(f"提示：未设置 {TEST_ADMIN_ENV}，数据库测试会被跳过。")
    worker_count = max(1, min(options.workers, len(modules)))

    # spawn on every platform: forking a parent that already holds database
    # connections is unsafe, and it keeps Linux CI identical to Windows dev machines.
    context = multiprocessing.get_context("spawn")
    tasks = context.Queue()
    results = context.Queue()
    for module in modules:
        tasks.put(module)
    for _ in range(worker_count):
        tasks.put(None)

    print(f"Running {len(modules)} test modules with {worker_count} worker(s)", flush=True)
    started = time.perf_counter()
    processes = [context.Process(target=_worker, args=(tasks, results, use_postgres))
                 for _ in range(worker_count)]
    for process in processes:
        process.start()
    collected = _collect(results, processes, modules)
    for process in processes:
        process.join(timeout=60)
    elapsed = time.perf_counter() - started

    print("\nSlowest modules:")
    for item in sorted(collected, key=lambda entry: entry.seconds, reverse=True)[:10]:
        print(f"  {item.seconds:7.1f}s  {item.module}")
    total = sum(item.tests_run for item in collected)
    failures = sum(item.failures for item in collected)
    errors = sum(item.errors for item in collected)
    skipped = sum(item.skipped for item in collected)
    print("-" * 70)
    print(f"Ran {total} tests in {elapsed:.3f}s")
    successful = all(item.successful for item in collected)
    if successful:
        print(f"OK (skipped={skipped})" if skipped else "OK")
        return 0
    failed = ", ".join(sorted(item.module for item in collected if not item.successful))
    print(f"FAILED (failures={failures}, errors={errors}, skipped={skipped})")
    print(f"Failed modules: {failed}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
