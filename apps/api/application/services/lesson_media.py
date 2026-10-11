"""Source-bound draft, review and publication workflow for lesson videos."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from application.job_worker import (
    CancellableJobResult,
    JobCancelled,
    RetryableJobError,
    TerminalJobError,
)
from application.lesson_media_renderer import (
    LessonMediaRenderer,
    RenderedLessonMedia,
    normalize_speech_text,
)
from infrastructure.runtime.tts_runtime import TtsRuntime, TtsRuntimeError

STORYBOARD_TEMPLATE_VERSION = "lesson-media-storyboard-v1"
NARRATION_TEMPLATE_VERSION = "lesson-media-narration-v1"
INSTRUCT_BY_LANGUAGE = {
    "Chinese": "用耐心、清晰、自然的中文老师语气朗读，语速稍慢，重点处有轻微停顿。",
    "English": "Read in a clear, patient English teacher voice at a measured pace with brief pauses between ideas.",
}
SPEAKER_BY_LANGUAGE = {"Chinese": "Serena", "English": "Ryan"}
JOB_TYPE = "lesson_media.render"
JOB_MAX_ATTEMPTS = 2
JOB_TIMEOUT_SECONDS = 600.0


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_from_block(block: dict[str, Any]) -> str:
    payload = block.get("payload") or {}
    markdown = payload.get("markdown") or payload.get("text") or ""
    return str(markdown).strip()


class LessonMediaService:
    """Orchestrate immutable video versions, TTS, bounded rendering and human gates."""

    def __init__(
        self,
        *,
        chapter_service: Any,
        media_store: Any,
        jobs: Any,
        speech_runtime: Any | None = None,
        renderer: LessonMediaRenderer | None = None,
    ) -> None:
        self.chapter_service = chapter_service
        self.media_store = media_store
        self.jobs = jobs
        self.speech_runtime = speech_runtime or TtsRuntime()
        self.renderer = renderer or LessonMediaRenderer()
        self.artifact_root = Path(media_store.root) / "lesson-media"
        self.working_root = self.artifact_root / ".working"

    def _published_chapter(self, chapter_id: str, publication_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        published = self.chapter_service.get_published(chapter_id, publication_id)
        chapter = self.chapter_service.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("已发布章节不存在")
        publication = next((item for item in chapter.get("publications", []) if item.get("publicationId") == publication_id), None)
        if not publication:
            raise LookupError("发布版本不属于此章节")
        revision_id = publication.get("sourceRevisionId")
        source = next((item for item in chapter.get("sourceRevisions", []) if item.get("sourceRevisionId") == revision_id), None)
        if not source:
            raise ValueError("发布课程找不到来源修订，不能创建视频")
        return published, chapter, source

    @staticmethod
    def _resolve_source_refs(refs: list[dict[str, Any]], source: dict[str, Any]) -> list[dict[str, Any]]:
        pages = {
            page["page"]: page
            for page in source.get("pages", [])
            if isinstance(page.get("page"), int) and not isinstance(page.get("page"), bool)
        }
        resolved: list[dict[str, Any]] = []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("sourceRevisionId") != source["sourceRevisionId"]:
                raise ValueError("分镜引用的来源修订与课程发布版本不一致")
            page_no = ref.get("page")
            if not isinstance(page_no, int) or isinstance(page_no, bool):
                raise ValueError("分镜引用缺少有效来源页码")
            page = pages.get(page_no)
            if page is None:
                raise ValueError("分镜引用的页码无法定位到来源")
            sentence_id = ref.get("sentenceId")
            sentence = next((item for item in page.get("sentences", []) if item.get("sentenceId") == sentence_id), None)
            if sentence is None:
                raise ValueError("分镜引用缺少可验证的原文句子定位")
            resolved.append({
                "sourceRevisionId": source["sourceRevisionId"],
                "page": page_no,
                "sentenceId": sentence_id,
                "regionId": sentence.get("regionId"),
                "quote": sentence["text"],
            })
        if not resolved:
            raise ValueError("已发布讲解缺少来源分镜引用，不能创建视频")
        return resolved

    def _build_storyboard(self, request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        published, chapter, source = self._published_chapter(request["chapterId"], request["publicationId"])
        if published.get("teachingMode") != "tutorial":
            raise ValueError("视频第一版只接受经过审核并发布的教程章节")
        if published.get("subject") not in {"math", "english"}:
            raise ValueError("视频第一版只支持数学和英语章节")
        if published.get("publicationId") != request["publicationId"]:
            raise ValueError("章节发布版本快照不匹配")
        if source.get("sourceRevisionId") != request["sourceRevisionId"]:
            raise ValueError("来源修订与已发布课程不匹配，请刷新后重试")
        lesson = next((item for item in published["lessons"] if item.get("lessonId") == request["lessonId"]), None)
        if not lesson or lesson.get("status") != "published":
            raise LookupError("已发布教程不存在")
        if lesson.get("sourceRevisionId") != request["sourceRevisionId"]:
            raise ValueError("课程讲解使用的来源修订与请求不一致")
        blocks = lesson.get("blocks") or []
        if not 3 <= len(blocks) <= 8:
            raise ValueError("视频分镜需要 3 到 8 个已发布讲解片段")
        source_title = str(source.get("sourceTitle") or "").strip() or None
        source_url = str(source.get("sourceUrl") or "").strip() or None
        attribution = str(source.get("attributionText") or "").strip() or None
        source_host = urlparse(source_url).hostname if source_url else None
        if source_host and (source_host == "openstax.org" or source_host.endswith(".openstax.org")):
            source_title = source_title or "OpenStax"
            attribution = attribution or "Access for free at openstax.org."
        license_name = str(source.get("license") or "").strip()
        if not license_name:
            raise ValueError("教材来源缺少许可记录，不能生成教学视频")
        scenes: list[dict[str, Any]] = []
        source_blocks: list[dict[str, Any]] = []
        for index, block in enumerate(blocks):
            markdown = _text_from_block(block)
            narration = normalize_speech_text(markdown)
            if not narration:
                raise ValueError("已发布讲解片段清理后为空，请修订课程后重试")
            payload = block.get("payload") or {}
            refs = payload.get("sourceRefs") or block.get("sourceRefs") or []
            resolved_refs = self._resolve_source_refs(refs, source)
            scene = {
                "sceneId": uuid.uuid4().hex,
                "order": index + 1,
                "kind": str(payload.get("kind") or block.get("type") or "explanation"),
                "title": str(payload.get("title") or block.get("title") or f"第 {index + 1} 段讲解"),
                "narration": narration,
                "sourceRefs": resolved_refs,
            }
            scenes.append(scene)
            source_blocks.append({
                "blockIndex": index,
                "originalMarkdown": markdown,
                "sourceRefs": resolved_refs,
            })
        if len(scenes) != len(blocks):
            raise ValueError("源讲解和分镜映射不完整")
        source_snapshot = {
            "chapterId": request["chapterId"],
            "chapterTitle": published["title"],
            "publicationId": request["publicationId"],
            "publicationVersion": published["version"],
            "lessonId": request["lessonId"],
            "lessonTitle": lesson["title"],
            "lessonVersion": lesson["version"],
            "sourceRevisionId": request["sourceRevisionId"],
            "sourceVersion": source.get("sourceVersion"),
            "sourceFingerprint": source.get("fingerprint"),
            "sourceFileSha256": source.get("sourceFileSha256"),
            "sourceTitle": source_title,
            "sourceUrl": source_url,
            "attributionText": attribution,
            "license": license_name,
            "pageStart": source.get("pageStart"),
            "pageEnd": source.get("pageEnd"),
            "sourceBlocks": source_blocks,
            "storyboardTemplateVersion": STORYBOARD_TEMPLATE_VERSION,
            "storyboardModel": {"provider": "deterministic-source-transform", "model": None, "promptVersion": None},
        }
        source_snapshot["contentFingerprint"] = _fingerprint({"source": source_snapshot, "scenes": scenes})
        self.renderer.validate_storyboard(
            scenes=scenes,
            source={"chapterTitle": source_snapshot["chapterTitle"], **source_snapshot},
        )
        return source_snapshot, scenes

    @staticmethod
    def _revision_response(row: dict[str, Any], render_job: dict[str, Any] | None) -> dict[str, Any]:
        source = row["sourceSnapshot"]
        return {
            "mediaId": row["mediaId"],
            "seriesId": row["seriesId"],
            "version": row["version"],
            "chapterId": row["chapterId"],
            "publicationId": row["publicationId"],
            "lessonId": row["lessonId"],
            "sourceRevisionId": row["sourceRevisionId"],
            "title": row["title"],
            "subject": row["subject"],
            "license": row["license"],
            "sourceTitle": source.get("sourceTitle"),
            "sourceUrl": source.get("sourceUrl"),
            "attributionText": source.get("attributionText"),
            "distributionScope": row["distributionScope"],
            "sourceSnapshot": source,
            "rendererSnapshot": row["rendererSnapshot"],
            "scenes": row["scenes"],
            "status": row["status"],
            "durationSeconds": row["durationSeconds"],
            "artifactSha256": row["artifactSha256"],
            "consistencyReview": row["consistencyReview"],
            "renderJob": LessonMediaService._job_summary(render_job),
            "createdAt": row["createdAt"],
            "reviews": row.get("reviews", []),
            "publishedAt": row["publishedAt"],
        }

    @staticmethod
    def _job_summary(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if not job:
            return None
        return {key: job[key] for key in ("jobId", "status", "progress", "message", "attemptCount", "maxAttempts", "lastError") if key in job}

    def create_draft(self, request: dict[str, Any], *, created_by: str = "teacher") -> dict[str, Any]:
        source_snapshot, scenes = self._build_storyboard(request)
        published = self.chapter_service.get_published(request["chapterId"], request["publicationId"])
        media_id = uuid.uuid4().hex
        series_id = uuid.uuid4().hex
        source_hash = source_snapshot["contentFingerprint"]
        now = time.time()
        row = self.media_store.create_revision({
            "mediaId": media_id,
            "seriesId": series_id,
            "version": 1,
            "chapterId": request["chapterId"],
            "publicationId": request["publicationId"],
            "lessonId": request["lessonId"],
            "sourceRevisionId": request["sourceRevisionId"],
            "sourceFingerprint": source_hash,
            "title": source_snapshot["lessonTitle"],
            "subject": published["subject"],
            "license": source_snapshot["license"],
            "sourceSnapshot": source_snapshot,
            "scenes": scenes,
            "rendererSnapshot": {
                "implementation": "CairoSVG+FFmpeg",
                "rendererTemplateVersion": "lesson-media-renderer-v1",
                "narrationTemplateVersion": NARRATION_TEMPLATE_VERSION,
                "plannedLanguage": "Chinese" if published["subject"] == "math" else "English",
                "plannedSpeaker": SPEAKER_BY_LANGUAGE["Chinese" if published["subject"] == "math" else "English"],
                "plannedInstruct": INSTRUCT_BY_LANGUAGE["Chinese" if published["subject"] == "math" else "English"],
            },
            "status": "draft",
            "distributionScope": "student" if source_snapshot["license"] and self._student_distribution_allowed(source_snapshot["license"]) else "local_only",
            "createdBy": created_by,
            "createdAt": now,
        })
        return self._revision_response(row, None)

    @staticmethod
    def _student_distribution_allowed(license_name: str) -> bool:
        normalized = license_name.lower().replace("-", " ")
        return bool(re.search(r"cc\s*by\s*4(?:\.0)?", normalized)) and not any(term in normalized for term in ("nc", "noncommercial", "non commercial", "sa"))

    def get(self, media_id: str) -> dict[str, Any]:
        row = self.media_store.get_revision(media_id)
        if not row:
            raise LookupError("教学视频修订不存在")
        job = self.jobs.latest_for_payload(JOB_TYPE, "mediaId", media_id)
        if row["status"] == "rendering" and job and job["status"] in {"failed", "cancelled"}:
            if job["status"] == "cancelled":
                self.media_store.cancel_render(media_id, expected_version=row["version"])
            else:
                self.media_store.fail_render(media_id, expected_version=row["version"])
            row = self.media_store.get_revision(media_id) or row
        return self._revision_response(row, job)

    def list_for_lesson(self, chapter_id: str, publication_id: str, lesson_id: str) -> dict[str, Any]:
        rows = self.media_store.list_for_lesson(chapter_id, publication_id, lesson_id)
        return {"items": [self._revision_response(row, self.jobs.latest_for_payload(JOB_TYPE, "mediaId", row["mediaId"])) for row in rows]}

    def create_revision(self, media_id: str, request: dict[str, Any], *, created_by: str = "teacher") -> dict[str, Any]:
        prior = self.media_store.get_revision(media_id)
        if not prior:
            raise LookupError("教学视频修订不存在")
        if prior["version"] != request["expectedVersion"]:
            raise ValueError("分镜已被其他编辑更新，请刷新后重试")
        if prior["status"] == "published":
            # Published bytes and their review record remain immutable; edits always branch.
            pass
        elif prior["status"] not in {"draft", "consistency_failed", "changes_requested", "consistency_review_required", "approved"}:
            raise ValueError("当前视频任务仍在执行，不能修订")
        scenes = copy.deepcopy(prior["scenes"])
        by_id = {scene["sceneId"]: scene for scene in scenes}
        for edit in request["sceneEdits"]:
            scene = by_id.get(edit["sceneId"])
            if not scene:
                raise ValueError("片段不属于当前视频修订")
            narration = normalize_speech_text(str(edit["narration"]))
            if not narration:
                raise ValueError("修订后的旁白不能为空")
            scene["narration"] = narration
        self.renderer.validate_storyboard(
            scenes=scenes,
            source={"chapterTitle": prior["sourceSnapshot"]["chapterTitle"], **prior["sourceSnapshot"]},
        )
        source_snapshot = copy.deepcopy(prior["sourceSnapshot"])
        source_snapshot["contentFingerprint"] = _fingerprint({"source": source_snapshot, "scenes": scenes})
        new_id = uuid.uuid4().hex
        row = self.media_store.create_revision({
            **{key: prior[key] for key in ("seriesId", "chapterId", "publicationId", "lessonId", "sourceRevisionId", "title", "subject", "license", "distributionScope")},
            "mediaId": new_id,
            "version": prior["version"] + 1,
            "sourceFingerprint": source_snapshot["contentFingerprint"],
            "sourceSnapshot": source_snapshot,
            "scenes": scenes,
            "rendererSnapshot": copy.deepcopy(prior["rendererSnapshot"]),
            "status": "draft",
            "createdBy": created_by,
            "createdAt": time.time(),
        })
        return self._revision_response(row, None)

    def enqueue_render(self, media_id: str, *, expected_version: int) -> dict[str, Any]:
        row = self.media_store.get_revision(media_id)
        if not row:
            raise LookupError("教学视频修订不存在")
        if row["version"] != expected_version:
            raise ValueError("教学视频版本已变化，请刷新后重试")
        latest = self.jobs.latest_for_payload(JOB_TYPE, "mediaId", media_id)
        if latest and latest["status"] in {"queued", "running", "succeeded"}:
            if latest["status"] == "succeeded":
                return latest
            raise ValueError("当前视频渲染任务仍在排队或执行")
        if row["status"] not in {"draft", "consistency_failed", "changes_requested"}:
            raise ValueError("当前视频状态不允许开始渲染")
        if row["status"] == "consistency_failed" and row["consistencyReview"]:
            raise ValueError("一致性检查失败，请先修订分镜再渲染")
        if not self.media_store.set_rendering(media_id, expected_version=expected_version):
            raise ValueError("视频状态已变化，请刷新后重试")
        attempt_key = f"lesson-media:{media_id}:v{expected_version}:render:{uuid.uuid4().hex[:8]}"
        payload = {
            "mediaId": media_id,
            "version": expected_version,
            "idempotencyKey": attempt_key,
            "deadlineSeconds": JOB_TIMEOUT_SECONDS,
            "rendererSnapshot": row["rendererSnapshot"],
        }
        job = self.jobs.create_job(JOB_TYPE, payload, idempotency_key=attempt_key, max_attempts=JOB_MAX_ATTEMPTS)
        return job

    def cancel_render(self, media_id: str) -> dict[str, Any]:
        row = self.media_store.get_revision(media_id)
        if not row:
            raise LookupError("教学视频修订不存在")
        job = self.jobs.latest_for_payload(JOB_TYPE, "mediaId", media_id)
        if not job or job["status"] not in {"queued", "running"}:
            raise ValueError("没有可取消的视频渲染任务")
        result = self.jobs.request_cancel(job["jobId"])
        self.media_store.cancel_render(media_id, expected_version=row["version"])
        return result

    def _validate_sources_still_published(self, row: dict[str, Any]) -> None:
        published, _chapter, source = self._published_chapter(row["chapterId"], row["publicationId"])
        lesson = next((item for item in published["lessons"] if item.get("lessonId") == row["lessonId"]), None)
        if not lesson or lesson.get("status") != "published" or source.get("sourceRevisionId") != row["sourceRevisionId"]:
            raise ValueError("来源课程或发布版本已删除或变更，视频不能继续发布")
        source_refs = [ref for scene in row["scenes"] for ref in scene.get("sourceRefs", [])]
        self._resolve_source_refs(source_refs, source)

    @staticmethod
    def _audio_result(result: Any, *, narration: str, language: str, speaker: str, instruct: str) -> dict[str, Any]:
        if isinstance(result, dict):
            audio = result.get("audio")
            provider = str(result.get("provider") or "unknown")
            model = str(result.get("model") or "unknown")
            media_type = str(result.get("mediaType") or "audio/wav")
        else:
            audio = result.audio
            provider = str(result.provider)
            model = str(result.model)
            media_type = str(result.media_type)
        if not isinstance(audio, bytes) or not audio:
            raise TerminalJobError("TTS runtime 未返回音频")
        return {
            "audio": audio,
            "mediaType": media_type,
            "metadata": {
                "provider": provider,
                "model": model,
                "language": language,
                "speaker": speaker,
                "narrationTemplateVersion": NARRATION_TEMPLATE_VERSION,
                "instructVersion": "lesson-media-instruct-v1",
                "instruct": instruct,
                "textSha256": hashlib.sha256(narration.encode("utf-8")).hexdigest(),
            },
        }

    def run_render(self, payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> CancellableJobResult:
        """Synthesize all narration and render a version-bound video inside one Worker job."""
        media_id = str(payload["mediaId"])
        row = self.media_store.get_revision(media_id)
        if not row or row["version"] != payload["version"] or row["status"] != "rendering":
            raise TerminalJobError("视频版本或状态已变化，当前任务已失效")
        deadline = time.monotonic() + min(float(payload.get("deadlineSeconds", JOB_TIMEOUT_SECONDS)), JOB_TIMEOUT_SECONDS)
        language = "Chinese" if row["subject"] == "math" else "English"
        speaker = SPEAKER_BY_LANGUAGE[language]
        instruct = INSTRUCT_BY_LANGUAGE[language]
        try:
            self._validate_sources_still_published(row)
            speech: list[dict[str, Any]] = []
            for scene in row["scenes"]:
                if cancellation_check():
                    raise JobCancelled()
                narration = normalize_speech_text(scene["narration"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("视频生成超过 10 分钟时限")
                try:
                    tts_result = self.speech_runtime.synthesize(
                        narration,
                        language=language,
                        speaker=speaker,
                        instruct=instruct,
                        timeout_seconds=min(90.0, remaining),
                    )
                except TtsRuntimeError as error:
                    raise RetryableJobError(str(error)) from error
                speech.append(self._audio_result(tts_result, narration=narration, language=language, speaker=speaker, instruct=instruct))
            rendered = self.renderer.render(
                scenes=row["scenes"],
                source={"chapterTitle": row["sourceSnapshot"]["chapterTitle"], **row["sourceSnapshot"]},
                audio=speech,
                language=language,
                temp_root=self.working_root,
                cancellation_check=cancellation_check,
                deadline=deadline,
            )
            if cancellation_check():
                self._discard_render_output(rendered)
                raise JobCancelled()
            video_hash = hashlib.sha256(rendered.video_path.read_bytes()).hexdigest()
            captions_hash = hashlib.sha256(rendered.captions_path.read_bytes()).hexdigest()
            video_final, captions_final = self._commit_artifacts(media_id, video_hash, captions_hash, rendered)
            output = {
                "artifactPath": str(video_final.relative_to(self.media_store.root)),
                "captionsPath": str(captions_final.relative_to(self.media_store.root)),
                "artifactSha256": video_hash,
                "captionsSha256": captions_hash,
                "durationSeconds": rendered.duration_seconds,
                "consistencyReview": rendered.consistency_review,
                "rendererSnapshot": rendered.renderer_snapshot,
            }
            if cancellation_check() or not self.media_store.complete_render(media_id, expected_version=row["version"], output=output):
                self._delete_artifacts(video_final, captions_final)
                raise JobCancelled()

            def cleanup_cancelled_success() -> None:
                self.media_store.discard_render(media_id, expected_version=row["version"])
                self._delete_artifacts(video_final, captions_final)

            return CancellableJobResult({"mediaId": media_id, "version": row["version"], "status": "consistency_review_required" if rendered.consistency_review["passed"] else "consistency_failed", "artifactSha256": video_hash, "durationSeconds": rendered.duration_seconds}, cleanup_cancelled_success)
        except JobCancelled:
            self.media_store.cancel_render(media_id, expected_version=row["version"])
            raise
        except RetryableJobError:
            raise
        except TerminalJobError:
            self.media_store.fail_render(media_id, expected_version=row["version"])
            raise
        except Exception as error:
            if time.monotonic() >= deadline:
                self.media_store.fail_render(media_id, expected_version=row["version"])
                raise TerminalJobError("视频生成超过 10 分钟时限") from error
            raise RetryableJobError(f"教学视频生成失败：{type(error).__name__}") from error

    def _commit_artifacts(
        self, media_id: str, video_hash: str, captions_hash: str, rendered: RenderedLessonMedia,
    ) -> tuple[Path, Path]:
        directory = self.artifact_root / media_id
        directory.mkdir(parents=True, exist_ok=True)
        video_final = directory / f"{video_hash}.mp4"
        captions_final = directory / f"{captions_hash}.vtt"
        for temporary, final, expected_hash in (
            (rendered.video_path, video_final, video_hash),
            (rendered.captions_path, captions_final, captions_hash),
        ):
            if final.exists():
                actual = hashlib.sha256(final.read_bytes()).hexdigest()
                if actual != expected_hash:
                    raise RuntimeError("不可变媒体路径发生内容冲突")
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, final)
                final.chmod(0o444)
        return video_final, captions_final

    @staticmethod
    def _delete_artifacts(*paths: Path) -> None:
        for path in paths:
            try:
                path.chmod(0o600)
                path.unlink(missing_ok=True)
            except OSError:
                continue

    def _discard_render_output(self, rendered: RenderedLessonMedia) -> None:
        self._delete_artifacts(rendered.video_path, rendered.captions_path)

    def review(self, media_id: str, request: dict[str, Any], *, actor: dict[str, Any]) -> dict[str, Any]:
        row = self.media_store.get_revision(media_id)
        if not row:
            raise LookupError("教学视频修订不存在")
        if row["status"] != "consistency_review_required" or not row["consistencyReview"]:
            raise ValueError("视频尚未通过自动一致性审校")
        review_check = row["consistencyReview"]
        if not review_check["passed"] or not row["artifactSha256"] or not row["captionsSha256"]:
            raise ValueError("一致性检查未通过，不能人工批准")
        self._validate_sources_still_published(row)
        video_path = self._artifact_path(row["artifactPath"])
        caption_path = self._artifact_path(row["captionsPath"])
        if not video_path.is_file() or not caption_path.is_file():
            raise ValueError("视频或字幕产物缺失，不能人工批准")
        if hashlib.sha256(video_path.read_bytes()).hexdigest() != row["artifactSha256"]:
            raise ValueError("视频文件哈希与审核快照不匹配")
        if hashlib.sha256(caption_path.read_bytes()).hexdigest() != row["captionsSha256"]:
            raise ValueError("字幕文件哈希与审核快照不匹配")
        if request["decision"] == "approve":
            new_status = "approved"
        else:
            new_status = "changes_requested"
        review_record = {
            "reviewId": uuid.uuid4().hex,
            "mediaId": media_id,
            "mediaVersion": row["version"],
            "artifactSha256": row["artifactSha256"],
            "reviewer": actor["actorId"],
            "actor": actor,
            "decision": request["decision"],
            "note": request.get("note", ""),
            "createdAt": time.time(),
        }
        if not self.media_store.add_review_and_transition(review_record, expected_status=row["status"], new_status=new_status):
            raise ValueError("视频修订已被其他操作更新，请刷新后重试")
        return self.get(media_id)

    def publish(self, media_id: str, *, actor: dict[str, Any], note: str = "") -> dict[str, Any]:
        row = self.media_store.get_revision(media_id)
        if not row:
            raise LookupError("教学视频修订不存在")
        if row["status"] != "approved" or not row["artifactSha256"]:
            raise ValueError("视频需要通过一致性检查和人工批准后才能发布")
        self._validate_sources_still_published(row)
        self._validated_public_paths(row)
        now = time.time()
        publish_record = {
            "reviewId": uuid.uuid4().hex,
            "mediaId": media_id,
            "mediaVersion": row["version"],
            "artifactSha256": row["artifactSha256"],
            "reviewer": actor["actorId"],
            "actor": actor,
            "decision": "publish",
            "note": note,
            "createdAt": now,
        }
        if not self.media_store.publish_with_audit(publish_record, published_at=now):
            raise ValueError("视频审核状态已变化，请刷新后重试")
        return self.get(media_id)

    def published_for_student(self, chapter_id: str, publication_id: str, lesson_id: str) -> dict[str, Any]:
        published, chapter, _source = self._published_chapter(chapter_id, publication_id)
        if chapter.get("publicationId") != publication_id:
            raise LookupError("学生端仅提供章节当前发布版本的视频")
        lesson = next((item for item in published["lessons"] if item.get("lessonId") == lesson_id), None)
        if not lesson or lesson.get("status") != "published":
            raise LookupError("已发布课程不存在")
        if lesson.get("sourceRevisionId") is None:
            raise ValueError("已发布课程缺少来源版本")
        rows = self.media_store.list_for_lesson(chapter_id, publication_id, lesson_id)
        newest_by_series: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["status"] != "published" or row["distributionScope"] != "student":
                continue
            if row["sourceRevisionId"] != lesson["sourceRevisionId"]:
                continue
            current = newest_by_series.get(row["seriesId"])
            if current is None or row["version"] > current["version"]:
                newest_by_series[row["seriesId"]] = row
        items: list[dict[str, Any]] = []
        for row in newest_by_series.values():
            if not row["durationSeconds"] or not row["artifactSha256"] or not row["captionsSha256"]:
                continue
            try:
                self._validated_public_paths(row)
            except (LookupError, ValueError):
                continue
            base = f"/api/chapters/{chapter_id}/media/published/{row['mediaId']}"
            source = row["sourceSnapshot"]
            items.append({
                "mediaId": row["mediaId"],
                "version": row["version"],
                "title": row["title"],
                "subject": row["subject"],
                "sourceRevisionId": row["sourceRevisionId"],
                "sourceTitle": source.get("sourceTitle"),
                "sourceUrl": source.get("sourceUrl"),
                "attributionText": source.get("attributionText"),
                "durationSeconds": row["durationSeconds"],
                "videoUrl": f"{base}/asset",
                "captionsUrl": f"{base}/captions",
                "captionsDownloadUrl": f"{base}/captions/download",
            })
        items.sort(key=lambda item: (item["title"], item["version"]))
        return {
            "chapterId": chapter_id,
            "publicationId": publication_id,
            "lessonId": lesson_id,
            "sourceRevisionId": lesson["sourceRevisionId"],
            "items": items,
        }

    def _artifact_path(self, relative_path: str | None) -> Path:
        if not relative_path:
            raise LookupError("媒体文件路径不存在")
        root = Path(self.media_store.root).resolve()
        resolved = (root / relative_path).resolve()
        if root not in resolved.parents or resolved.suffix.lower() not in {".mp4", ".vtt"}:
            raise ValueError("媒体文件路径无效")
        return resolved

    def _validated_public_paths(self, row: dict[str, Any]) -> tuple[Path, Path]:
        video = self._artifact_path(row["artifactPath"])
        captions = self._artifact_path(row["captionsPath"])
        if not video.is_file() or not captions.is_file():
            raise LookupError("已发布媒体文件不存在")
        if hashlib.sha256(video.read_bytes()).hexdigest() != row["artifactSha256"]:
            raise ValueError("视频文件校验失败")
        if hashlib.sha256(captions.read_bytes()).hexdigest() != row["captionsSha256"]:
            raise ValueError("字幕文件校验失败")
        return video, captions

    def public_asset(self, chapter_id: str, media_id: str, *, kind: str) -> Path:
        row = self.media_store.get_revision(media_id)
        if not row or row["chapterId"] != chapter_id:
            raise LookupError("已发布教学视频不存在")
        published = self.published_for_student(chapter_id, row["publicationId"], row["lessonId"])
        if not any(item["mediaId"] == media_id for item in published["items"]):
            raise LookupError("已发布教学视频不存在")
        video, captions = self._validated_public_paths(row)
        return video if kind == "asset" else captions

    def teacher_asset(self, media_id: str, *, kind: str) -> Path:
        """Serve the exact versioned bytes to a teacher for manual review."""
        row = self.media_store.get_revision(media_id)
        if not row or row["status"] not in {
            "consistency_review_required", "consistency_failed", "approved", "published",
        }:
            raise LookupError("教学视频尚无可供审校的渲染产物")
        if not row.get("consistencyReview"):
            raise LookupError("教学视频尚无可供审校的渲染产物")
        video, captions = self._validated_public_paths(row)
        return video if kind == "asset" else captions
