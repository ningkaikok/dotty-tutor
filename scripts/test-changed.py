#!/usr/bin/env python3
"""Run only the tests affected by the current change; fall back to the full suite when unsure.

Backend selection follows the real import graph of ``apps/api`` (including imports
inside functions), so a test that reaches a changed module through a service is
selected too. Frontend selection delegates to ``vitest related``. This is a local
development aid: CI and pre-PR checks still run the full suites.

Usage (from the repository root):
    python scripts/test-changed.py                 # diff against origin/main + working tree
    python scripts/test-changed.py --base HEAD     # only uncommitted changes
    python scripts/test-changed.py --list          # print the selection without running
"""

from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web"

# Changes here can affect any test (schema, fixtures, dependency set), so selection is unsafe.
FULL_SUITE_TRIGGERS = (
    "apps/api/migrations/",
    "apps/api/persistence/schema",
    "apps/api/persistence/migration",
    "apps/api/persistence/database.py",
    "apps/api/tests/postgres_test_support.py",
    "apps/api/tests/postgres_test_runner.py",
    "apps/api/tests/auth_test_support.py",
    "apps/api/pyproject.toml",
    "apps/api/uv.lock",
)
WEB_FULL_SUITE_TRIGGERS = ("apps/web/package.json", "pnpm-lock.yaml", "apps/web/vite.config", "apps/web/vitest")


def changed_files(base: str) -> list[str]:
    def git(*args: str) -> list[str]:
        output = subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout
        return [line.strip() for line in output.splitlines() if line.strip()]

    merge_base = git("merge-base", base, "HEAD")[0] if base != "HEAD" else "HEAD"
    files = set(git("diff", "--name-only", merge_base)) | set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(files)


def module_name(path: Path) -> str:
    parts = list(path.relative_to(API).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def import_graph() -> dict[str, set[str]]:
    """Map every apps/api module to the modules that import it (reverse edges)."""
    modules = {module_name(path): path for path in API.rglob("*.py") if ".venv" not in path.parts}
    importers: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        package = name.rsplit(".", 1)[0] if "." in name else ""
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    anchor = package.split(".")[: max(0, len(package.split(".")) - node.level + 1)]
                    base = ".".join([*anchor, base]) if base else ".".join(anchor)
                # "from pkg import mod" may import a submodule rather than a symbol.
                targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            for target in targets:
                # Credit the module and its parent packages, whose __init__ also runs.
                pieces = target.split(".")
                for size in range(1, len(pieces) + 1):
                    candidate = ".".join(pieces[:size])
                    if candidate in importers and candidate != name:
                        importers[candidate].add(name)
    return importers


def affected_backend_tests(files: list[str]) -> list[str] | None:
    """Return selected test modules, or None when the full suite is required."""
    api_files = [item for item in files if item.startswith("apps/api/") and item.endswith((".py", ".toml", ".lock"))]
    if any(item.startswith(FULL_SUITE_TRIGGERS) for item in api_files):
        return None
    importers = import_graph()
    pending = [module_name(ROOT / item) for item in api_files if item.endswith(".py") and (ROOT / item).exists()]
    seen = set(pending)
    while pending:
        current = pending.pop()
        for parent in importers.get(current, ()):
            if parent not in seen:
                seen.add(parent)
                pending.append(parent)
    selected = {name for name in seen if name.startswith("tests.test_")}
    if any(not item.startswith("apps/api/tests/") for item in api_files):
        # Subprocess/importlib loads (e.g. the OpenAPI export runs the whole app) are
        # invisible to the static graph, so these tests run for any source change.
        selected |= dynamic_loading_tests()
    return sorted(selected)


DYNAMIC_LOADING_MARKERS = ("subprocess", "importlib", "import_module", "__import__", "runpy")


def dynamic_loading_tests() -> set[str]:
    return {
        module_name(path) for path in (API / "tests").glob("test_*.py")
        if any(marker in path.read_text(encoding="utf-8") for marker in DYNAMIC_LOADING_MARKERS)
    }


def run_backend(selection: list[str] | None, *, list_only: bool) -> int:
    if selection == []:
        print("后端：没有受影响的测试。")
        return 0
    label = "全量（改动涉及迁移/存储结构/测试设施/依赖）" if selection is None else f"{len(selection)} 个测试模块"
    print(f"后端：{label}")
    if selection:
        print("  " + "\n  ".join(selection))
    if list_only:
        return 0
    # Run inside the backend's uv environment: this script is usually launched with a
    # system Python that lacks the backend dependencies. The runner parallelises modules
    # and skips database tests itself when DOTTY_TEST_POSTGRES_ADMIN_URL is unset.
    uv = shutil.which("uv")
    if uv is None:
        print("后端：找不到 uv，请先把 uv 加入 PATH。")
        return 1
    command = [uv, "run", "python", "-m", "tests.postgres_test_runner", *(selection or [])]
    return subprocess.run(command, cwd=API).returncode


def run_frontend(files: list[str], *, list_only: bool) -> int:
    web_files = [item for item in files if item.startswith("apps/web/")]
    if not web_files:
        print("前端：没有改动。")
        return 0
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print("前端：找不到 pnpm，跳过。")
        return 1
    if any(item.startswith(WEB_FULL_SUITE_TRIGGERS) for item in files):
        args = ["exec", "vitest", "run"]
        print("前端：全量 vitest（依赖或测试配置有变化）")
    else:
        sources = [str(Path(item).relative_to("apps/web")) for item in web_files if item.startswith("apps/web/src/")
                   and (ROOT / item).exists()]
        if not sources:
            print("前端：没有 src 下的改动。")
            return 0
        args = ["exec", "vitest", "related", "--run", *sources]
        print(f"前端：vitest related（{len(sources)} 个改动文件）")
    if list_only:
        return 0
    return subprocess.run([pnpm, *args], cwd=WEB).returncode


def main() -> int:
    # Windows consoles default to a legacy code page that cannot print the Chinese messages.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main", help="比较基准，默认 origin/main；HEAD 表示只看未提交改动")
    parser.add_argument("--list", action="store_true", help="只列出将要运行的测试")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--frontend-only", action="store_true")
    options = parser.parse_args()
    files = changed_files(options.base)
    status = 0
    if not options.frontend_only:
        status |= run_backend(affected_backend_tests(files), list_only=options.list)
    if not options.backend_only:
        status |= run_frontend(files, list_only=options.list)
    return status


if __name__ == "__main__":
    sys.exit(main())
