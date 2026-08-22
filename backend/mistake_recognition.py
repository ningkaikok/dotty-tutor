"""复用教材 OCR/出题管线识别单张错题图的适配层。

刻意不单独实现错题解析：教材管线里的切分、公式规范化和质量门禁已经过真实
教材校准，错题图片本质上是"只有一道题的一页教材"。单独写一套规则必然与主
管线漂移——同样的 OCR 文本在两边切出不同结果，学生会看到互相矛盾的题目。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def build_mistake_recognizer(
    *,
    resolve_ocr_text: Callable[..., tuple[str, dict[str, Any]]],
    generate_lesson: Callable[[str], tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]],
    build_content_blocks: Callable[[dict[str, Any], str, list[str]], list[dict[str, Any]]],
) -> Callable[[Path, str, Path, str], tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]]:
    def recognize(
        source_path: Path,
        source_text: str,
        asset_dir: Path,
        asset_url_prefix: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        lesson_source, ocr_run = resolve_ocr_text(
            source_text,
            source_path=source_path,
            asset_dir=asset_dir,
            asset_url_prefix=asset_url_prefix,
        )
        payload, guide_cards, model_run = generate_lesson(lesson_source)
        question = payload["question"]
        references = question.pop("imageReferences", [])
        available = {
            Path(url).name: url
            for url in ocr_run.get("imageUrls", [])
            if isinstance(url, str) and url.startswith("/api/mistakes/")
        }
        question["imageUrls"] = [
            available[Path(reference).name]
            for reference in references
            if Path(reference).name in available
        ]
        question["contentBlocks"] = build_content_blocks(payload, lesson_source, references)
        return payload, guide_cards, ocr_run, model_run

    return recognize
