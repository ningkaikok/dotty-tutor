"""Use-case facade for textbook upload and batch processing.

The implementation lives beside this facade while callers use this stable
application-layer name.  Keeping this seam explicit makes the future Worker
migration possible without changing API routes again.
"""

from application.services.textbook_processing import (
    PDF_BATCH_PAGES,
    TextbookProcessingService,
)

__all__ = ["PDF_BATCH_PAGES", "TextbookProcessingService"]
