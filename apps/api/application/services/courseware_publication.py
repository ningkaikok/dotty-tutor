"""Adapt frozen courseware publications to existing media and voice boundaries."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from application.lesson_media_renderer import normalize_speech_text


class CoursewarePublicationAdapter:
    """Whitelist public teaching content; activity answers/reveal steps never enter narration."""

    def __init__(self, service: Any) -> None:
        self.service = service

    @staticmethod
    def block_text(block: dict[str, Any]) -> str:
        kind = block.get("kind")
        if kind == "text":
            return str(block.get("text") or "")
        if kind == "formula":
            return " ".join(str(block.get(key) or "") for key in ("latex", "explanation")).strip()
        if kind == "image":
            return str(block.get("caption") or block.get("alt") or "")
        if kind == "word_cards":
            return "\n".join(" · ".join(str(card.get(key) or "") for key in ("term", "meaning", "example"))
                             for card in block.get("cards", []))
        return ""

    @staticmethod
    def fingerprint(publication: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(publication, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":")).encode()).hexdigest()

    def load(self, courseware_id: str, publication_id: str, *, public: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        publication = self.service.store.load_publication(publication_id)
        if not publication or publication.get("coursewareId") != courseware_id:
            raise LookupError("课件发布版本不存在或不属于此课件")
        if public and publication.get("distributionScope") not in {"classroom", "public"}:
            raise LookupError("课件未向学生发布")
        document = self.service.store.load_document(courseware_id)
        if not document or document.get("deletedAt") or document.get("status") == "deleted":
            raise LookupError("课件不存在")
        chapter = self.service.store.load_chapter(document["chapterId"])
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("来源章节不存在")
        source = next((item for item in chapter.get("sourceRevisions", [])
                       if item.get("sourceRevisionId") == publication["sourceRevisionId"]), None)
        if not source:
            raise ValueError("发布课件缺少冻结来源修订")
        if source.get("uploadId") and source.get("sourceFileSha256"):
            from application.services.chapter_source_preview import (
                file_hash,
                source_file,
            )

            upload = self.service.chapters.store.load_job(source["uploadId"])
            path = source_file(self.service.chapters.store, upload) if upload and upload.get("status") in {"complete", "completed", "ready"} else None
            if path is None or file_hash(path) != source["sourceFileSha256"]:
                raise ValueError("课件原文件已变化或不可用")
        return publication, source

    def storyboard(self, request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        publication, source = self.load(request["coursewareId"], request["publicationId"])
        if request["sourceRevisionId"] != publication["sourceRevisionId"]:
            raise ValueError("课件来源修订与请求不一致")
        snapshot = publication["snapshot"]
        page_ids = request["pageIds"]
        if not 3 <= len(page_ids) <= 8 or len(set(page_ids)) != len(page_ids):
            raise ValueError("课件视频需要 3 到 8 个不同的已发布页面")
        by_id = {page["pageId"]: page for page in snapshot.get("pages", [])}
        scenes = []
        for page_id in page_ids:
            page = by_id.get(page_id)
            if not page:
                raise LookupError("页面不属于指定课件发布版本")
            narration = normalize_speech_text("\n".join(filter(None, (self.block_text(block) for block in page.get("blocks", [])))))
            if not narration:
                raise ValueError("此页没有可用于视频的公开讲解块；活动不能作为答案旁白")
            refs = page.get("sourceRefs", [])
            self.service._validate_refs(refs, source, source.get("pages", []))
            scenes.append({"sceneId": uuid.uuid4().hex, "order": len(scenes) + 1,
                           "kind": "courseware_page", "title": page["title"], "narration": narration, "sourceRefs": refs})
        result = {"sourceKind": "courseware", "coursewareId": request["coursewareId"],
                  "publicationFingerprint": self.fingerprint(publication), "pageIds": page_ids,
                  "chapterId": snapshot["chapterId"], "chapterTitle": snapshot["title"],
                  "publicationId": publication["publicationId"], "publicationVersion": publication["version"],
                  "lessonId": f"courseware:{request['coursewareId']}", "lessonTitle": snapshot["title"],
                  "lessonVersion": publication["version"], "subject": snapshot["subject"],
                  "sourceRevisionId": publication["sourceRevisionId"], "sourceFileSha256": source.get("sourceFileSha256"),
                  "sourceTitle": publication.get("sourceTitle"), "sourceUrl": publication.get("sourceUrl"),
                  "attributionText": publication.get("attributionText"), "license": snapshot.get("sourceLicense") or source.get("license"),
                  "distributionScope": publication["distributionScope"], "sourceBlocks": [],
                  "storyboardTemplateVersion": "courseware-public-teaching-v1"}
        if not result["license"] or not result["attributionText"]:
            raise ValueError("课件视频必须保留来源许可和署名")
        result["contentFingerprint"] = hashlib.sha256(json.dumps({"source": result, "scenes": scenes}, sort_keys=True).encode()).hexdigest()
        return result, scenes

    def validate_media(self, row: dict[str, Any], *, public: bool = False) -> None:
        frozen = row["sourceSnapshot"]
        publication, source = self.load(frozen["coursewareId"], row["publicationId"], public=public)
        if (frozen.get("publicationFingerprint") != self.fingerprint(publication)
                or row["sourceRevisionId"] != publication["sourceRevisionId"]
                or row["chapterId"] != publication["snapshot"]["chapterId"]):
            raise ValueError("课件视频来源发布指纹不匹配")
        for scene in row["scenes"]:
            self.service._validate_refs(scene.get("sourceRefs", []), source, source.get("pages", []))

    def voice_context(self, courseware_id: str, publication_id: str, page_id: str, block_id: str) -> dict[str, Any]:
        publication, _source = self.load(courseware_id, publication_id, public=True)
        snapshot = publication["snapshot"]
        page = next((item for item in snapshot.get("pages", []) if item.get("pageId") == page_id), None)
        block = next((item for item in (page or {}).get("blocks", []) if item.get("blockId") == block_id), None)
        content = self.block_text(block or {})
        if not page or not block or not content:
            raise LookupError("此发布页没有可用于语音陪练的公开讲解块")
        return {"subject": snapshot["subject"], "chapterTitle": snapshot["title"], "lessonTitle": page["title"],
                "knowledgePoints": [], "stepTitle": page["title"], "stepContent": content[:6000],
                "contextLabel": f"{snapshot['title']} · {page['title']}"}
