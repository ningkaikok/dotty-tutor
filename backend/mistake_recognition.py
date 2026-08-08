"""Adapter that reuses the textbook OCR/generation pipeline for one mistake."""

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
