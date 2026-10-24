"""Private remote storage for recoverable application files.

Logical file keys stay in PostgreSQL; bytes are split into small immutable
objects so a Supabase Free bucket's 50 MB per-object cap never reduces the
application's existing upload limit. The local files remain the working cache.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx2
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Engine

from persistence.database import decode_json
from persistence.schema import managed_file_objects

CHUNK_BYTES = 4 * 1024 * 1024
MAX_REMOTE_OBJECT_BYTES = 50 * 1024 * 1024
_SAFE_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


class FileStorageError(RuntimeError):
    """A remote binary could not be saved or restored without losing integrity."""


class FileStorageConfigurationError(FileStorageError):
    """Remote storage was requested without a complete, safe configuration."""


def _safe_parts(value: str) -> tuple[str, ...]:
    """Validate internal logical keys before they reach paths or REST URLs."""
    if not value or "\\" in value or "\x00" in value or value.startswith("/"):
        raise ValueError("文件标识无效")
    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("文件标识无效")
    return parts


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _local_cache_key(stat: os.stat_result, manifest_sha256: str) -> tuple[int, int, int, int, str]:
    """Bind fast local verification to both file identity and its current manifest."""
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, manifest_sha256


class FileAssetStore:
    """Persist and restore private uploads through the official Storage REST API."""

    def __init__(
        self,
        *,
        engine: Engine,
        data_root: str | Path,
        backend: str | None = None,
        supabase_url: str | None = None,
        service_role_key: str | None = None,
        bucket: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.engine = engine
        self.root = Path(data_root).expanduser().resolve()
        self.upload_root = self.root / "uploads"
        self.mistake_root = self.root / "mistakes"
        selected_backend = (backend or os.getenv("DOTTY_FILE_STORAGE_BACKEND", "local")).strip().lower()
        if selected_backend not in {"local", "supabase"}:
            raise FileStorageConfigurationError("DOTTY_FILE_STORAGE_BACKEND 只支持 local 或 supabase")
        self.backend = selected_backend
        self.bucket = (bucket or os.getenv("DOTTY_SUPABASE_STORAGE_BUCKET", "dotty-private")).strip()
        self.base_url = (supabase_url or os.getenv("DOTTY_SUPABASE_URL", "")).strip().rstrip("/")
        self.service_role_key = service_role_key or os.getenv("DOTTY_SUPABASE_SERVICE_ROLE_KEY", "")
        self._client = client
        self._owns_client = client is None
        self._bucket_verified = False
        self._bucket_lock = threading.Lock()
        self._verified_local: dict[str, tuple[int, int, int, int, str]] = {}
        self._namespace_versions: dict[str, str] = {}

        if self.backend == "supabase":
            parsed = urlparse(self.base_url)
            local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if not self.base_url or not parsed.netloc or parsed.query or parsed.fragment or not (
                parsed.scheme == "https" or local_http
            ):
                raise FileStorageConfigurationError("Supabase 存储地址必须使用 HTTPS（本机测试可用 localhost HTTP）")
            if not self.service_role_key.strip():
                raise FileStorageConfigurationError("已启用远端文件存储，但服务端 Storage 凭据未配置")
            if not _SAFE_BUCKET.fullmatch(self.bucket):
                raise FileStorageConfigurationError("远端文件存储桶名称无效")
            if self._client is None:
                self._client = httpx2.Client(timeout=30.0, follow_redirects=False)

    @property
    def enabled(self) -> bool:
        """Whether managed file metadata and remote bytes are enabled."""
        return self.backend == "supabase"

    def close(self) -> None:
        """Close the HTTP pool when this store owns it."""
        if self._owns_client and self._client is not None:
            self._client.close()

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/storage/v1/{path}"

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Normalize transport errors without retaining request headers or credentials."""
        assert self._client is not None
        try:
            return self._client.request(method, url, **kwargs)
        except Exception as error:
            raise FileStorageError(f"Supabase 文件服务连接失败（{type(error).__name__}）") from error

    def _verify_private_bucket(self) -> None:
        if self._bucket_verified:
            return
        with self._bucket_lock:
            if self._bucket_verified:
                return
            assert self._client is not None
            response = self._request(
                "GET", self._url(f"bucket/{quote(self.bucket, safe='')}"),
                headers=self._headers(),
            )
            if response.status_code != 200:
                raise FileStorageError(f"Supabase 私有存储桶检查失败（HTTP {response.status_code}）")
            try:
                bucket_data = response.json()
            except (ValueError, json.JSONDecodeError) as error:
                raise FileStorageError("Supabase 存储桶检查返回了无效响应") from error
            if not isinstance(bucket_data, dict) or bucket_data.get("public") is not False:
                raise FileStorageConfigurationError("远端存储桶必须已存在并配置为私有")
            self._bucket_verified = True

    def _local_path(self, root: Path, relative: str, *, must_exist: bool) -> Path:
        parts = _safe_parts(relative)
        if root.is_symlink():
            raise FileStorageError("托管文件根目录不能是符号链接")
        base = root.resolve()
        candidate = base.joinpath(*parts)
        current = base
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise FileStorageError("托管文件路径不能包含符号链接")
        resolved = candidate.resolve(strict=must_exist)
        try:
            resolved.relative_to(base)
        except ValueError as error:
            raise FileStorageError("托管文件路径超出受管目录") from error
        return resolved

    def _upload_key(self, upload_id: str, path: Path) -> tuple[str, Path]:
        upload_parts = _safe_parts(upload_id)
        if len(upload_parts) != 1:
            raise ValueError("上传标识无效")
        directory = self._local_path(self.upload_root, upload_id, must_exist=False)
        candidate = Path(os.path.abspath(path.expanduser()))
        try:
            relative = candidate.relative_to(directory).as_posix()
        except ValueError as error:
            raise FileStorageError("教材文件必须位于该上传的受管目录内") from error
        local_path = self._local_path(self.upload_root, f"{upload_id}/{relative}", must_exist=path.exists())
        return f"uploads/{upload_id}/{relative}", local_path

    def _mistake_key(self, learner_id: str, mistake_id: str, path: Path) -> tuple[str, Path]:
        if not learner_id or len(learner_id) > 128 or "\x00" in learner_id:
            raise ValueError("学生身份无效")
        mistake_parts = _safe_parts(mistake_id)
        if len(mistake_parts) != 1:
            raise ValueError("错题标识无效")
        directory = self._local_path(self.mistake_root, mistake_id, must_exist=False)
        candidate = Path(os.path.abspath(path.expanduser()))
        try:
            relative = candidate.relative_to(directory).as_posix()
        except ValueError as error:
            raise FileStorageError("错题文件必须位于该错题的受管目录内") from error
        local_path = self._local_path(self.mistake_root, f"{mistake_id}/{relative}", must_exist=path.exists())
        learner_partition = hashlib.sha256(learner_id.encode("utf-8")).hexdigest()[:24]
        return f"mistakes/{learner_partition}/{mistake_id}/{relative}", local_path

    def persist_upload_file(self, upload_id: str, path: Path, *, content_type: str | None = None) -> dict[str, Any]:
        """Persist a source, OCR result, question image, or other upload-owned file."""
        key, safe_path = self._upload_key(upload_id, path)
        return self._persist(key, safe_path, content_type=content_type)

    def restore_upload_file(self, upload_id: str, path: Path) -> Path:
        """Validate a cached upload file or recover it from the private bucket."""
        key, safe_path = self._upload_key(upload_id, path)
        return self._restore(key, safe_path)

    def get_upload_file_manifest(self, upload_id: str, relative_path: str) -> dict[str, str | int] | None:
        """Read a file's recorded hash/size without restoring or contacting object storage."""
        upload_parts = _safe_parts(upload_id)
        relative_parts = _safe_parts(relative_path)
        if len(upload_parts) != 1:
            raise ValueError("上传标识无效")
        row = self._read_manifest(f"uploads/{upload_id}/{'/'.join(relative_parts)}")
        if row is None:
            return None
        return {"sha256": str(row["content_sha256"]), "size": int(row["size_bytes"])}

    def restore_upload_namespace(self, upload_id: str, directory: Path) -> int:
        """Restore all previously persisted assets for one upload into this cache."""
        upload_parts = _safe_parts(upload_id)
        if len(upload_parts) != 1:
            raise ValueError("上传标识无效")
        prefix = f"uploads/{upload_id}/"
        rows = self._list_manifests(prefix)
        restored = 0
        root = self._local_path(self.upload_root, upload_id, must_exist=False)
        target_root = directory.expanduser().resolve(strict=False)
        if target_root != root:
            raise FileStorageError("上传目录必须与当前实例的 DOTTY_DATA_DIR 一致")
        digest = hashlib.sha256()
        for row in rows:
            digest.update(f"{row['file_key']}:{row['content_sha256']}:{row['size_bytes']}:{row['updated_at']}\n".encode())
        version = digest.hexdigest()
        if self._namespace_versions.get(prefix) == version and all(
            self._cached_upload_file_is_current(prefix, row, root) for row in rows
        ):
            return 0
        for row in rows:
            key = str(row["file_key"])
            relative = key.removeprefix(prefix)
            safe_path = self._local_path(self.upload_root, f"{upload_id}/{relative}", must_exist=False)
            self._restore_row(row, safe_path)
            restored += 1
        self._namespace_versions[prefix] = version
        return restored

    def _cached_upload_file_is_current(self, prefix: str, row: dict[str, Any], root: Path) -> bool:
        key = str(row["file_key"])
        relative = key.removeprefix(prefix)
        path = self._local_path(self.upload_root, f"{root.name}/{relative}", must_exist=False)
        if path.is_symlink() or not path.is_file():
            return False
        stat = path.stat()
        return self._verified_local.get(key) == (
            stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, str(row["content_sha256"]),
        )

    def persist_upload_namespace(self, upload_id: str, directory: Path) -> int:
        """Persist every stable file produced by this upload before it is marked complete."""
        if not self.enabled:
            return 0
        upload_parts = _safe_parts(upload_id)
        if len(upload_parts) != 1:
            raise ValueError("上传标识无效")
        root = self._local_path(self.upload_root, upload_id, must_exist=True)
        if directory.expanduser().resolve(strict=True) != root:
            raise FileStorageError("上传目录必须与当前实例的 DOTTY_DATA_DIR 一致")
        files: list[Path] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise FileStorageError("上传目录中出现符号链接，已停止远端持久化")
            if path.is_file() and not path.name.endswith((".part", ".partial")):
                files.append(path)
        for path in files:
            self.persist_upload_file(upload_id, path)
        return len(files)

    def persist_mistake_file(
        self, learner_id: str, mistake_id: str, path: Path, *, content_type: str | None = None,
    ) -> dict[str, Any]:
        """Persist a student-owned original photo or OCR-created image asset."""
        key, safe_path = self._mistake_key(learner_id, mistake_id, path)
        return self._persist(key, safe_path, content_type=content_type)

    def restore_mistake_file(self, learner_id: str, mistake_id: str, path: Path) -> Path:
        """Validate or recover one mistake image without exposing its remote key."""
        key, safe_path = self._mistake_key(learner_id, mistake_id, path)
        return self._restore(key, safe_path)

    def _persist(self, file_key: str, path: Path, *, content_type: str | None) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "local_only", "size": path.stat().st_size}
        self._verify_private_bucket()
        if path.is_symlink() or not path.is_file():
            raise FileStorageError("待持久化文件不存在或不是普通文件")
        before = path.stat()
        file_digest, size = _sha256(path)
        content_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        previous = self._read_manifest(file_key)
        if (
            previous
            and previous.get("content_sha256") == file_digest
            and int(previous.get("size_bytes") or -1) == size
            and previous.get("content_type") == content_type
        ):
            self._verified_local[file_key] = _local_cache_key(before, file_digest)
            return {"status": "persisted", "sha256": file_digest, "size": size}
        namespace = hashlib.sha256(file_key.encode("utf-8")).hexdigest()[:24]
        chunks: list[dict[str, Any]] = []
        with path.open("rb") as stream:
            index = 0
            while True:
                data = stream.read(CHUNK_BYTES)
                if not data and (size != 0 or index > 0):
                    break
                chunk_digest = hashlib.sha256(data).hexdigest()
                object_path = f"private/{namespace}/{file_digest}/{index:06d}-{chunk_digest}"
                self._upload_and_verify(object_path, data, content_type)
                chunks.append({"path": object_path, "sha256": chunk_digest, "size": len(data)})
                index += 1
                if not data:
                    break
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise FileStorageError("文件在远端持久化期间发生变化，请重试")
        if sum(int(item["size"]) for item in chunks) != size:
            raise FileStorageError("文件分块大小校验失败")
        manifest = {"version": 1, "bucket": self.bucket, "chunkSize": CHUNK_BYTES, "chunks": chunks}
        now = time.time()
        statement = postgresql_insert(managed_file_objects).values(
            file_key=file_key,
            content_sha256=file_digest,
            size_bytes=size,
            content_type=content_type,
            manifest_json=manifest,
            created_at=now,
            updated_at=now,
        ).on_conflict_do_update(
            index_elements=[managed_file_objects.c.file_key],
            set_={
                "content_sha256": file_digest,
                "size_bytes": size,
                "content_type": content_type,
                "manifest_json": manifest,
                "updated_at": now,
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)
        self._verified_local[file_key] = _local_cache_key(after, file_digest)
        self._namespace_versions.clear()
        return {"status": "persisted", "sha256": file_digest, "size": size}

    def _upload_and_verify(self, object_path: str, data: bytes, content_type: str) -> None:
        if len(data) > MAX_REMOTE_OBJECT_BYTES:
            raise FileStorageError("远端对象超过 Supabase 单对象上限")
        assert self._client is not None
        encoded = quote(object_path, safe="/")
        upload = self._request(
            "POST", self._url(f"object/{quote(self.bucket, safe='')}/{encoded}"),
            content=data,
            headers={**self._headers(content_type=content_type), "x-upsert": "false"},
        )
        if upload.status_code not in {200, 201}:
            if upload.status_code not in {400, 409}:
                raise FileStorageError(f"Supabase 文件上传失败（HTTP {upload.status_code}）")
        downloaded = self._request(
            "GET", self._url(f"object/authenticated/{quote(self.bucket, safe='')}/{encoded}"),
            headers=self._headers(),
        )
        if downloaded.status_code != 200:
            raise FileStorageError(f"Supabase 文件回读校验失败（HTTP {downloaded.status_code}）")
        if downloaded.content != data:
            raise FileStorageError("Supabase 文件回读内容与上传内容不一致")

    def _read_manifest(self, file_key: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(managed_file_objects).where(
                managed_file_objects.c.file_key == file_key,
            )).mappings().first()
        return dict(row) if row else None

    def _list_manifests(self, prefix: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(managed_file_objects).where(
                managed_file_objects.c.file_key.startswith(prefix, autoescape=True),
            ).order_by(managed_file_objects.c.file_key)).mappings().all()
        return [dict(row) for row in rows]

    def _restore(self, file_key: str, path: Path) -> Path:
        if not self.enabled:
            return path
        row = self._read_manifest(file_key)
        if row is None:
            if path.is_file() and not path.is_symlink():
                return path
            raise FileNotFoundError("受管文件没有可恢复的远端持久化记录")
        self._restore_row(row, path)
        return path

    def _restore_row(self, row: dict[str, Any], path: Path) -> None:
        file_key = str(row["file_key"])
        if path.is_symlink():
            raise FileStorageError("本地缓存不能是符号链接")
        if path.is_file():
            status = path.stat()
            verified = self._verified_local.get(file_key)
            expected_digest = str(row["content_sha256"])
            if verified == _local_cache_key(status, expected_digest):
                return
            digest, size = _sha256(path)
            if digest != expected_digest or size != int(row["size_bytes"]):
                raise FileStorageError("本地文件指纹与持久化记录不一致，文件可能已损坏")
            self._verified_local[file_key] = _local_cache_key(status, expected_digest)
            return

        self._verify_private_bucket()
        manifest = decode_json(row["manifest_json"])
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("bucket") != self.bucket:
            raise FileStorageError("远端文件清单无效或存储桶不匹配")
        chunks = manifest.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise FileStorageError("远端文件清单缺少分块")

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        digest = hashlib.sha256()
        written = 0
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".partial", delete=False) as output:
                temporary = Path(output.name)
                assert self._client is not None
                for chunk in chunks:
                    if not isinstance(chunk, dict):
                        raise FileStorageError("远端文件分块清单无效")
                    object_path = str(chunk.get("path") or "")
                    object_parts = _safe_parts(object_path)
                    if (
                        len(object_parts) != 4
                        or object_parts[0] != "private"
                        or not re.fullmatch(r"[0-9a-f]{24}", object_parts[1])
                        or not re.fullmatch(r"[0-9a-f]{64}", object_parts[2])
                        or not re.fullmatch(r"\d{6}-[0-9a-f]{64}", object_parts[3])
                    ):
                        raise FileStorageError("远端对象路径不符合受管格式")
                    expected_chunk_hash = str(chunk.get("sha256") or "")
                    expected_chunk_size = int(chunk.get("size", -1))
                    if not re.fullmatch(r"[0-9a-f]{64}", expected_chunk_hash) or not 0 <= expected_chunk_size <= CHUNK_BYTES:
                        raise FileStorageError("远端分块指纹或大小无效")
                    response = self._request(
                        "GET", self._url(f"object/authenticated/{quote(self.bucket, safe='')}/{quote(object_path, safe='/')}"),
                        headers=self._headers(),
                    )
                    if response.status_code != 200:
                        raise FileStorageError(f"远端文件分块暂不可用（HTTP {response.status_code}）")
                    block = response.content
                    if len(block) != expected_chunk_size or hashlib.sha256(block).hexdigest() != expected_chunk_hash:
                        raise FileStorageError("远端文件分块指纹不一致，文件可能已损坏")
                    output.write(block)
                    digest.update(block)
                    written += len(block)
                output.flush()
                os.fsync(output.fileno())
            if written != int(row["size_bytes"]) or digest.hexdigest() != row["content_sha256"]:
                raise FileStorageError("恢复后的文件指纹与持久化记录不一致")
            os.replace(temporary, path)
            temporary = None
            stat = path.stat()
            self._verified_local[file_key] = _local_cache_key(stat, str(row["content_sha256"]))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def migrate_upload_directory(self, upload_id: str, directory: Path) -> dict[str, int]:
        """Persist existing ordinary files under one upload; never delete originals."""
        self._require_remote()
        root = self._local_path(self.upload_root, upload_id, must_exist=True)
        if directory.expanduser().resolve(strict=True) != root:
            raise FileStorageError("迁移目录与当前上传的受管目录不一致")
        files: list[Path] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise FileStorageError("上传目录中出现符号链接，已停止本机文件迁移")
            if path.is_file() and not path.name.endswith(".partial"):
                files.append(path)
        total_bytes = sum(path.stat().st_size for path in files)
        for path in files:
            self.persist_upload_file(upload_id, path)
        return {"files": len(files), "bytes": total_bytes}

    def _require_remote(self) -> None:
        if not self.enabled:
            raise FileStorageConfigurationError("显式迁移本机文件前必须启用 Supabase 文件存储")


__all__ = [
    "CHUNK_BYTES",
    "FileAssetStore",
    "FileStorageConfigurationError",
    "FileStorageError",
    "MAX_REMOTE_OBJECT_BYTES",
]
