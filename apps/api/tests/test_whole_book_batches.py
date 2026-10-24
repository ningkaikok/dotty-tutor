"""Business boundaries for physical-page batching without PostgreSQL."""

from __future__ import annotations

import unittest
from math import ceil

from pydantic import ValidationError

from application.services.whole_book import MAX_WHOLE_BOOK_BATCHES, whole_book_batches
from domain.contracts.whole_book import (
    WholeBookBatchView,
    WholeBookPageDisposition,
    WholeBookRunView,
)


class WholeBookBatchBoundaryTests(unittest.TestCase):
    def test_user_chapter_starts_belong_to_the_following_batch(self) -> None:
        """Given OCR chapter starts, When a split is chosen, Then its heading page stays in the next batch."""
        pages = [
            {"page": page, "text": f"Chapter {index}: Topic"}
            for index, page in enumerate((26, 56, 86), start=1)
        ]

        batches = whole_book_batches(100, pages, "Geometry")

        self.assertEqual(
            [(item["pageStart"], item["pageEnd"]) for item in batches],
            [(1, 25), (26, 55), (56, 85), (86, 100)],
        )
        self.assertEqual([item["chapterHeading"] for item in batches], [None, "Chapter 1: Topic", "Chapter 2: Topic", "Chapter 3: Topic"])
        self.assertTrue(all(batch["pageEnd"] < next_batch["pageStart"] for batch, next_batch in zip(batches, batches[1:])))

    def test_user_773_page_book_keeps_exact_coverage_across_26_batches(self) -> None:
        """Given a 773-page textbook with frequent headings, When batching, Then all pages appear once in ceil(773/30) batches."""
        pages = [
            {"page": page, "text": f"Chapter {index}: Topic"}
            for index, page in enumerate(range(2, 773, 10), start=1)
        ]

        batches = whole_book_batches(773, pages, "OpenStax Writing Guide")
        page_numbers = [
            page_number
            for item in batches
            for page_number in range(item["pageStart"], item["pageEnd"] + 1)
        ]

        self.assertEqual(len(batches), ceil(773 / 30))
        self.assertEqual(len(batches), 26)
        self.assertEqual(page_numbers, list(range(1, 774)))
        self.assertTrue(all(item["pageEnd"] - item["pageStart"] < 30 for item in batches))

    def test_user_1000_page_book_with_dense_chapter_starts_stays_within_34_batches(self) -> None:
        """Given the 1000-page limit and very frequent headings, When batching, Then ceil(1000/30) batches cover every page once."""
        pages = [
            {"page": page, "text": f"Chapter {index}: Topic"}
            for index, page in enumerate(range(2, 1000, 2), start=1)
        ]

        batches = whole_book_batches(1000, pages, "Science")
        page_numbers = [
            page_number
            for item in batches
            for page_number in range(item["pageStart"], item["pageEnd"] + 1)
        ]

        self.assertEqual(len(batches), ceil(1000 / 30))
        self.assertEqual(len(batches), MAX_WHOLE_BOOK_BATCHES)
        self.assertEqual(len(batches), 34)
        self.assertEqual(page_numbers, list(range(1, 1001)))
        self.assertTrue(all(item["pageEnd"] - item["pageStart"] < 30 for item in batches))

    def test_user_rejects_a_1001_page_book(self) -> None:
        """Given a book beyond the advertised bound, When batching, Then it is rejected before work can be planned."""
        with self.assertRaisesRegex(ValueError, "1–1000"):
            whole_book_batches(1001, [], "Too Long")

    def test_user_contract_accepts_page_773_and_1000_but_rejects_1001(self) -> None:
        """Given the source-page ledger boundary, When validating pages, Then only 1..1000 are accepted."""
        for page in (773, 1000):
            self.assertEqual(
                WholeBookPageDisposition(page=page, disposition="covered").page,
                page,
            )
        with self.assertRaises(ValidationError):
            WholeBookPageDisposition(page=1001, disposition="covered")
        with self.assertRaises(ValidationError):
            WholeBookBatchView(
                batchIndex=0, title="Out of range", pageStart=1001, pageEnd=1001,
                status="queued",
            )

    def test_user_run_contract_accepts_34_batch_limits_and_rejects_overflow(self) -> None:
        """Given the maximum run shape, When validating progress, Then page and batch caps match the service."""
        batches = [
            WholeBookBatchView(
                batchIndex=index,
                title=f"Batch {index + 1}",
                pageStart=index * 30 + 1,
                pageEnd=min(1000, (index + 1) * 30),
                status="queued",
            )
            for index in range(34)
        ]
        run_payload = {
            "runId": "run", "uploadId": "upload", "filename": "book.pdf", "status": "queued",
            "totalPages": 1000, "completedPages": 1000, "completedBatches": 34,
            "batchCount": 34, "currentBatchIndex": 33, "batches": batches,
            "createdAt": 1, "updatedAt": 1,
        }
        run = WholeBookRunView(**run_payload)
        self.assertEqual(len(run.batches), 34)

        for overflow in (
            {"totalPages": 1001},
            {"completedBatches": 35},
            {"batchCount": 35},
            {"currentBatchIndex": 34},
            {"batches": [*batches, batches[-1]]},
        ):
            with self.subTest(overflow=overflow):
                with self.assertRaises(ValidationError):
                    WholeBookRunView(**(run_payload | overflow))

    def test_user_unaligned_book_keeps_complete_non_overlapping_page_coverage(self) -> None:
        """Given a 73-page book without OCR headings, When batched, Then each physical page appears once."""
        batches = whole_book_batches(73, [], "Biology")

        self.assertEqual(
            [(item["pageStart"], item["pageEnd"]) for item in batches],
            [(1, 30), (31, 60), (61, 73)],
        )
        self.assertEqual(
            [page for item in batches for page in range(item["pageStart"], item["pageEnd"] + 1)],
            list(range(1, 74)),
        )


if __name__ == "__main__":
    unittest.main()
