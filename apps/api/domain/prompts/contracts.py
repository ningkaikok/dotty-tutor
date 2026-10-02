"""提示词管理 HTTP 契约；凭据只在请求头中传递，不进入数据模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=24000)
    baseRevisionId: str = Field(min_length=1, max_length=64)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revisionId: str = Field(min_length=1, max_length=64)
    variables: dict[str, str]


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revisionId: str = Field(min_length=1, max_length=64)
    expectedActiveRevisionId: str = Field(min_length=1, max_length=64)
    action: Literal["publish", "rollback"]


class PromptRevision(BaseModel):
    revisionId: str
    templateId: str
    version: str
    contentHash: str
    text: str
    baseRevisionId: str | None
    createdAt: float
    createdBy: str
    previewed: bool
    published: bool


class ReleaseEvent(BaseModel):
    revisionId: str
    previousRevisionId: str
    action: str
    createdAt: float
    createdBy: str


class PromptDetail(BaseModel):
    id: str
    variables: list[str]
    editable: bool
    activeRevisionId: str
    revisions: list[PromptRevision]
    events: list[ReleaseEvent]


class PromptList(BaseModel):
    canPublish: bool
    items: list[PromptDetail]


class PreviewResponse(BaseModel):
    rendered: str
