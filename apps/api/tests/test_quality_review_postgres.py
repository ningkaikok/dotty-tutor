"""PostgreSQL acceptance tests for source review import, retrieval and decisions."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.services.chapter_source_preview import file_hash
from application.services.quality_review import QualityReviewService
from persistence.quality_review_store import QualityReviewStore
from routers.quality_review_routes import build_quality_review_router
from tests.postgres_test_support import PostgresTestCase


class QualityReviewPostgresAcceptanceTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = QualityReviewStore(database_url=self.database_url, data_root=str(self.data_root))
        self.addCleanup(self.store.close)
        self.source_root = self.data_root / "sources"
        self.source_root.mkdir()
        self.source_pdf = self.source_root / "ElementaryAlgebra2e-WEB.pdf"
        self.source_pdf.write_bytes(_pdf_with_text("Verified source quote for the test."))
        self.source_hash = file_hash(self.source_pdf)

        app = FastAPI()
        app.state.auth_mode = "demo"
        app.include_router(build_quality_review_router(
            QualityReviewService(self.store, source_root=self.source_root)
        ))
        self.client = self.enterContext(TestClient(app))

    @staticmethod
    def packet() -> dict:
        return {
            "title": "OpenStax source review pilot",
            "source": {"sourceTitle": "Elementary Algebra 2e"},
            "cases": [{
                "id": "math-p214-example-2-2",
                "subject": "math",
                "sourcePage": 214,
                "source": {
                    "url": "https://openstax.org/details/books/elementary-algebra-2e",
                    "license": "CC BY 4.0",
                    "version": "2e",
                    "contentSha256": "a" * 64,
                },
                "sourceLocator": {"quote": "Solve the equation and check the result."},
                "input": {"ocrText": "EXAMPLE 2.2\nSolve the equation."},
                "expected": {"questionNumbers": [], "requiredSourceFragments": ["EXAMPLE 2.2"]},
                "actual": {"method": "real_source_replay_pending_human_review", "checks": {"questionNumbersMatch": True}},
            }],
        }

    def test_user_imports_a_source_package_and_reviews_its_original_case_snapshot(self) -> None:
        # Given an OpenStax page case with source, expectation and observed output fingerprints
        created = self.client.post("/api/quality-reviews/packages", json=self.packet())
        self.assertEqual(created.status_code, 201, created.text)
        package_id = created.json()["packageId"]

        # When the teacher opens the package list and case detail
        packages = self.client.get("/api/quality-reviews/packages").json()["items"]
        listed = self.client.get(f"/api/quality-reviews/packages/{package_id}/cases").json()
        case_id = listed["items"][0]["caseId"]
        detail = self.client.get(f"/api/quality-reviews/cases/{case_id}").json()

        # Then the immutable source/output are visible and the case stays pending
        self.assertEqual(packages[0]["sourceKind"], "real_open_material")
        self.assertFalse(packages[0]["source"]["counted"])
        self.assertEqual(detail["sourcePage"], 214)
        self.assertEqual(detail["sourceVerification"], "pending_managed_asset")
        self.assertEqual(detail["reviewStatus"], "pending_source_verification")
        self.assertEqual(detail["actual"]["method"], "real_source_replay_pending_human_review")
        self.assertEqual(detail["counted"], False)

    def test_user_opens_an_original_page_from_a_server_managed_source_pdf(self) -> None:
        # Given a source case linked to the server-managed PDF and its actual SHA-256
        packet = self.packet()
        packet["cases"][0]["sourcePage"] = 1
        packet["cases"][0]["source"].update({
            "sourceAssetId": "openstax-elementary-algebra-2e",
            "contentSha256": self.source_hash,
        })
        packet["cases"][0]["sourceLocator"]["quote"] = "Verified source quote for the test."
        created = self.client.post("/api/quality-reviews/packages", json=packet)
        self.assertEqual(created.status_code, 201, created.text)
        case_id = self.client.get(
            f"/api/quality-reviews/packages/{created.json()['packageId']}/cases"
        ).json()["items"][0]["caseId"]

        # When the reviewer asks the API to display that exact original page
        response = self.client.get(f"/api/quality-reviews/cases/{case_id}/source-page")

        # Then the service returns a real image while the human decision stays pending
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else "")
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_user_records_a_provisional_decision_without_claiming_source_acceptance_or_gold(self) -> None:
        # Given a case whose original page asset is not yet registered
        created = self.client.post("/api/quality-reviews/packages", json=self.packet()).json()
        case_id = self.client.get(f"/api/quality-reviews/packages/{created['packageId']}/cases").json()["items"][0]["caseId"]

        # When the user records a decision and supplies a display name
        response = self.client.post(f"/api/quality-reviews/cases/{case_id}/decisions", json={
            "expectedRecordVersion": 1,
            "decision": "accepted",
            "rationale": "初步检查了已展示的引用和输出摘要，等待原页确认。",
            "verifiedQuote": "Solve the equation and check the result.",
            "displayName": "Reviewer A",
        })

        # Then the durable opinion remains provisional, unverified and uncounted
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["reviewStatus"], "decision_recorded_pending_source")
        self.assertEqual(result["decisions"][0]["stage"], "provisional")
        self.assertEqual(result["decisions"][0]["actorId"], "demo:unverified")
        self.assertFalse(result["decisions"][0]["identityVerified"])
        self.assertEqual(result["decisions"][0]["verifiedQuote"], "")
        self.assertEqual(result["decisions"][0]["claimedQuote"], "Solve the equation and check the result.")
        self.assertEqual(result["counted"], False)

    def test_user_cannot_change_source_kind_counted_flag_or_supply_a_server_path(self) -> None:
        # Given an import payload attempting to elevate source trust and choose a local path
        packet = self.packet()
        packet["source"]["counted"] = True
        packet["cases"][0]["source"]["file"] = "/private/tmp/secret.pdf"

        # When the import endpoint validates the payload
        response = self.client.post("/api/quality-reviews/packages", json=packet)

        # Then no case is imported and the API rejects client-selected trust or paths
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/quality-reviews/packages").json()["items"], [])

    def test_user_cannot_overwrite_a_case_with_a_stale_decision(self) -> None:
        # Given the same server-derived actor already decided frozen record version 1
        created = self.client.post("/api/quality-reviews/packages", json=self.packet()).json()
        case_id = self.client.get(f"/api/quality-reviews/packages/{created['packageId']}/cases").json()["items"][0]["caseId"]
        payload = {"expectedRecordVersion": 1, "decision": "needs_changes", "rationale": "先补来源页。"}
        self.assertEqual(self.client.post(f"/api/quality-reviews/cases/{case_id}/decisions", json=payload).status_code, 200)

        # When the same browser changes its display name and tries to count as a second reviewer
        second_name = {**payload, "displayName": "Another name"}
        stale = self.client.post(f"/api/quality-reviews/cases/{case_id}/decisions", json=second_name)

        # Then the server actor key rejects the duplicate and the case version stays frozen
        self.assertEqual(stale.status_code, 409)
        current = self.client.get(f"/api/quality-reviews/cases/{case_id}").json()
        self.assertEqual(current["recordVersion"], 1)
        self.assertEqual(len(current["decisions"]), 1)

    def test_user_can_submit_a_final_single_review_only_after_managed_page_and_quote_match(self) -> None:
        # Given a managed OpenStax test PDF whose page contains the source locator
        packet = self.packet()
        packet["cases"][0]["sourcePage"] = 1
        packet["cases"][0]["source"].update({
            "sourceAssetId": "openstax-elementary-algebra-2e",
            "contentSha256": self.source_hash,
        })
        packet["cases"][0]["sourceLocator"]["quote"] = "Verified source quote for the test."
        created = self.client.post("/api/quality-reviews/packages", json=packet)
        self.assertEqual(created.status_code, 201, created.text)
        case_id = self.client.get(
            f"/api/quality-reviews/packages/{created.json()['packageId']}/cases"
        ).json()["items"][0]["caseId"]

        # When the reviewer quotes the same text from the pinned original page
        response = self.client.post(f"/api/quality-reviews/cases/{case_id}/decisions", json={
            "expectedRecordVersion": 1,
            "decision": "accepted",
            "rationale": "我已查看原页与当前实际输出，记录单人教材决定。",
            "verifiedQuote": "Verified source quote for the test.",
        })

        # Then the single review is final for its exact version, while never becoming gold
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["reviewStatus"], "human_accepted")
        self.assertEqual(result["sourceVerification"], "human_quote_verified")
        self.assertEqual(result["sourceVerifiedHash"], self.source_hash)
        self.assertEqual(result["decisions"][0]["stage"], "final")
        self.assertTrue(result["decisions"][0]["verifiedQuote"])
        self.assertEqual(result["counted"], False)

    def test_user_can_append_a_new_case_revision_without_rewriting_old_decisions(self) -> None:
        # Given an opinion already belongs to the original frozen case version
        created = self.client.post("/api/quality-reviews/packages", json=self.packet()).json()
        case_id = self.client.get(
            f"/api/quality-reviews/packages/{created['packageId']}/cases"
        ).json()["items"][0]["caseId"]
        self.client.post(f"/api/quality-reviews/cases/{case_id}/decisions", json={
            "expectedRecordVersion": 1, "decision": "needs_changes", "rationale": "保留旧意见。"
        })

        # When the reviewer explicitly creates a corrected output version
        response = self.client.post(f"/api/quality-reviews/cases/{case_id}/revisions", json={
            "expectedRecordVersion": 1,
            "input": {"ocrText": "updated input"},
            "expected": {"questionNumbers": ["EXAMPLE 2.2"]},
            "actual": {"questionNumbers": ["EXAMPLE 2.2"]},
        })

        # Then a new immutable snapshot is current and the prior decision stays on version 1
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["recordVersion"], 2)
        self.assertEqual([item["recordVersion"] for item in result["revisions"]], [1, 2])
        self.assertEqual(result["revisions"][1]["createdBy"], "demo:unverified")
        self.assertEqual(result["decisions"][0]["reviewedRecordVersion"], 1)
        self.assertEqual(result["reviewStatus"], "pending_source_verification")


def _pdf_with_text(text: str) -> bytes:
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("ascii")
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    document.extend(b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:]))
    document.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(document)
