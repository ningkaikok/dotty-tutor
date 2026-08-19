"""External capability adapters: LLM, OCR, review and TTS."""

from infrastructure.runtime.model_runtime import ModelRuntime, Provider, runtime
from infrastructure.runtime.ocr_runtime import OcrProvider, OcrRuntime, runtime as ocr_runtime
from infrastructure.runtime.contracts import RuntimeConfigSnapshot, RuntimeExecutionError
from infrastructure.runtime.review_runtime import ReviewRuntime, runtime_reviewer

__all__ = [
    "ModelRuntime",
    "Provider",
    "runtime",
    "OcrProvider",
    "OcrRuntime",
    "RuntimeConfigSnapshot",
    "RuntimeExecutionError",
    "ocr_runtime",
    "ReviewRuntime",
    "runtime_reviewer",
]
