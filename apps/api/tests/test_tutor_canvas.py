import unittest

from domain.tutoring.canvas import evaluate_point_placement, normalize_point_placement


class TutorCanvasTests(unittest.TestCase):
    def test_point_is_normalized_and_evaluated(self) -> None:
        state = normalize_point_placement({"kind": "point-placement", "points": [{"id": "student", "x": 2, "y": 3}], "operations": []})
        result = evaluate_point_placement(state, target={"x": 2, "y": 3})
        self.assertIsNotNone(result)
        self.assertEqual(result["assessment"], "correct")

    def test_invalid_canvas_is_not_an_answer(self) -> None:
        self.assertEqual(normalize_point_placement({"kind": "point-placement", "points": "bad"})["points"], [])
