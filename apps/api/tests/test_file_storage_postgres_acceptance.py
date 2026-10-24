"""User acceptance for private file persistence across isolated API caches."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from application.services.textbook_processing import TextbookProcessingService
from infrastructure.files.file_storage import FileStorageError
from infrastructure.files.upload_registry import UploadRegistry
from persistence.app_store import AppStore
from persistence.mistake_store import MistakeStore, mistake_items
from persistence.schema import managed_file_objects
from routers.mistake_routes import build_mistake_router
from tests.postgres_test_support import PostgresTestCase

SERVICE_KEY = "test-storage-service-key"
STORAGE_ENV = (
    "DOTTY_FILE_STORAGE_BACKEND",
    "DOTTY_SUPABASE_URL",
    "DOTTY_SUPABASE_SERVICE_ROLE_KEY",
    "DOTTY_SUPABASE_STORAGE_BUCKET",
)


@contextmanager
def textbook_api_client(store: AppStore, registry: UploadRegistry) -> Iterator[TestClient]:
    """Run the real textbook routes with isolated database and file adapters."""
    import routers.textbook_routes as textbook_routes

    names = ("store", "upload_registry", "upload_job", "upload_status", "update_upload_job", "pdf_uploads")
    previous = {name: getattr(textbook_routes, name) for name in names}
    textbook_routes.store = store
    textbook_routes.upload_registry = registry
    textbook_routes.upload_job = registry.get
    textbook_routes.upload_status = registry.status
    textbook_routes.update_upload_job = registry.update
    textbook_routes.pdf_uploads = registry.uploads
    app = FastAPI()
    app.include_router(textbook_routes.router)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        for name, value in previous.items():
            setattr(textbook_routes, name, value)


class PrivateStorageServer(HTTPServer):
    objects: dict[str, bytes]
    requests: list[str]
    fail_uploads: bool

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, StorageHandler)
        self.objects = {}
        self.requests = []
        self.fail_uploads = False


class StorageHandler(BaseHTTPRequestHandler):
    server: PrivateStorageServer

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _authorized(self) -> bool:
        return self.headers.get("apikey") == SERVICE_KEY and self.headers.get("Authorization") == f"Bearer {SERVICE_KEY}"

    def _send(self, status: int, content: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    @staticmethod
    def _object_key(path: str) -> str | None:
        parts = [unquote(part) for part in urlsplit(path).path.split("/") if part]
        if len(parts) < 5 or parts[:3] != ["storage", "v1", "object"]:
            return None
        if parts[3] != "dotty-private":
            return None
        return "/".join(parts[4:])

    def do_GET(self) -> None:
        self.server.requests.append(self.path)
        if not self._authorized():
            self._send(403, b"{}")
            return
        path = urlsplit(self.path).path
        if path == "/storage/v1/bucket/dotty-private":
            self._send(200, b'{"id":"dotty-private","public":false}')
            return
        if not path.startswith("/storage/v1/object/authenticated/"):
            self._send(404, b"{}")
            return
        key = self._object_key(path.replace("/authenticated", "", 1))
        if key is None or key not in self.server.objects:
            self._send(404, b"{}")
            return
        self._send(200, self.server.objects[key], "application/octet-stream")

    def do_POST(self) -> None:
        self.server.requests.append(self.path)
        if not self._authorized():
            self._send(403, b"{}")
            return
        if self.server.fail_uploads:
            self._send(503, b"{}")
            return
        key = self._object_key(self.path)
        if key is None:
            self._send(404, b"{}")
            return
        if key in self.server.objects:
            self._send(409, b"{}")
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.objects[key] = body
        self._send(200, b'{"Key":"stored"}')


class FileStorageAcceptanceTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.remote = PrivateStorageServer(("127.0.0.1", 0))
        self.server_thread = threading.Thread(target=self.remote.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.previous_storage_env = {name: os.environ.get(name) for name in STORAGE_ENV}
        os.environ.update({
            "DOTTY_FILE_STORAGE_BACKEND": "supabase",
            "DOTTY_SUPABASE_URL": f"http://127.0.0.1:{self.remote.server_port}",
            "DOTTY_SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
            "DOTTY_SUPABASE_STORAGE_BUCKET": "dotty-private",
        })
        self.addCleanup(self._restore_storage_env)

    def _restore_storage_env(self) -> None:
        for name, value in self.previous_storage_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _stop_server(self) -> None:
        self.remote.shutdown()
        self.remote.server_close()
        self.server_thread.join(timeout=2)

    def _store(self, name: str) -> AppStore:
        root = self.data_root / name
        root.mkdir()
        store = AppStore(database_url=self.database_url, data_root=root)
        self.addCleanup(store.close)
        return store

    def test_user_polling_upload_status_does_not_download_the_source_pdf(self) -> None:
        """Given a cold cache, When status is read, Then only metadata is returned without fetching files."""
        # Given a source larger than the recommended small-object upload size plus a page image.
        source_bytes = b"%PDF-1.7\n" + bytes(range(256)) * 24_000
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"recognized-page-image"
        first = self._store("cache-a")
        upload_id = "persisted-upload"
        directory = first.upload_root / upload_id
        asset = directory / "assets" / "batch-001" / "page.png"
        asset.parent.mkdir(parents=True)
        (directory / "source.pdf").write_bytes(source_bytes)
        asset.write_bytes(image_bytes)
        job = {
            "uploadId": upload_id, "filename": "book.pdf", "contentType": "application/pdf",
            "size": len(source_bytes), "chunkSize": 1, "totalChunks": 1, "directory": directory,
            "status": "complete", "progress": 100, "message": "done", "sourceText": "",
            "result": {"sourceFingerprint": hashlib.sha256(source_bytes).hexdigest()},
        }
        first_registry = UploadRegistry(store=first, lesson_store={}, default_guide_cards=[], pdf_tail_check_bytes=1024)
        first_registry.update(job, "complete", 100, "done")

        # When a new API process points at cache B, Then database metadata does not pin cache A.
        self.remote.requests.clear()
        second = self._store("cache-b")
        second_registry = UploadRegistry(store=second, lesson_store={}, default_guide_cards=[], pdf_tail_check_bytes=1024)
        with textbook_api_client(second, second_registry) as client:
            response = client.get(f"/api/uploads/{upload_id}/status")
        self.assertEqual(response.status_code, 200, response.text)
        restored_job = second_registry.get(upload_id)
        assert restored_job is not None
        self.assertEqual(restored_job["directory"], second.upload_root / upload_id)
        self.assertFalse(str(restored_job["directory"]).startswith(str(first.upload_root)))
        restored_pdf = restored_job["directory"] / "source.pdf"
        restored_image = restored_job["directory"] / "assets" / "batch-001" / "page.png"
        self.assertEqual(response.json()["status"], "complete")
        self.assertFalse(restored_pdf.exists())
        self.assertFalse(restored_image.exists())
        manifest = second.file_assets.get_upload_file_manifest(upload_id, "source.pdf")
        assert manifest is not None
        self.assertEqual(manifest["sha256"], hashlib.sha256(source_bytes).hexdigest())
        self.assertFalse(any("/object/authenticated/" in request for request in self.remote.requests))

        # The API's source and image handlers restore only the exact requested object.
        second.file_assets.restore_upload_file(upload_id, restored_pdf)
        second.file_assets.restore_upload_file(upload_id, restored_image)
        self.assertEqual(hashlib.sha256(restored_pdf.read_bytes()).hexdigest(), hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(hashlib.sha256(restored_image.read_bytes()).hexdigest(), hashlib.sha256(image_bytes).hexdigest())
        self.assertTrue(all("/public/" not in request for request in self.remote.requests))

    def test_user_asset_request_restores_only_the_requested_image(self) -> None:
        """Given several remote files, When one image is requested, Then other objects stay cold."""
        first = self._store("single-image-cache-a")
        upload_id = "single-image-upload"
        directory = first.upload_root / upload_id
        (directory / "assets" / "batch-001").mkdir(parents=True)
        source = directory / "source.pdf"
        first_image = directory / "assets" / "batch-001" / "first.png"
        second_image = directory / "assets" / "batch-001" / "second.png"
        source.write_bytes(b"%PDF-1.7\nsource")
        first_image.write_bytes(b"first-question-image")
        second_image.write_bytes(b"second-question-image")
        first.file_assets.persist_upload_namespace(upload_id, directory)
        first.save_job({
            "uploadId": upload_id,
            "filename": "book.pdf",
            "contentType": "application/pdf",
            "size": source.stat().st_size,
            "chunkSize": source.stat().st_size,
            "totalChunks": 1,
            "directory": directory,
            "status": "complete",
            "progress": 100,
            "message": "done",
            "sourceText": "",
            "result": {"sourceFingerprint": hashlib.sha256(source.read_bytes()).hexdigest()},
        })

        second = self._store("single-image-cache-b")
        target = second.upload_root / upload_id / "assets" / "batch-001" / "second.png"
        self.remote.requests.clear()
        registry = UploadRegistry(store=second, lesson_store={}, default_guide_cards=[], pdf_tail_check_bytes=1024)
        with textbook_api_client(second, registry) as client:
            response = client.get(f"/api/uploads/{upload_id}/assets/batch-001/second.png")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"second-question-image")
        object_reads = [request for request in self.remote.requests if "/object/authenticated/" in request]
        self.assertEqual(len(object_reads), 1)
        self.assertEqual(target.read_bytes(), b"second-question-image")
        self.assertFalse((second.upload_root / upload_id / "source.pdf").exists())
        self.assertFalse((second.upload_root / upload_id / "assets" / "batch-001" / "first.png").exists())

    def test_user_sees_failure_when_remote_upload_fails_and_no_manifest_claims_success(self) -> None:
        """Given a healthy local PDF, When private storage is unavailable, Then it stays unmarked."""
        store = self._store("cache-fail")
        upload_id = "failed-upload"
        directory = store.upload_root / upload_id
        directory.mkdir()
        source = directory / "source.pdf"
        source.write_bytes(b"%PDF-1.7\ncontent")
        self.remote.fail_uploads = True

        with self.assertRaisesRegex(FileStorageError, "上传失败"):
            store.file_assets.persist_upload_file(upload_id, source)
        with store.engine.connect() as connection:
            rows = connection.execute(select(managed_file_objects)).mappings().all()
        self.assertEqual(rows, [])
        self.assertEqual(source.read_bytes(), b"%PDF-1.7\ncontent")

    def test_user_receives_a_clear_integrity_error_if_a_remote_chunk_changes(self) -> None:
        """Given a saved manifest, When remote bytes are corrupted, Then cache B stays empty."""
        first = self._store("cache-corrupt-a")
        upload_id = "corrupt-upload"
        directory = first.upload_root / upload_id
        directory.mkdir()
        source_bytes = b"%PDF-1.7\nimmutable source"
        source = directory / "source.pdf"
        source.write_bytes(source_bytes)
        first.file_assets.persist_upload_file(upload_id, source)
        self.assertEqual(len(self.remote.objects), 1)
        object_key = next(iter(self.remote.objects))
        self.remote.objects[object_key] = b"corrupted bytes"

        second = self._store("cache-corrupt-b")
        target = second.upload_root / upload_id
        with self.assertRaisesRegex(FileStorageError, "指纹不一致"):
            second.file_assets.restore_upload_namespace(upload_id, target)
        self.assertFalse((target / "source.pdf").exists())

    def test_user_does_not_read_a_cached_asset_after_another_process_updates_its_manifest(self) -> None:
        """Given cache A verified an image, When cache B replaces its manifest, Then A rejects stale bytes."""
        first = self._store("manifest-cache-a")
        upload_id = "derived-image-upload"
        first_directory = first.upload_root / upload_id / "assets" / "batch-001"
        first_directory.mkdir(parents=True)
        first_image = first_directory / "question.png"
        first_image.write_bytes(b"old-image")
        first.file_assets.persist_upload_file(upload_id, first_image)

        second = self._store("manifest-cache-b")
        second_directory = second.upload_root / upload_id / "assets" / "batch-001"
        second_directory.mkdir(parents=True)
        second_image = second_directory / "question.png"
        second_image.write_bytes(b"new-image")
        second.file_assets.persist_upload_file(upload_id, second_image)

        with self.assertRaisesRegex(FileStorageError, "指纹与持久化记录不一致"):
            first.file_assets.restore_upload_file(upload_id, first_image)

    def test_user_cannot_store_a_file_from_outside_the_upload_root(self) -> None:
        """Given an upload id, When a caller supplies an external path, Then the boundary rejects it."""
        store = self._store("cache-traversal")
        external = self.data_root / "outside.pdf"
        external.write_bytes(b"not an upload")
        with self.assertRaisesRegex(FileStorageError, "必须位于"):
            store.file_assets.persist_upload_file("any-upload", external)

    def test_user_cannot_redirect_managed_roots_through_symlinks(self) -> None:
        """Given managed roots, When either root is replaced by a link, Then persistence rejects it."""
        store = self._store("managed-root-link")
        external_uploads = self.data_root / "external-uploads"
        external_uploads.mkdir()
        store.upload_root.rmdir()
        store.upload_root.symlink_to(external_uploads, target_is_directory=True)
        with self.assertRaisesRegex(FileStorageError, "根目录不能是符号链接"):
            store.file_assets.persist_upload_file(
                "linked-upload", store.upload_root / "linked-upload" / "source.pdf",
            )

        external_mistakes = self.data_root / "external-mistakes"
        external_mistakes.mkdir()
        mistake_root = store.root / "mistakes"
        mistake_root.symlink_to(external_mistakes, target_is_directory=True)
        with self.assertRaisesRegex(FileStorageError, "根目录不能是符号链接"):
            store.file_assets.persist_mistake_file(
                "learner-a", "linked-mistake", mistake_root / "linked-mistake" / "original.png",
            )

    def test_user_upload_is_durable_before_processing_discards_received_chunks(self) -> None:
        """Given a merged PDF, When received chunks are cleaned, Then a new Worker restores the source."""
        first = self._store("early-source-cache-a")
        upload_id = "early-source-upload"
        directory = first.upload_root / upload_id
        directory.mkdir()
        source_bytes = b"%PDF-1.7\nsource-before-ocr"
        chunk_path = directory / "chunk-000000.part"
        chunk_path.write_bytes(source_bytes)
        job = {
            "uploadId": upload_id, "filename": "book.pdf", "contentType": "application/pdf",
            "size": len(source_bytes), "chunkSize": len(source_bytes), "totalChunks": 1,
            "directory": directory, "status": "uploading", "progress": 20, "message": "received",
            "sourceText": "", "result": None,
        }
        first.save_job(job)
        registry = UploadRegistry(store=first, lesson_store={}, default_guide_cards=[], pdf_tail_check_bytes=1024)
        processing = TextbookProcessingService(store=first, upload_registry=registry, ocr_runtime=object())

        source_path, fingerprint = processing._ensure_source_pdf(job, [chunk_path])
        self.assertEqual(fingerprint, hashlib.sha256(source_bytes).hexdigest())
        self.assertTrue(source_path.is_file())
        chunk_path.unlink()

        second = self._store("early-source-cache-b")
        second_registry = UploadRegistry(store=second, lesson_store={}, default_guide_cards=[], pdf_tail_check_bytes=1024)
        restored = second_registry.get(upload_id)
        assert restored is not None
        second_processing = TextbookProcessingService(
            store=second, upload_registry=second_registry, ocr_runtime=object(),
        )
        second_processing._ensure_source_pdf(
            restored, [restored["directory"] / "chunk-000000.part"],
        )
        self.assertEqual((restored["directory"] / "source.pdf").read_bytes(), source_bytes)
        self.assertFalse((restored["directory"] / "chunk-000000.part").exists())

    def test_user_reopens_an_imported_mistake_photo_and_ocr_image_after_cache_loss(self) -> None:
        """Given a learner photo, When cache A is lost, Then the authorized API restores both images."""
        first = self._store("mistake-cache-a")
        mistake_store = MistakeStore(engine=first.engine, data_root=first.root, file_assets=first.file_assets)
        self.addCleanup(mistake_store.close)

        def recognize(_source: Path, source_text: str, asset_directory: Path, _prefix: str):
            asset_directory.mkdir(parents=True, exist_ok=True)
            (asset_directory / "question.png").write_bytes(b"ocr-question-image")
            prompt = source_text or "解方程 x + 1 = 3"
            payload = {
                "question": {
                    "id": "recognized-question", "questionType": "short-answer",
                    "prompt": prompt, "chapter": "一次方程", "knowledgePoint": "移项",
                    "options": [], "imageUrls": [], "contentBlocks": [],
                },
                "lessonSteps": [], "architecture": {},
            }
            return payload, [], {"provider": "fixture"}, {"provider": "fixture"}

        app_a = FastAPI()
        app_a.include_router(build_mistake_router(store=mistake_store, recognize=recognize))
        with TestClient(app_a) as client_a:
            response = client_a.post(
                "/api/mistakes/import",
                files={"file": ("work.jpg", b"original-student-photo", "image/jpeg")},
                data={"learnerId": "learner-a", "sourceText": "解方程 2x = 8"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            mistake = response.json()
        mistake_id = mistake["mistakeId"]
        with first.engine.connect() as connection:
            row = connection.execute(select(mistake_items).where(
                mistake_items.c.mistake_id == mistake_id,
            )).mappings().one()
        self.assertFalse(Path(row["source_image_path"]).is_absolute())

        # When cache A disappears, Then the next API instance reconstructs the learner-scoped files.
        shutil.rmtree(mistake_store.mistake_root / mistake_id)
        second = self._store("mistake-cache-b")
        second_mistake_store = MistakeStore(engine=second.engine, data_root=second.root, file_assets=second.file_assets)
        self.addCleanup(second_mistake_store.close)
        app_b = FastAPI()
        app_b.include_router(build_mistake_router(store=second_mistake_store, recognize=recognize))
        with TestClient(app_b) as client_b:
            original = client_b.get(f"/api/mistakes/{mistake_id}/source")
            page_image = client_b.get(f"/api/mistakes/{mistake_id}/assets/question.png")
        self.assertEqual(original.status_code, 200, original.text)
        self.assertEqual(original.content, b"original-student-photo")
        self.assertEqual(page_image.status_code, 200, page_image.text)
        self.assertEqual(page_image.content, b"ocr-question-image")
        self.assertTrue(all("/public/" not in path for path in self.remote.requests))

    def test_user_cannot_read_mistake_assets_through_a_symlinked_parent_directory(self) -> None:
        """Given local mistake files, When a parent becomes a symlink, Then reads stay inside the data root."""
        backing_store = self._store("local-path-db")
        mistake_root = self.data_root / "local-path-boundary"
        mistake_root.mkdir()
        store = MistakeStore(engine=backing_store.engine, data_root=mistake_root)
        self.addCleanup(store.close)
        mistake_id = "symlink-boundary-case"
        item_directory = store.item_directory(mistake_id)
        source = item_directory / "original.png"
        source.write_bytes(b"student-original")
        store.create({
            "mistakeId": mistake_id,
            "learnerId": "learner-a",
            "sourceFilename": "original.png",
            "contentType": "image/png",
            "sourceImagePath": str(source),
            "sourceImageUrl": f"/api/mistakes/{mistake_id}/source",
            "questionPayload": {"question": {"id": "q-1", "prompt": "一道题"}},
            "guideCards": [],
            "ocrRun": {},
            "modelRun": {},
            "originalAnswer": "",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "方程",
            "knowledgePoint": "移项",
            "status": "pending_confirmation",
            "createdAt": 1.0,
            "updatedAt": 1.0,
        })

        external_assets = self.data_root / "external-mistake-assets"
        external_assets.mkdir()
        (external_assets / "question.png").write_bytes(b"outside-image")
        (item_directory / "assets").symlink_to(external_assets, target_is_directory=True)
        self.assertIsNone(store.asset_path(mistake_id, "question.png"))
        with self.assertRaisesRegex(ValueError, "符号链接"):
            store.persist_capture_files("learner-a", mistake_id, item_directory)

        # Replacing the entire mistake directory also cannot redirect the source-photo route.
        (item_directory / "assets").unlink()
        shutil.rmtree(item_directory)
        external_mistake = self.data_root / "external-mistake-directory"
        external_mistake.mkdir()
        (external_mistake / "original.png").write_bytes(b"outside-original")
        item_directory.symlink_to(external_mistake, target_is_directory=True)
        self.assertIsNone(store.source_path(mistake_id))


__all__ = ["FileStorageAcceptanceTests"]
