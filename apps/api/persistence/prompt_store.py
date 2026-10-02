"""提示词不可变修订、乐观发布指针与回滚审计；不持久化预览变量或学生数据。"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from persistence.schema import prompt_heads, prompt_release_events, prompt_revisions
from prompts import CATALOG, PromptTemplate, validate_template


class PromptConflict(ValueError):
    """当前发布版本已经变化，或修订不满足发布条件。"""


class PromptStore:
    """数据库行锁保护发布，独立 API/Worker 读取同一套已发布版本。"""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine

    def seed(self) -> None:
        """登记 Git 基线；升级代码只归档新基线，不覆盖在线发布指针。"""
        with self.engine.begin() as conn:
            for key, template in CATALOG.items():
                identity = template.identity()
                revision_id = hashlib.sha256(f"{key}:{template.version}:{identity['contentHash']}".encode()).hexdigest()
                conn.execute(insert(prompt_revisions).values(
                    revision_id=revision_id, template_id=key, version=template.version,
                    content_hash=identity["contentHash"], body=template.text, base_revision_id=None,
                    created_at=0.0, created_by="git", previewed=True,
                ).on_conflict_do_nothing())
                conn.execute(insert(prompt_heads).values(
                    template_id=key, active_revision_id=revision_id,
                ).on_conflict_do_nothing())

    def active_templates(self) -> dict[str, PromptTemplate]:
        """一次查询固定整套模板；运行中发布不会改变已取出的值对象。"""
        self.seed()
        with self.engine.connect() as conn:
            rows = conn.execute(select(prompt_revisions).join(
                prompt_heads, prompt_heads.c.active_revision_id == prompt_revisions.c.revision_id,
            )).mappings().all()
        templates = {}
        for row in rows:
            key = row["template_id"]
            if key in CATALOG:
                validate_template(key, row["body"])
                templates[key] = PromptTemplate(key, row["version"], row["body"], CATALOG[key].variables)
        return templates

    @staticmethod
    def _view(row: Any) -> dict[str, Any]:
        return {
            "revisionId": row["revision_id"], "templateId": row["template_id"],
            "version": row["version"], "contentHash": row["content_hash"], "text": row["body"],
            "baseRevisionId": row["base_revision_id"], "createdAt": row["created_at"],
            "createdBy": row["created_by"], "previewed": row["previewed"],
        }

    def detail(self, key: str) -> dict[str, Any]:
        if key not in CATALOG:
            raise KeyError(key)
        self.seed()
        with self.engine.connect() as conn:
            active = conn.execute(select(prompt_heads.c.active_revision_id).where(prompt_heads.c.template_id == key)).scalar_one()
            rows = conn.execute(select(prompt_revisions).where(prompt_revisions.c.template_id == key)
                                .order_by(prompt_revisions.c.created_at.desc(), prompt_revisions.c.revision_id)).mappings().all()
            events = conn.execute(select(prompt_release_events).where(prompt_release_events.c.template_id == key)
                                  .order_by(prompt_release_events.c.created_at.desc())).mappings().all()
        published = {event["revision_id"] for event in events} | {row["revision_id"] for row in rows if row["created_by"] == "git"}
        return {
            "id": key, "variables": list(CATALOG[key].variables), "editable": not key.startswith("evaluation."),
            "activeRevisionId": active,
            "revisions": [{**self._view(row), "published": row["revision_id"] in published} for row in rows],
            "events": [{"revisionId": row["revision_id"], "previousRevisionId": row["previous_revision_id"],
                        "action": row["action"], "createdAt": row["created_at"], "createdBy": row["created_by"]} for row in events],
        }

    def save_draft(self, key: str, body: str, base_revision_id: str, *, actor: str, now: float | None = None) -> dict[str, Any]:
        validate_template(key, body)
        if key.startswith("evaluation."):
            raise ValueError("评分标准由开发维护者管理，不能在线编辑")
        self.seed()
        revision_id = uuid.uuid4().hex
        with self.engine.begin() as conn:
            base = conn.execute(select(prompt_revisions.c.template_id).where(prompt_revisions.c.revision_id == base_revision_id)).scalar_one_or_none()
            if base != key:
                raise ValueError("草稿来源版本不存在")
            row = conn.execute(prompt_revisions.insert().values(
                revision_id=revision_id, template_id=key, version=f"online-{revision_id}",
                content_hash=hashlib.sha256(body.encode()).hexdigest(), body=body,
                base_revision_id=base_revision_id, created_at=time.time() if now is None else now,
                created_by=actor, previewed=False,
            ).returning(prompt_revisions)).mappings().one()
        return {**self._view(row), "published": False}

    def preview(self, key: str, revision_id: str, values: dict[str, str]) -> str:
        with self.engine.begin() as conn:
            row = conn.execute(select(prompt_revisions).where(
                prompt_revisions.c.template_id == key, prompt_revisions.c.revision_id == revision_id,
            )).mappings().one_or_none()
            if row is None:
                raise KeyError(revision_id)
            template = PromptTemplate(key, row["version"], row["body"], CATALOG[key].variables)
            rendered = template.render(**values)
            conn.execute(prompt_revisions.update().where(prompt_revisions.c.revision_id == revision_id).values(previewed=True))
        return rendered

    def activate(self, key: str, revision_id: str, expected_active: str, *, actor: str, rollback: bool = False, now: float | None = None) -> None:
        """比较当前指针后原子发布；回滚只指向曾发布内容，不允许用回滚发布未预览草稿。"""
        if key not in CATALOG:
            raise KeyError(key)
        if key.startswith("evaluation."):
            raise ValueError("评分标准不能在线发布")
        self.seed()
        with self.engine.begin() as conn:
            active = conn.execute(select(prompt_heads.c.active_revision_id).where(
                prompt_heads.c.template_id == key,
            ).with_for_update()).scalar_one()
            if active != expected_active:
                raise PromptConflict("发布版本已变化，请刷新后比较再操作")
            row = conn.execute(select(prompt_revisions).where(
                prompt_revisions.c.template_id == key, prompt_revisions.c.revision_id == revision_id,
            )).mappings().one_or_none()
            if row is None:
                raise KeyError(revision_id)
            if active == revision_id:
                raise PromptConflict("该版本已经生效")
            if rollback:
                released = conn.execute(select(prompt_release_events.c.event_id).where(
                    prompt_release_events.c.template_id == key, prompt_release_events.c.revision_id == revision_id,
                )).first()
                if row["created_by"] != "git" and released is None:
                    raise PromptConflict("只能回滚到曾发布的版本")
            elif not row["previewed"]:
                raise PromptConflict("请先完成已保存草稿的变量预览")
            conn.execute(prompt_heads.update().where(prompt_heads.c.template_id == key).values(active_revision_id=revision_id))
            conn.execute(prompt_release_events.insert().values(
                event_id=uuid.uuid4().hex, template_id=key, revision_id=revision_id, previous_revision_id=active,
                action="rollback" if rollback else "publish", created_at=time.time() if now is None else now, created_by=actor,
            ))
