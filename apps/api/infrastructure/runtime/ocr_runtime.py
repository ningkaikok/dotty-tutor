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

from infrastructure.runtime.contracts import (
    RuntimeConfigSnapshot,
    attach_runtime_config,
)

OcrProvider = Literal["auto", "mineru", "pypdf"]


@dataclass
class OcrSelection:
    # MinerU 是教材图片/公式识别的默认路径；没有安装时仍保留请求选择，实际执行层
    # 会明确回退到 PDF 文字层，避免 Docker 启动后把宿主机 MinerU 路径误当成可用能力。
    provider: OcrProvider = "mineru"


class OcrRuntime:
    """保存进程级 OCR 选择，并隔离 MinerU 命令行细节。"""

    def __init__(self) -> None:
        configured = os.getenv("OCR_PROVIDER", "mineru").strip().lower()
        self.selection = OcrSelection(
            provider=configured if configured in {"auto", "mineru", "pypdf"} else "mineru"  # type: ignore[arg-type]
        )

    def mineru_command(self) -> Path | None:
        """按显式配置、项目虚拟环境、PATH 的顺序寻找 MinerU。

        本机安装脚本和文档约定的环境在仓库根目录 ``.mineru-venv``；Docker 必须显式
        挂载 Linux MinerU 或接入独立 OCR 服务。
        """
        configured = os.getenv("MINERU_COMMAND", "").strip()
        module_path = Path(__file__).resolve()
        project_root = module_path.parents[3]
        candidates = [
            Path(configured).expanduser() if configured else None,
            project_root / ".mineru-venv" / "bin" / "mineru",
            Path("/opt/mineru/bin/mineru"),
            Path(shutil.which("mineru")) if shutil.which("mineru") else None,
        ]
        return next(
            (path for path in candidates if path and path.is_file() and os.access(path, os.X_OK)),
            None,
        )

    def catalog(self) -> dict:
        command = self.mineru_command()
        # ``selected`` describes the requested provider; ``effective`` is the
        # provider this process can actually execute.  Keep MinerU as the
        # default preference, but make the Docker/minimal-image fallback
        # explicit instead of pretending the host installation is available.
        effective = self.selection.provider
        if effective == "auto":
            effective = "mineru" if command else "pypdf"
        elif effective == "mineru" and not command:
            effective = "pypdf"
        # compose.yaml 同时写入运行模式，/.dockerenv 作为直接运行容器时的兜底。
        # 两者都只用于解释“为什么不可用”，不会把宿主机路径误报成容器内可执行文件。
        in_container = os.getenv("DOTTY_RUNTIME_MODE") == "docker" or Path("/.dockerenv").is_file()
        unavailable_detail = (
            "Docker API 未挂载 MinerU；请使用本机后端，或配置 Linux MinerU/独立 OCR 服务"
            if in_container
            else "未安装：需要独立 Python 3.12 环境和模型"
        )
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
                    "detail": str(command) if command else unavailable_detail,
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

    def config_snapshot(
        self,
        *,
        provider: str | None = None,
        runtime_name: str = "ocr",
        timeout: float = 900.0,
    ) -> RuntimeConfigSnapshot:
        """Return provider/fallback configuration without exposing local paths."""
        return RuntimeConfigSnapshot(
            provider=provider or self.selection.provider,
            model=None,
            runtime=runtime_name,
            schema="ocr-markdown-v1",
            prompt="ocr-pipeline",
            timeout=timeout,
        )

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

    @staticmethod
    def _persist_structured_output(output_dir: str, asset_dir: Path) -> None:
        """把 MinerU 同时产出、目前完全被丢弃的结构化 JSON 落盘保存。

        ``*_content_list.json``（块级结构：type/bbox/page_idx/caption）和
        ``*_middle.json``（行级结构：逐行 bbox、spans）不是每个 MinerU 版本/参数
        组合都一定产出；找不到就跳过，不能因为这两个可选文件而让整个 OCR 失败。
        下游暂时不读取这两个文件，这一步只是把数据留下来，供后续图注归属和换行
        重建复用，本身不改变任何可观察行为。
        """
        for pattern, target_name in (
            ("*_content_list.json", "source.content_list.json"),
            ("*_middle.json", "source.middle.json"),
        ):
            candidates = sorted(
                Path(output_dir).rglob(pattern),
                key=lambda path: path.stat().st_size,
                reverse=True,
            )
            if not candidates:
                continue
            shutil.copy2(candidates[0], asset_dir / target_name)

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
                self._persist_structured_output(output_dir, asset_dir)
            run = {
                "requestedProvider": self.selection.provider,
                "provider": "mineru",
                "mode": "pipeline-auto",
                "fallback": False,
                "output": "markdown",
                "startPage": start_page + 1,
                "endPage": None if end_page is None else end_page + 1,
                "imageUrls": image_urls,
            }
            attach_runtime_config(run, self.config_snapshot(provider="mineru"))
            return markdown[:40_000], run

    def render_page_image(
        self,
        source_path: Path,
        page_number: int,
        asset_dir: Path,
        asset_url_prefix: str,
    ) -> tuple[str, str] | None:
        """把含矢量图的 PDF 页渲染成稳定题图，返回 Markdown 引用和 API URL。

        只有 OCR 已确认页面依赖图形且没有提取出局部图片时才调用。使用系统
        ``pdftoppm`` 而不是把新的 Python 图像依赖塞进 API 虚拟环境。
        """
        command = shutil.which("pdftoppm")
        if not command or page_number < 1:
            return None
        asset_dir.mkdir(parents=True, exist_ok=True)
        basename = f"rendered-page-{page_number:04d}"
        output_prefix = asset_dir / basename
        output_path = asset_dir / f"{basename}.png"
        if not output_path.is_file():
            completed = subprocess.run(
                [command, "-f", str(page_number), "-l", str(page_number), "-png", "-r", "144", str(source_path), str(output_prefix)],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            generated = asset_dir / f"{basename}-{page_number}.png"
            if completed.returncode != 0 or not generated.is_file():
                return None
            generated.replace(output_path)
        return f"images/{output_path.name}", f"{asset_url_prefix}/{output_path.name}"


runtime = OcrRuntime()
