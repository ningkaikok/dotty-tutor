"""OCR orchestration shared by textbook and mistake imports.

The module deliberately wraps MinerU instead of embedding subprocess details in
HTTP routes. ``ocr_runtime`` remains the provider adapter; this file only owns
manual-text precedence, fallback policy, and observability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader

from observability import log_event
from ocr_runtime import runtime as ocr_runtime


def extract_pdf_text(reader: PdfReader, max_pages: int = 10, max_chars: int = 16_000) -> str:
    """Read a bounded PDF text layer without failing the whole import."""
    pages: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            pages.append(text)
        if sum(len(item) for item in pages) >= max_chars:
            break
    return "\n\n".join(pages)[:max_chars]


def resolve_ocr_text(
    source_text: str,
    extracted_text: str = "",
    source_path: Path | None = None,
    start_page: int = 0,
    end_page: int | None = None,
    asset_dir: Path | None = None,
    asset_url_prefix: str = "",
) -> tuple[str, dict[str, Any]]:
    """Resolve one source into text and a provider audit record.

    Manual text wins because it is useful for deterministic demos. MinerU is
    attempted next; the PDF text layer is the last non-model fallback.
    """
    if source_text.strip():
        log_event("ocr.completed", provider="manual", mode="pasted-text", fallback=False)
        return source_text.strip(), {
            "requestedProvider": "manual",
            "provider": "manual",
            "mode": "pasted-text",
            "fallback": False,
            "output": "text",
        }

    requested = ocr_runtime.selection.provider
    if source_path and ocr_runtime.should_use_mineru():
        log_event(
            "ocr.started",
            provider=requested,
            start_page=start_page + 1,
            end_page=None if end_page is None else end_page + 1,
        )
        try:
            result = ocr_runtime.parse(
                source_path,
                start_page,
                end_page,
                asset_dir,
                asset_url_prefix,
            )
            log_event("ocr.completed", provider=result[1].get("provider"), fallback=False)
            return result
        except Exception as error:
            log_event(
                "ocr.failed",
                level=40,
                provider=requested,
                fallback=bool(extracted_text),
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            return extracted_text, {
                "requestedProvider": requested,
                "provider": "pypdf" if extracted_text else "none",
                "mode": "text-layer-fallback" if extracted_text else "ocr-failed",
                "fallback": True,
                "error": str(error),
                "output": "text",
            }

    log_event(
        "ocr.completed",
        provider="pypdf" if extracted_text else "none",
        mode="text-layer" if extracted_text else "no-text-layer",
        fallback=False,
    )
    return extracted_text, {
        "requestedProvider": requested,
        "provider": "pypdf" if extracted_text else "none",
        "mode": "text-layer" if extracted_text else "no-text-layer",
        "fallback": False,
        "output": "text",
    }
