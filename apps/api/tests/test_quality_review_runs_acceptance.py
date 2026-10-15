"""PostgreSQL user-behavior acceptance for bounded quality-review comparisons."""

from __future__ import annotations

import threading
import unittest.mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.job_worker import JobWorker
from application.quality_review_runs_jobs import build_quality_review_runs_registry
from application.services.quality_review import QualityReviewService
from application.services.quality_review_runs import QualityReviewRunsService
from infrastructure.runtime.model_runtime import ModelRuntime
from persistence.job_store import JobStore
from persistence.quality_review_runs_store import QualityReviewRunsStore
from persistence.quality_review_store import QualityReviewStore
from routers.quality_review_routes import build_quality_review_router
from routers.quality_review_runs_routes import build_quality_review_runs_router
from tests.postgres_test_support import PostgresTestCase


class QualityReviewRunsAcceptanceTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        assert self.database.engine is not None
        self.review_store = QualityReviewStore(database_url=self.database_url, data_root=str(self.data_root))
        self.runs_store = QualityReviewRunsStore(engine=self.database.engine)
        self.jobs = JobStore(database_url=self.database_url)
        self.addCleanup(self.review_store.close)
        self.addCleanup(self.runs_store.close)
        self.addCleanup(self.jobs.close)
        self.runtime = ModelRuntime()
        # Discovery is an external boundary too: developer CLI installations,
        # API credentials and local Ollama availability must not alter acceptance.
        self.runtime_catalog = unittest.mock.patch.object(self.runtime, "providers", return_value=[
            {"id": "codex", "label": "Codex", "available": True,
             "models": ["default"], "detail": "test provider boundary"},
            {"id": "deepseek", "label": "DeepSeek", "available": True,
             "models": ["deepseek-flash"], "detail": "test provider boundary"},
        ])
        self.runtime_catalog.start()
        self.addCleanup(self.runtime_catalog.stop)
        self.runtime_call = unittest.mock.patch.object(
            self.runtime, "generate_json_as", side_effect=self.model_result
        )
        self.runtime_call.start()
        self.addCleanup(self.runtime_call.stop)
        self.review_service = QualityReviewService(self.review_store)
        self.runs_service = QualityReviewRunsService(
            store=self.runs_store,
            review_store=self.review_store,
            runtime=self.runtime,
            jobs=self.jobs,
        )
        self.worker = JobWorker(
            self.jobs,
            build_quality_review_runs_registry(self.runs_service),
            worker_id="quality-review-tests",
            lease_seconds=10,
        )
        app = FastAPI()
        app.state.auth_mode = "demo"
        app.include_router(build_quality_review_router(self.review_service))
        app.include_router(build_quality_review_runs_router(self.runs_service))
        self.client = self.enterContext(TestClient(app))

    @staticmethod
    def packet(*, spoof_gold_fields: bool = False) -> dict:
        input_value = {"ocrText": "EXAMPLE 2.2\nSolve the equation."}
        if spoof_gold_fields:
            input_value.update({
                "sourceKind": "user_authored",
                "humanReviewed": True,
                "counted": True,
                "taskDimension": "error_localization",
                "redactedInput": {"question": "Solve x + 2 = 5"},
                "rubric": {"exactAnswer": True},
                "strata": {"subject": "math", "gradeBand": "middle", "difficulty": "easy"},
                "redaction": "No identifiers present",
            })
        return {
            "title": "OpenStax model comparison pilot",
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
                "input": input_value,
                "expected": {"questionNumbers": [], "requiredSourceFragments": ["EXAMPLE 2.2"]},
                "actual": {"method": "source_replay"},
            }],
        }

    @staticmethod
    def model_result(provider: str, model: str, *_: object, **__: object):
        # This replaces only the genuine model-provider boundary. Its JSON shape
        # is the contracted output for the frozen example case.
        return (
            {"questionNumbers": [], "requiredSourceFragments": ["EXAMPLE 2.2"]},
            {
                "actualProvider": provider,
                "actualModel": model,
                "durationMs": 1.0,
                "usage": {"promptTokens": None, "outputTokens": None},
            },
        )

    def create_case(self, *, spoof_gold_fields: bool = False) -> str:
        imported = self.client.post(
            "/api/quality-reviews/packages", json=self.packet(spoof_gold_fields=spoof_gold_fields)
        )
        self.assertEqual(imported.status_code, 201, imported.text)
        package_id = imported.json()["packageId"]
        case_id = self.client.get(f"/api/quality-reviews/packages/{package_id}/cases").json()["items"][0]["caseId"]
        return case_id

    def create_payload(self, case_id: str) -> dict:
        return {
            "caseIds": [case_id],
            "taskInstruction": "按案例输入提取题号和必须保留的原文片段。",
            "baseline": {"provider": "codex", "model": "default"},
            "candidate": {"provider": "deepseek", "model": "deepseek-flash"},
            "perCallTimeoutSeconds": 8,
            "totalBudgetSeconds": 30,
            "maxOutputTokens": 160,
        }

    def test_user_runs_blinded_models_rates_one_frozen_case_and_refreshes_durable_results(self) -> None:
        # Given a source case and two configured real model selections
        case_id = self.create_case()

        # When the user starts one bounded comparison, then the shared Worker executes it
        created = self.client.post("/api/quality-review-runs", json=self.create_payload(case_id))
        self.assertEqual(created.status_code, 201, created.text)
        run_id = created.json()["runId"]
        self.assertEqual(created.json()["status"], "queued")
        queued_job = self.jobs.get_job(run_id)
        self.assertIsNotNone(queued_job)
        self.assertEqual(queued_job["maxAttempts"], 1)  # type: ignore[index]
        self.assertEqual(queued_job["attemptCount"], 0)  # type: ignore[index]
        self.worker.run_once()

        # And the user reloads the saved result
        view = self.client.get(f"/api/quality-review-runs/{run_id}").json()

        # Then the run is durable, version-pinned, and both successful outputs remain anonymous before rating
        self.assertEqual(view["status"], "completed")
        self.assertTrue(view["blind"])
        self.assertIsNone(view["models"])
        saved_case = view["cases"][0]
        self.assertEqual(saved_case["recordVersion"], 1)
        self.assertEqual(saved_case["snapshot"]["inputFingerprint"], saved_case["inputFingerprint"])
        self.assertNotIn("armAModelSlot", saved_case)
        self.assertNotIn("requestedModel", str(saved_case["arms"]))
        self.assertNotIn("actualModel", str(saved_case["arms"]))
        self.assertEqual({arm["status"] for arm in saved_case["arms"].values()}, {"succeeded"})

        # When the user rates the anonymous pair
        rating = self.client.post(f"/api/quality-review-runs/{run_id}/ratings", json={
            "caseId": case_id,
            "aPass": True,
            "bPass": False,
            "preferred": "A",
            "rationale": "A 保留了所有题号和来源片段。",
        })

        # Then only this server-derived, unverified demo identity is recorded and its exploratory result is explicit
        self.assertEqual(rating.status_code, 200, rating.text)
        rated_view = rating.json()
        self.assertFalse(rated_view["blind"])
        self.assertEqual(rated_view["ratings"][0]["actorId"], "demo:unverified")
        self.assertFalse(rated_view["ratings"][0]["identityVerified"])
        self.assertEqual(set(rated_view["models"]), {"baseline", "candidate"})
        self.assertIn("actualModel", rated_view["cases"][0]["arms"]["A"])
        summary = self.client.get(f"/api/quality-review-runs/{run_id}/summary").json()
        self.assertFalse(summary["formalRankingAvailable"])
        self.assertEqual(summary["exploratoryPairedBinary"]["sampleCount"], 1)

        duplicate = self.client.post(f"/api/quality-review-runs/{run_id}/ratings", json={
            "caseId": case_id,
            "aPass": True,
            "bPass": False,
            "preferred": "A",
            "rationale": "重复提交不能形成第二位评分者。",
        })
        self.assertEqual(duplicate.status_code, 409)

        persisted = self.runs_store.get_run(run_id)
        assert persisted is not None
        self.assertIsNone(persisted["cases"][0]["arms"]["A"]["cost"])
        self.assertIsNone(persisted["cases"][0]["arms"]["A"]["promptTokens"])

    def test_user_given_embedding_only_local_tag_when_comparing_models_then_catalog_and_run_validation_exclude_it(self) -> None:
        # Given a local provider that exposes both an embedding model and a text-generation model
        case_id = self.create_case()
        providers = [
            {
                "id": "ollama", "label": "Ollama", "available": True,
                "models": ["nomic-embed-text:latest", "qwen2.5:7b"], "detail": "local",
            },
            {
                "id": "codex", "label": "Codex", "available": True,
                "models": ["default"], "detail": "configured",
            },
            {
                "id": "deepseek", "label": "DeepSeek", "available": False,
                "models": ["deepseek-flash"], "detail": "not configured",
            },
        ]

        # When the user opens the catalog and submits an embedding-only baseline
        with unittest.mock.patch.object(self.runtime, "providers", return_value=providers):
            catalog = self.client.get("/api/quality-review-runs/models")
            self.assertEqual(catalog.status_code, 200, catalog.text)
            local = next(item for item in catalog.json()["providers"] if item["provider"] == "ollama")

            invalid_payload = self.create_payload(case_id)
            invalid_payload["baseline"] = {"provider": "ollama", "model": "nomic-embed-text:latest"}
            rejected = self.client.post("/api/quality-review-runs", json=invalid_payload)

            valid_payload = self.create_payload(case_id)
            valid_payload["baseline"] = {"provider": "ollama", "model": "qwen2.5:7b"}
            valid_payload["candidate"] = {"provider": "codex", "model": "default"}
            accepted = self.client.post("/api/quality-review-runs", json=valid_payload)

        # Then discovery and server validation use the same generation-capability boundary
        self.assertEqual(local["models"], ["qwen2.5:7b"])
        self.assertTrue(local["available"])
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("可生成文本的模型", rejected.json()["detail"])
        self.assertEqual(accepted.status_code, 201, accepted.text)

    def test_user_cancels_an_inflight_call_without_releasing_the_database_run_slot(self) -> None:
        # Given a provider call already in flight for the single durable run slot
        case_id = self.create_case()
        entered = threading.Event()
        release = threading.Event()

        def blocked_model(provider: str, model: str, *_: object, **__: object):
            entered.set()
            release.wait(5)
            return self.model_result(provider, model)

        with unittest.mock.patch.object(self.runtime, "generate_json_as", side_effect=blocked_model):
            created = self.client.post("/api/quality-review-runs", json=self.create_payload(case_id))
            self.assertEqual(created.status_code, 201, created.text)
            run_id = created.json()["runId"]
            worker_results: list[dict] = []
            worker_thread = threading.Thread(target=lambda: worker_results.append(self.worker.run_once() or {}))
            worker_thread.start()
            self.assertTrue(entered.wait(3), "model call did not reach the external boundary")

            # When the user cancels while the transport is still active
            cancelled = self.client.post(f"/api/quality-review-runs/{run_id}/cancel")
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertTrue(cancelled.json()["run"]["cancelRequested"])
            second = self.client.post("/api/quality-review-runs", json=self.create_payload(case_id))

            # Then the running sample retains the slot and a second model run cannot overlap it
            self.assertEqual(second.status_code, 409)
            self.assertEqual(self.runs_store.get_run(run_id)["status"], "running")  # type: ignore[index]
            release.set()
            worker_thread.join(5)
            self.assertFalse(worker_thread.is_alive(), "shared Worker did not finish after the provider returned")
            self.assertEqual(worker_results[0]["status"], "cancelled")
            saved = self.runs_store.get_run(run_id)

        # A canceled provider response is preserved as a non-ratable sample, and the lock is released only at completion
        assert saved is not None
        self.assertEqual(saved["status"], "cancelled")
        self.assertEqual(saved["cases"][0]["arms"]["A"]["status"], "cancelled")
        self.assertEqual(self.runs_store.request_cancel(run_id), False)

    def test_user_refreshing_a_queued_run_does_not_start_or_retry_its_model_job(self) -> None:
        # Given a newly enqueued run that is still waiting for the shared Worker
        case_id = self.create_case()
        created = self.client.post("/api/quality-review-runs", json=self.create_payload(case_id))
        self.assertEqual(created.status_code, 201, created.text)
        run_id = created.json()["runId"]

        # When the user refreshes the run detail several times
        first = self.client.get(f"/api/quality-review-runs/{run_id}")
        second = self.client.get(f"/api/quality-review-runs/{run_id}")

        # Then GET stays read-only and the durable job remains eligible for its one Worker attempt
        self.assertEqual(first.json()["status"], "queued")
        self.assertEqual(second.json()["status"], "queued")
        job = self.jobs.get_job(run_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "queued")  # type: ignore[index]
        self.assertEqual(job["attemptCount"], 0)  # type: ignore[index]

    def test_user_cancels_a_queued_run_and_both_durable_states_converge(self) -> None:
        # Given a persisted run and its not-yet-claimed shared Job
        case_id = self.create_case()
        created = self.client.post("/api/quality-review-runs", json=self.create_payload(case_id))
        self.assertEqual(created.status_code, 201, created.text)
        run_id = created.json()["runId"]

        # When the user cancels before a Worker claims it
        response = self.client.post(f"/api/quality-review-runs/{run_id}/cancel")

        # Then the business run and the shared job are both terminal and the single-run slot is released
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["run"]["status"], "cancelled")
        self.assertEqual(self.jobs.get_job(run_id)["status"], "cancelled")  # type: ignore[index]
        self.assertIsNone(self.runs_store.get_active_run_id())

    def test_user_sees_failed_arms_saved_without_a_retry_or_rating_option(self) -> None:
        # Given a frozen case whose external model responses do not match the required output shape
        case_id = self.create_case()
        created = self.client.post("/api/quality-review-runs", json=self.create_payload(case_id))
        self.assertEqual(created.status_code, 201, created.text)
        run_id = created.json()["runId"]
        with unittest.mock.patch.object(
            self.runtime,
            "generate_json_as",
            return_value=({}, {"actualProvider": "codex", "actualModel": "default", "usage": {}}),
        ):
            self.worker.run_once()

        # When the user reloads the run and attempts to score the failed pair
        run_view = self.client.get(f"/api/quality-review-runs/{run_id}").json()
        rating = self.client.post(f"/api/quality-review-runs/{run_id}/ratings", json={
            "caseId": case_id,
            "aPass": False,
            "bPass": False,
            "preferred": "neither",
            "rationale": "两个模型响应都不符合结构要求。",
        })

        # Then both samples remain inspectable as failed, no retry is queued, and ratings are rejected
        self.assertEqual(run_view["status"], "completed")
        self.assertEqual({arm["status"] for arm in run_view["cases"][0]["arms"].values()}, {"failed"})
        self.assertEqual(self.jobs.get_job(run_id)["attemptCount"], 1)  # type: ignore[index]
        self.assertEqual(rating.status_code, 409)

    def test_user_given_an_unavailable_provider_when_starting_comparison_then_no_run_is_created(self) -> None:
        # Given provider discovery reports an unconfigured generation provider
        case_id = self.create_case()
        providers = [
            {"id": "codex", "label": "Codex", "available": False,
             "models": ["default"], "detail": "not configured"},
        ]
        # When the teacher submits a comparison using that provider
        with unittest.mock.patch.object(self.runtime, "providers", return_value=providers):
            response = self.client.post("/api/quality-review-runs", json=self.create_payload(case_id))
        # Then discovery isolation does not weaken the real availability gate
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("codex 当前不可用", response.json()["detail"])
        self.assertEqual(self.client.get("/api/quality-review-runs").json()["items"], [])

    def test_user_cannot_turn_public_source_or_client_claims_into_human_gold(self) -> None:
        # Given a public-source packet that includes forged human-review labels and benchmark fields
        self.create_case(spoof_gold_fields=True)

        # When an authorized teacher requests the formal export
        response = self.client.get("/api/quality-review-runs/gold-export")

        # Then server provenance wins, no fake count is reported, and no JSONL is offered
        self.assertEqual(response.status_code, 200, response.text)
        export = response.json()
        self.assertFalse(export["ready"])
        self.assertEqual(export["countedCases"], 0)
        self.assertIsNone(export["jsonl"])
        self.assertIn("公开教材", export["blockingCases"][0]["reason"])
