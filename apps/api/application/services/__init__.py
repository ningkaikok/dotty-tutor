"""Application service entry points.

The service modules deliberately expose the existing implementations through
narrow names first.  This lets routes migrate independently while preserving
the old top-level imports used by scripts and tests.
"""

from application.services.textbook_processing import TextbookProcessingService

__all__ = ["TextbookProcessingService"]
