from __future__ import annotations

import unittest

from publication_routes import _public_lesson


class PublicationBoundaryTests(unittest.TestCase):
    def test_student_projection_hides_studio_diagnostics(self) -> None:
        public = _public_lesson({
            "lessonId": "lesson-1",
            "title": "一次方程",
            "version": 1,
            "status": "published",
            "knowledgePoints": ["移项"],
            "blocks": [],
            "questionPayload": {
                "question": {
                    "id": "lesson-1",
                    "prompt": "解方程",
                    "publicationStatus": "ready",
                    "sourceArtifactUrl": "/private/source.md",
                    "promptArtifactUrl": "/private/prompt.md",
                },
                "lessonSteps": [],
                "architecture": {"source": "internal"},
                "review": {"status": "reviewed"},
                "quality": {"status": "ready"},
                "modelRun": {"provider": "codex", "model": "secret"},
            },
            "guideCards": [{"hint": "内部提示"}],
        })
        payload = public["questionPayload"]
        self.assertNotIn("review", payload)
        self.assertNotIn("quality", payload)
        self.assertNotIn("sourceArtifactUrl", payload["question"])
        self.assertNotIn("promptArtifactUrl", payload["question"])
        self.assertEqual(payload["modelRun"]["provider"], "published")
        self.assertEqual(public["guideCards"], [])


if __name__ == "__main__":
    unittest.main()
