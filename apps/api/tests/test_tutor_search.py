import unittest

from persistence.search_store import build_search_text, cjk_bigrams


class TutorSearchTests(unittest.TestCase):
    def test_cjk_bigrams_and_search_text(self) -> None:
        self.assertEqual(cjk_bigrams("一次函数"), ["一次", "次函", "函数"])
        text = build_search_text(title="一次函数", chapter="代数", knowledge_points=["斜率"], body="求直线解析式")
        self.assertIn("函数", text)
        self.assertIn("斜率", text)
