"""User scenarios for frozen region assets, unit coverage and publication adapters."""
from __future__ import annotations

import unittest

from application.services.courseware import CoursewareService
from application.services.courseware_publication import CoursewarePublicationAdapter


class CoursewareAssetScenarios(unittest.TestCase):
    def test_user_uses_a_saved_region_then_receives_a_crop_with_frozen_coordinates(self) -> None:
        # Given a frozen physical source page with a real normalized region
        source = {"sourceRevisionId": "revision", "uploadId": "upload", "sourceFileSha256": "a" * 64}
        page = {"page": 3, "regions": [{"regionId": "figure-1", "x": .1, "y": .2, "width": .4, "height": .3}]}
        # When the source catalogue is prepared
        assets = CoursewareService._source_assets(source, [page])
        # Then the region is distinct from the whole page and pins original identity
        crop = next(item for item in assets if item["kind"] == "source_image")
        self.assertEqual(crop["sourceRegionId"], "figure-1")
        self.assertEqual(crop["sourceFileSha256"], "a" * 64)
        self.assertEqual(crop["crop"], {"x": .1, "y": .2, "width": .4, "height": .3})
        self.assertEqual(crop["sourceRefs"][0]["regionIds"], ["figure-1"])

    def test_user_has_no_trusted_coordinates_then_receives_an_explicit_whole_page_fallback(self) -> None:
        # Given a legacy source region whose coordinates were never saved
        source = {"sourceRevisionId": "revision", "uploadId": "upload", "sourceFileSha256": "a" * 64}
        # When the catalogue is prepared
        assets = CoursewareService._source_assets(source, [{"page": 3, "regions": [{"regionId": "unknown"}]}])
        # Then no coordinates are invented and only the labelled original page is served
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["kind"], "source_page")
        self.assertEqual(assets[0]["fallbackReason"], "missing_trusted_region")

    def test_user_downloads_a_region_then_the_png_is_cropped_and_forged_coordinates_are_rejected(self) -> None:
        # Given a rasterized engineering page and saved source coordinates
        import copy
        import struct
        import tempfile
        from pathlib import Path

        import cairosvg

        from application.services.courseware_assets import (
            crop_source_asset,
            source_assets,
        )
        source = {"sourceRevisionId": "revision", "uploadId": "upload", "sourceFileSha256": "a" * 64}
        page = {"page": 3, "regions": [{"regionId": "figure", "x": .1, "y": .2, "width": .4, "height": .3}]}
        crop = next(asset for asset in source_assets(source, [page]) if asset["kind"] == "source_image")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "page.png"
            cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>', write_to=str(path))
            # When delivering a trusted region, Then dimensions match the saved crop and reuse is deterministic
            source["pages"] = [page]
            result = crop_source_asset(path, source, 3, crop)
            self.assertEqual(struct.unpack(">II", result.read_bytes()[16:24]), (40, 30))
            self.assertEqual(crop_source_asset(path, source, 3, crop), result)
            forged = copy.deepcopy(crop)
            forged["crop"]["width"] = .8
            with self.assertRaisesRegex(ValueError, "坐标"):
                crop_source_asset(path, source, 3, forged)

    def test_user_requests_voice_on_a_teaching_block_then_teacher_answers_are_excluded(self) -> None:
        # Given a published page with private teacher notes and an activity answer
        page = {"title": "Observe", "teacherNotes": "SECRET", "revealSteps": [{"content": "SECRET"}],
                "blocks": [{"blockId": "teach", "kind": "text", "text": "Compare the diagram."},
                           {"blockId": "answer", "kind": "activity", "answerKey": "SECRET"}]}
        # When the adapter selects a public teaching block
        text = CoursewarePublicationAdapter.block_text(page["blocks"][0])
        # Then private facilitation and assessments never enter the tutoring prompt
        self.assertEqual(text, "Compare the diagram.")
        self.assertEqual(CoursewarePublicationAdapter.block_text(page["blocks"][1]), "")


if __name__ == "__main__":
    unittest.main()
