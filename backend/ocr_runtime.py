"""教材 OCR Provider 的发现、选择与 MinerU 子进程适配。

OCR 只负责把页面还原为 Markdown、LaTeX 和图片资源，不负责判断题型或生成答案。
``auto`` 模式优先 MinerU，找不到命令时由上层回退到 pypdf 文字层。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


OcrProvider = Literal["auto", "mineru", "pypdf"]


@dataclass
class OcrSelection:
    provider: OcrProvider = "auto"


class OcrRuntime:
    """保存进程级 OCR 选择，并隔离 MinerU 命令行细节。"""

    def __init__(self) -> None:
        self.selection = OcrSelection()

    def mineru_command(self) -> Path | None:
        """按显式配置、项目虚拟环境、PATH 的顺序寻找 MinerU。"""
        configured = os.getenv("MINERU_COMMAND")
        candidates = [
            Path(configured).expanduser() if configured else None,
            Path(__file__).resolve().parents[1] / ".mineru-venv" / "bin" / "mineru",
            Path(shutil.which("mineru")) if shutil.which("mineru") else None,
        ]
        return next((path for path in candidates if path and path.is_file()), None)

    def catalog(self) -> dict:
        command = self.mineru_command()
        effective = self.selection.provider
        if effective == "auto":
            effective = "mineru" if command else "pypdf"
        return {
            "selected": self.selection.provider,
            "effective": effective,
            "providers": [
                {
                    "id": "auto",
                    "label": "自动选择",
                    "available": True,
                    "detail": "优先 MinerU；不可用时仅抽取 PDF 文字层",
                },
                {
                    "id": "mineru",
                    "label": "MinerU OCR",
                    "available": bool(command),
                    "detail": str(command) if command else "未安装：需要独立 Python 3.12 环境和模型",
                },
                {
                    "id": "pypdf",
                    "label": "PDF 文字层",
                    "available": True,
                    "detail": "速度快，但不能识别纯扫描图片",
                },
            ],
        }

    def select(self, provider: OcrProvider) -> dict:
        if provider == "mineru" and not self.mineru_command():
            raise ValueError("MinerU 尚未安装")
        self.selection.provider = provider
        return self.catalog()

    def should_use_mineru(self) -> bool:
        return self.mineru_command() is not None and self.selection.provider in ("auto", "mineru")

    def page_count(self, source_path: Path) -> int:
        """使用 MinerU 环境中的 PDFium 快速读取页数。

        图片很多的教材若由 pypdf 实例化每个页面对象可能耗时接近一分钟；这里只读取 PDF
        目录，不做正文解析，目的是让分批规划尽快返回。
        """
        command = self.mineru_command()
        interpreter = command.parent / "python" if command else None
        if not interpreter or not interpreter.is_file():
            raise RuntimeError("MinerU Python 环境不可用")
        completed = subprocess.run(
            [
                str(interpreter),
                "-c",
                "import pypdfium2,sys; print(len(pypdfium2.PdfDocument(sys.argv[1])))",
                str(source_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"PDFium 读取页数失败：{detail[-500:]}")
        return int(completed.stdout.strip())

    def parse(
        self,
        source_path: Path,
        start_page: int = 0,
        end_page: int | None = None,
        asset_dir: Path | None = None,
        asset_url_prefix: str = "",
    ) -> tuple[str, dict]:
        """调用 MinerU 解析指定页段，并把临时图片复制到持久化资源目录。

        MinerU 的工作目录会在调用结束后删除，所以所有要被前端引用的图片和 source.md
        必须在退出临时目录前复制；返回 URL 而不是本机路径，防止泄露文件系统结构。
        """
        command = self.mineru_command()
        if not command:
            raise RuntimeError("MinerU 尚未安装")
        page_args = ["-s", str(start_page)]
        if end_page is not None:
            page_args.extend(["-e", str(end_page)])
        with tempfile.TemporaryDirectory(prefix="dotty-mineru-") as output_dir:
            completed = subprocess.run(
                [
                    str(command),
                    "-p", str(source_path),
                    "-o", output_dir,
                    "-m", "auto",
                    "-b", "pipeline",
                    "-l", "ch",
                    "-f", "true",
                    "-t", "true",
                    *page_args,
                ],
                capture_output=True,
                text=True,
                timeout=15 * 60,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise RuntimeError(f"MinerU 解析失败：{detail[-1000:]}")
            markdown_files = sorted(
                Path(output_dir).rglob("*.md"),
                key=lambda path: path.stat().st_size,
                reverse=True,
            )
            if not markdown_files:
                raise RuntimeError("MinerU 没有生成 Markdown")
            markdown = markdown_files[0].read_text(encoding="utf-8", errors="replace")
            image_urls: list[str] = []
            image_references = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
            if asset_dir:
                asset_dir.mkdir(parents=True, exist_ok=True)
                copied: set[str] = set()
                for reference in image_references:
                    source_image = next(
                        (path for path in Path(output_dir).rglob(Path(reference).name) if path.is_file()),
                        None,
                    )
                    if not source_image or source_image.name in copied:
                        continue
                    shutil.copy2(source_image, asset_dir / source_image.name)
                    copied.add(source_image.name)
                    image_urls.append(f"{asset_url_prefix}/{source_image.name}")
                (asset_dir / "source.md").write_text(markdown, encoding="utf-8")
            return markdown[:40_000], {
                "requestedProvider": self.selection.provider,
                "provider": "mineru",
                "mode": "pipeline-auto",
                "fallback": False,
                "output": "markdown",
                "startPage": start_page + 1,
                "endPage": None if end_page is None else end_page + 1,
                "imageUrls": image_urls,
            }


runtime = OcrRuntime()
