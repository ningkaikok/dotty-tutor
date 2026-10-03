"""Observable source evidence segmentation for textbook text layers."""

import unittest

from domain.chapters.source import sentences


class ChapterSentenceTests(unittest.TestCase):
    def test_decimal_equations_and_wrapped_prose_keep_their_full_sentence_evidence(self) -> None:
        source = 'For x=0.5, y=2.5. These\ninfluences include film and poetry. A memoir means “memory.”'
        result = sentences(source, 12, "revision")
        self.assertEqual([item["text"] for item in result], [
            'For x=0.5, y=2.5.', 'These\ninfluences include film and poetry.', 'A memoir means “memory.”',
        ])
        self.assertTrue(all(item["text"] in source for item in result))

    def test_blank_paragraphs_keep_separate_locators_without_sentence_punctuation(self) -> None:
        result = sentences('Linear equations\n\nSolve x+2=5', 2, "revision")
        self.assertEqual([item["text"] for item in result], ['Linear equations', 'Solve x+2=5'])
