"""Bounded contracts for textbook courseware planning, authoring, and playback.

Courseware is deliberately separate from question and lesson contracts: a lesson
can contain several pages and optional activities without changing exam records.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CoursewareSubject = Annotated[str, StringConstraints(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")]
PageKind = Literal[
    "cover", "explanation", "vocabulary", "reading", "worked_example",
    "activity", "practice", "summary",
]
PageLayout = Literal["hero_split", "two_column", "cards", "text_focus", "figure_focus"]
ReviewStatus = Literal["pending", "approved", "changes_requested"]


class CoursewareSourceRef(BaseModel):
    sourceRevisionId: str = Field(min_length=1, max_length=64)
    page: int = Field(ge=1, le=100_000)
    sentenceIds: list[str] = Field(default_factory=list, max_length=20)
    regionIds: list[str] = Field(default_factory=list, max_length=20)
    assetId: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def has_locator(self) -> CoursewareSourceRef:
        if not self.sentenceIds and not self.regionIds and not self.assetId:
            raise ValueError("来源引用至少需要句子、图像区域或素材定位")
        return self


class CoursewareCropRect(BaseModel):
    """Normalized coordinates copied only from a frozen source region."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_page(self) -> CoursewareCropRect:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("裁图区域必须位于原页边界内")
        return self


class CoursewareAsset(BaseModel):
    assetId: str = Field(min_length=1, max_length=128)
    kind: Literal["source_image", "source_page", "teacher_image", "teaching_illustration", "audio"]
    origin: Literal["textbook", "teacher_provided", "ai_supplement"]
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=20)
    license: str | None = Field(default=None, max_length=256)
    attributionText: str | None = Field(default=None, max_length=500)
    distributionScope: Literal["private", "classroom", "public"] = "private"
    alt: str = Field(default="", max_length=500)
    status: Literal["available", "missing", "needs_review"] = "available"
    sourceRegionId: str | None = None
    sourceFileSha256: str | None = None
    crop: CoursewareCropRect | None = None
    fallbackReason: Literal["missing_trusted_region"] | None = None
    # Populated by the API from managed storage; never trusted from a request.
    src: str | None = None


class CoursewareBlockBase(BaseModel):
    blockId: str = Field(min_length=1, max_length=128)


class CoursewareTextBlock(CoursewareBlockBase):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=6000)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=20)
    authorship: Literal["source_excerpt", "teacher_authored", "ai_supplement"] = "ai_supplement"


class CoursewareImageBlock(CoursewareBlockBase):
    kind: Literal["image"]
    assetId: str = Field(min_length=1, max_length=128)
    alt: str = Field(min_length=1, max_length=500)
    caption: str | None = Field(default=None, max_length=500)


class CoursewareFormulaBlock(CoursewareBlockBase):
    kind: Literal["formula"]
    latex: str = Field(min_length=1, max_length=2000)
    explanation: str | None = Field(default=None, max_length=2000)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=20)


class CoursewareWordCard(BaseModel):
    term: str = Field(min_length=1, max_length=160)
    meaning: str = Field(min_length=1, max_length=1000)
    example: str | None = Field(default=None, max_length=1000)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=10)


class CoursewareWordCardsBlock(CoursewareBlockBase):
    kind: Literal["word_cards"]
    cards: list[CoursewareWordCard] = Field(min_length=1, max_length=24)


class CoursewareInputBlock(CoursewareBlockBase):
    kind: Literal["input"]
    prompt: str = Field(min_length=1, max_length=1000)
    minResponses: int = Field(default=1, ge=1, le=20)
    placeholder: str = Field(default="", max_length=160)


class CoursewareChecklistItem(BaseModel):
    itemId: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=300)


class CoursewareChecklistBlock(CoursewareBlockBase):
    kind: Literal["checklist"]
    title: str = Field(default="", max_length=300)
    items: list[CoursewareChecklistItem] = Field(min_length=1, max_length=20)


class CoursewareActivityBlock(CoursewareBlockBase):
    kind: Literal["activity"]
    activityId: str = Field(min_length=1, max_length=128)
    activityKind: Literal["discussion", "reflection", "matching", "choice", "fill_blank", "short_answer"]
    instructions: str = Field(min_length=1, max_length=2000)
    assessmentMode: Literal["participation", "teacher_review", "objective", "self_check"]
    objectiveIds: list[str] = Field(default_factory=list, max_length=20)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=20)
    practiceReason: str | None = Field(default=None, max_length=1000)
    responseSchema: dict[str, str | int | bool] = Field(default_factory=dict, max_length=12)
    choices: list[str] = Field(default_factory=list, max_length=12)
    selectionMode: Literal["single", "multiple"] = "single"
    answerKey: str | list[str] | None = Field(default=None, max_length=2000)
    acceptedAnswers: list[str] = Field(default_factory=list, max_length=20)
    teacherRubric: str | None = Field(default=None, max_length=2000)
    authorship: Literal["teacher_authored", "ai_supplement"] = "ai_supplement"

    @model_validator(mode="after")
    def normalize_choice_mode(self) -> CoursewareActivityBlock:
        # Earlier snapshots may contain several correct options without a mode.
        # The interaction mode is public; the answer itself remains private.
        if self.activityKind == "choice" and isinstance(self.answerKey, list) and len(self.answerKey) > 1:
            self.selectionMode = "multiple"
        return self

    @field_validator("responseSchema")
    @classmethod
    def bound_response_schema(cls, value: dict[str, str | int | bool]) -> dict[str, str | int | bool]:
        if any(len(key) > 80 for key in value):
            raise ValueError("活动响应字段名不能超过 80 个字符")
        if any(isinstance(item, str) and len(item) > 300 for item in value.values()):
            raise ValueError("活动响应字段描述不能超过 300 个字符")
        if any(isinstance(item, int) and abs(item) > 1_000_000 for item in value.values()):
            raise ValueError("活动响应数字边界不能超过 1,000,000")
        return value

    @field_validator("choices", "acceptedAnswers")
    @classmethod
    def bound_activity_strings(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("活动选项与答案必须是 1–500 个字符的非空文本")
        return value


CoursewareBlock = Annotated[
    Union[
        CoursewareTextBlock,
        CoursewareImageBlock,
        CoursewareFormulaBlock,
        CoursewareWordCardsBlock,
        CoursewareInputBlock,
        CoursewareChecklistBlock,
        CoursewareActivityBlock,
    ],
    Field(discriminator="kind"),
]


class CoursewareRevealStep(BaseModel):
    revealId: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=3000)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=20)
    reviewStatus: ReviewStatus = "pending"


class CoursewareRevealContent(BaseModel):
    """Editable reveal content without server-owned approval state."""

    revealId: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=3000)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=20)

    model_config = ConfigDict(extra="forbid")


class CoursewarePageOutline(BaseModel):
    pageId: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    pageKind: PageKind
    layout: PageLayout = "text_focus"
    objectiveIds: list[str] = Field(default_factory=list, max_length=20)
    sourceRefs: list[CoursewareSourceRef] = Field(min_length=1, max_length=30)
    estimatedMinutes: int = Field(default=3, ge=1, le=30)
    required: bool = True


class CoursewareSectionPlan(BaseModel):
    sectionId: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=500)
    estimatedMinutes: int = Field(ge=1, le=120)
    pages: list[CoursewarePageOutline] = Field(min_length=1, max_length=30)


class CoursewareSessionPlan(BaseModel):
    sessionId: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    durationMinutes: int = Field(ge=10, le=120)
    sections: list[CoursewareSectionPlan] = Field(min_length=1, max_length=12)


class CoursewareObjective(BaseModel):
    objectiveId: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    sourceRefs: list[CoursewareSourceRef] = Field(min_length=1, max_length=20)


class CoursewarePlanCreate(BaseModel):
    subject: CoursewareSubject
    audience: str = Field(min_length=1, max_length=200)
    languagePolicy: str = Field(min_length=1, max_length=500)
    durationMinutes: int = Field(default=40, ge=10, le=120)
    objectives: list[str] = Field(min_length=1, max_length=12)
    expectedRecordVersion: int = Field(ge=1)
    pageStart: int | None = Field(default=None, ge=1, le=100_000)
    pageEnd: int | None = Field(default=None, ge=1, le=100_000)
    # True keeps the approved plan's courseware as a retained batch and only
    # accepts pages outside every planned/generated range. False replaces the
    # whole plan, which is the original single-plan behaviour.
    supplement: bool = False
    autoGeneratePages: bool = False
    autoPublish: bool = False

    @model_validator(mode="after")
    def validate_page_range(self) -> CoursewarePlanCreate:
        if self.autoPublish and not self.autoGeneratePages:
            raise ValueError("自动完成教程需要同时启用自动生成页面")
        if (self.pageStart is None) != (self.pageEnd is None):
            raise ValueError("pageStart 和 pageEnd 必须同时提供")
        if self.pageStart is not None and self.pageEnd is not None and self.pageEnd < self.pageStart:
            raise ValueError("pageEnd 不能早于 pageStart")
        return self


class CoursewareRetainedBatch(BaseModel):
    """An earlier approved plan whose courseware stays editable after a supplement.

    Objectives are frozen with the batch because its pages reference these IDs,
    not the objectives of whichever plan is currently open.
    """

    planVersion: int = Field(ge=1)
    pageStart: int = Field(ge=1, le=100_000)
    pageEnd: int = Field(ge=1, le=100_000)
    objectives: list[CoursewareObjective] = Field(default_factory=list, max_length=12)
    coursewareIds: list[str] = Field(default_factory=list, max_length=120)


class CoursewarePlan(BaseModel):
    schemaVersion: Literal[1] = 1
    chapterId: str
    sourceRevisionId: str
    pageStart: int = Field(ge=1, le=100_000)
    pageEnd: int = Field(ge=1, le=100_000)
    subject: CoursewareSubject
    audience: str
    languagePolicy: str
    objectives: list[CoursewareObjective] = Field(default_factory=list, max_length=12)
    requestedObjectives: list[str] = Field(default_factory=list, max_length=12)
    sessions: list[CoursewareSessionPlan] = Field(default_factory=list, max_length=12)
    coverageMap: dict[str, list[CoursewareSourceRef]] = Field(default_factory=dict, max_length=12)
    status: Literal["draft", "awaiting_confirmation", "approved", "superseded"] = "draft"
    planVersion: int = Field(ge=1)
    recordVersion: int = Field(ge=1)
    generationJobId: str | None = None
    approvalCommandId: str | None = Field(default=None, max_length=128)
    requestHash: str | None = Field(default=None, max_length=64)
    generationAudit: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=24)
    generationStatus: Literal["queued", "running", "completed", "failed", "cancelled"] = "completed"
    coursewareIds: list[str] = Field(default_factory=list, max_length=120)
    retainedBatches: list[CoursewareRetainedBatch] = Field(default_factory=list, max_length=50)
    createdAt: float
    updatedAt: float

    @model_validator(mode="after")
    def validate_unique_plan_ids_and_durations(self) -> CoursewarePlan:
        session_ids: list[str] = []
        section_ids: list[str] = []
        page_ids: list[str] = []
        objective_ids = [item.objectiveId for item in self.objectives]
        if self.pageEnd < self.pageStart:
            raise ValueError("课件来源页段无效")
        if len(self.coverageMap) > 12 or any(len(refs) > 30 for refs in self.coverageMap.values()):
            raise ValueError("coverageMap 最多覆盖 12 个目标，每项目标最多 30 条来源")
        if not set(self.coverageMap).issubset(objective_ids):
            raise ValueError("coverageMap 只能引用已声明的教学目标")
        if self.status in {"awaiting_confirmation", "approved"}:
            if not self.objectives or not self.sessions:
                raise ValueError("确认课件规划前必须有教学目标和课时页面")
            if set(self.coverageMap) != set(objective_ids) or any(not refs for refs in self.coverageMap.values()):
                raise ValueError("每个教学目标都必须有非空来源覆盖")
        for session in self.sessions:
            session_ids.append(session.sessionId)
            if sum(section.estimatedMinutes for section in session.sections) != session.durationMinutes:
                raise ValueError("课时预计时长必须等于所属环节时长之和")
            for section in session.sections:
                section_ids.append(section.sectionId)
                if sum(page.estimatedMinutes for page in section.pages) != section.estimatedMinutes:
                    raise ValueError("环节预计时长必须等于所属页面时长之和")
                for page in section.pages:
                    page_ids.append(page.pageId)
                    if not page.sourceRefs:
                        raise ValueError("每个页面大纲必须绑定至少一条来源引用")
                    if not set(page.objectiveIds).issubset(objective_ids):
                        raise ValueError("页面目标必须引用已声明的教学目标")
        if len(session_ids) != len(set(session_ids)) or len(section_ids) != len(set(section_ids)) or len(page_ids) != len(set(page_ids)):
            raise ValueError("session、section 和 page ID 必须唯一")
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("教学目标 ID 必须唯一")
        retained_versions = [batch.planVersion for batch in self.retainedBatches]
        if len(retained_versions) != len(set(retained_versions)) or any(
            version >= self.planVersion for version in retained_versions
        ):
            raise ValueError("保留批次必须是更早且互不重复的规划版本")
        return self


class CoursewarePlanEdit(BaseModel):
    audience: str = Field(min_length=1, max_length=200)
    languagePolicy: str = Field(min_length=1, max_length=500)
    objectives: list[CoursewareObjective] = Field(min_length=1, max_length=12)
    sessions: list[CoursewareSessionPlan] = Field(min_length=1, max_length=12)
    expectedRecordVersion: int = Field(ge=1)
    pageStart: int | None = Field(default=None, ge=1, le=100_000)
    pageEnd: int | None = Field(default=None, ge=1, le=100_000)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_edit_graph(self) -> CoursewarePlanEdit:
        if (self.pageStart is None) != (self.pageEnd is None):
            raise ValueError("pageStart 和 pageEnd 必须同时提供")
        if self.pageStart is not None and self.pageEnd is not None and self.pageEnd < self.pageStart:
            raise ValueError("pageEnd 不能早于 pageStart")
        CoursewarePlan(
            chapterId="validation", sourceRevisionId="validation", pageStart=1, pageEnd=1, subject="english",
            audience=self.audience, languagePolicy=self.languagePolicy,
            objectives=self.objectives, sessions=self.sessions, coverageMap={},
            status="draft", planVersion=1, recordVersion=1,
            createdAt=0, updatedAt=0,
        )
        return self


class CoursewarePlanDraftOutput(BaseModel):
    """Only model-generated plan content; persistence and workflow state are server-owned."""

    objectives: list[CoursewareObjective] = Field(min_length=1, max_length=12)
    sessions: list[CoursewareSessionPlan] = Field(min_length=1, max_length=12)
    coverageMap: dict[str, list[CoursewareSourceRef]] = Field(min_length=1, max_length=12)

    @field_validator("sessions", mode="before")
    @classmethod
    def derive_generated_timing(cls, value: object) -> object:
        """Use page timings as the authoritative generated teaching schedule."""
        if not isinstance(value, list):
            return value
        import copy

        sessions = copy.deepcopy(value)
        for session in sessions:
            if not isinstance(session, dict) or not isinstance(session.get("sections"), list):
                continue
            sections = session["sections"]
            timed_pages = [page for section in sections if isinstance(section, dict)
                           for page in section.get("pages", []) if isinstance(page, dict)]
            if timed_pages and all(type(page.get("estimatedMinutes")) is int and 1 <= page["estimatedMinutes"] <= 30 for page in timed_pages):
                total = sum(page["estimatedMinutes"] for page in timed_pages)
                if total < 10:
                    # All generated times are suggestions. Keep a short outline
                    # within the same ten-minute session contract instead of
                    # rejecting it only after deriving its aggregate timings.
                    extra, remainder = divmod(10 - total, len(timed_pages))
                    for index, page in enumerate(timed_pages):
                        page["estimatedMinutes"] += extra + (index < remainder)
            for section in sections:
                if not isinstance(section, dict) or not isinstance(section.get("pages"), list):
                    continue
                pages = section["pages"]
                if pages and all(isinstance(page, dict) and type(page.get("estimatedMinutes")) is int for page in pages):
                    section["estimatedMinutes"] = sum(page["estimatedMinutes"] for page in pages)
            if sections and all(isinstance(section, dict) and type(section.get("estimatedMinutes")) is int for section in sections):
                session["durationMinutes"] = sum(section["estimatedMinutes"] for section in sections)
        return sessions

    @field_validator("coverageMap", mode="before")
    @classmethod
    def decode_coverage_entries(cls, value: object) -> object:
        """Decode the closed model wire format while keeping the API's keyed map."""
        if not isinstance(value, list):
            return value
        result: dict[str, object] = {}
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {"objectiveId", "sourceRefs"}:
                raise ValueError("coverageMap 条目必须包含 objectiveId 与 sourceRefs")
            key = entry["objectiveId"]
            if not isinstance(key, str) or key in result:
                raise ValueError("coverageMap 目标 ID 必须是唯一文本")
            result[key] = entry["sourceRefs"]
        return result

    @model_validator(mode="after")
    def validate_output_graph(self) -> CoursewarePlanDraftOutput:
        CoursewarePlan(
            chapterId="validation", sourceRevisionId="validation", pageStart=1, pageEnd=1, subject="english",
            audience="validation", languagePolicy="validation",
            objectives=self.objectives, sessions=self.sessions, coverageMap=self.coverageMap,
            status="awaiting_confirmation", planVersion=1, recordVersion=1,
            createdAt=0, updatedAt=0,
        )
        return self


class CoursewarePageDraftOutput(BaseModel):
    blocks: list[CoursewareBlock] = Field(default_factory=list, max_length=40)
    teacherNotes: str = Field(default="", max_length=4000)
    revealSteps: list[CoursewareRevealContent] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_generated_ids(self) -> CoursewarePageDraftOutput:
        # Only an explicit teacher edit can claim human authorship; source excerpts
        # retain their separate attribution and still pass the publication checks.
        for block in self.blocks:
            if isinstance(block, (CoursewareTextBlock, CoursewareActivityBlock)) and block.authorship == "teacher_authored":
                block.authorship = "ai_supplement"
        block_ids = [block.blockId for block in self.blocks]
        activity_ids = [block.activityId for block in self.blocks if isinstance(block, CoursewareActivityBlock)]
        reveal_ids = [item.revealId for item in self.revealSteps]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("生成页面的 blockId 必须唯一")
        if len(activity_ids) != len(set(activity_ids)) or len(reveal_ids) != len(set(reveal_ids)):
            raise ValueError("生成页面的 activityId/revealId 必须唯一")
        return self


class CoursewarePlanApprove(BaseModel):
    planVersion: int = Field(ge=1)
    expectedRecordVersion: int = Field(ge=1)
    commandId: str = Field(min_length=1, max_length=128)


class CoursewarePage(BaseModel):
    pageId: str = Field(min_length=1, max_length=128)
    sectionId: str = Field(min_length=1, max_length=128)
    pageKind: PageKind
    layout: PageLayout
    title: str = Field(min_length=1, max_length=200)
    objectiveIds: list[str] = Field(default_factory=list, max_length=20)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=30)
    attributionText: str | None = Field(default=None, max_length=500)
    blocks: list[CoursewareBlock] = Field(default_factory=list, max_length=40)
    teacherNotes: str = Field(default="", max_length=4000)
    revealSteps: list[CoursewareRevealStep] = Field(default_factory=list, max_length=20)
    estimatedMinutes: int = Field(ge=1, le=30)
    generationStatus: Literal["queued", "running", "completed", "failed", "cancelled"] = "queued"
    reviewStatus: ReviewStatus = "pending"
    version: int = Field(default=1, ge=1)
    error: str | None = Field(default=None, max_length=1000)
    generationAudit: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=24)

    @model_validator(mode="after")
    def validate_page_ids(self) -> CoursewarePage:
        block_ids = [block.blockId for block in self.blocks]
        activity_ids = [block.activityId for block in self.blocks if isinstance(block, CoursewareActivityBlock)]
        reveal_ids = [item.revealId for item in self.revealSteps]
        if not self.sourceRefs:
            raise ValueError("每个课件页面必须绑定至少一条来源引用")
        if len(block_ids) != len(set(block_ids)) or len(activity_ids) != len(set(activity_ids)) or len(reveal_ids) != len(set(reveal_ids)):
            raise ValueError("同一页面中的 blockId、activityId 和 revealId 必须唯一")
        return self


class CoursewarePageContent(BaseModel):
    """Teacher-editable content only; workflow state stays server-owned."""

    title: str = Field(min_length=1, max_length=200)
    pageKind: PageKind
    layout: PageLayout
    objectiveIds: list[str] = Field(default_factory=list, max_length=20)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=30)
    blocks: list[CoursewareBlock] = Field(default_factory=list, max_length=40)
    teacherNotes: str = Field(default="", max_length=4000)
    revealSteps: list[CoursewareRevealContent] = Field(default_factory=list, max_length=20)
    estimatedMinutes: int = Field(ge=1, le=30)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_page_content_ids(self) -> CoursewarePageContent:
        block_ids = [block.blockId for block in self.blocks]
        activity_ids = [block.activityId for block in self.blocks if isinstance(block, CoursewareActivityBlock)]
        reveal_ids = [item.revealId for item in self.revealSteps]
        if not self.sourceRefs:
            raise ValueError("每个页面必须绑定至少一条来源引用")
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("同一页面中的 blockId 必须唯一")
        if len(activity_ids) != len(set(activity_ids)) or len(reveal_ids) != len(set(reveal_ids)):
            raise ValueError("同一页面中的 activityId 和 revealId 必须唯一")
        return self


class CoursewareSection(BaseModel):
    sectionId: str
    title: str
    purpose: str = ""
    estimatedMinutes: int = Field(ge=1, le=120)
    pageIds: list[str] = Field(default_factory=list, max_length=30)


class CoursewareIssue(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1000)
    pageId: str | None = None


class CoursewareAutomation(BaseModel):
    """Durable unattended production state, separate from human review evidence."""

    status: Literal["generating", "checking", "repairing", "ready", "needs_attention", "paused"] = "generating"
    repairs: dict[str, int] = Field(default_factory=dict, max_length=120)
    message: str = Field(default="", max_length=1000)
    jobId: str | None = None
    retryable: bool = False


class CoursewareDocument(BaseModel):
    coursewareId: str
    chapterId: str
    planVersion: int = Field(ge=1)
    sourceRevisionId: str
    pageStart: int = Field(ge=1, le=100_000)
    pageEnd: int = Field(ge=1, le=100_000)
    sourceTitle: str | None = Field(default=None, max_length=200)
    sourceUrl: str | None = Field(default=None, max_length=500)
    sourceLicense: str | None = Field(default=None, max_length=256)
    attributionText: str | None = Field(default=None, max_length=500)
    sourceDistributionScope: Literal["private", "classroom", "public"] = "private"
    subject: CoursewareSubject
    audience: str = Field(default="", max_length=200)
    languagePolicy: str = Field(default="", max_length=500)
    title: str = Field(min_length=1, max_length=200)
    durationMinutes: int = Field(ge=10, le=120)
    sections: list[CoursewareSection] = Field(default_factory=list, max_length=12)
    pages: list[CoursewarePage] = Field(default_factory=list, max_length=120)
    assets: list[CoursewareAsset] = Field(default_factory=list, max_length=200)
    reviewIssues: list[CoursewareIssue] = Field(default_factory=list, max_length=200)
    recordVersion: int = Field(ge=1)
    status: Literal["draft", "in_review", "ready", "published", "deleted"]
    generationStatus: Literal["queued", "running", "partial", "failed", "completed", "cancelled"]
    sourceReadiness: Literal["ready", "needs_review", "blocked"]
    reviewStatus: Literal["pending", "in_review", "ready", "changes_requested"]
    publicationId: str | None = None
    generationJobId: str | None = None
    automation: CoursewareAutomation | None = None
    reviewRecords: list[dict[str, str | float | bool | list[str]]] = Field(default_factory=list, max_length=500)
    generationAudit: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=24)
    createdAt: float
    updatedAt: float

    @model_validator(mode="after")
    def validate_document_graph(self) -> CoursewareDocument:
        section_ids = [item.sectionId for item in self.sections]
        page_ids = [item.pageId for item in self.pages]
        linked_pages = [page_id for section in self.sections for page_id in section.pageIds]
        if len(section_ids) != len(set(section_ids)) or len(page_ids) != len(set(page_ids)):
            raise ValueError("课件 sectionId 和 pageId 必须唯一")
        if len(linked_pages) != len(set(linked_pages)) or set(page_ids) != set(linked_pages):
            raise ValueError("sections.pageIds 必须与 pages 完全对应且不能重复")
        section_by_page = {page_id: section.sectionId for section in self.sections for page_id in section.pageIds}
        if any(section_by_page.get(page.pageId) != page.sectionId for page in self.pages):
            raise ValueError("页面 sectionId 必须指向包含该页的环节")
        if sum(section.estimatedMinutes for section in self.sections) != self.durationMinutes:
            raise ValueError("课件总时长必须等于所有环节时长之和")
        pages_by_id = {page.pageId: page for page in self.pages}
        for section in self.sections:
            if sum(pages_by_id[page_id].estimatedMinutes for page_id in section.pageIds) != section.estimatedMinutes:
                raise ValueError("环节时长必须等于该环节所有页面时长之和")
        if self.pageEnd < self.pageStart:
            raise ValueError("课件来源范围无效")
        asset_ids = [asset.assetId for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("课件资产 assetId 必须唯一")
        asset_by_id = {asset.assetId: asset for asset in self.assets}
        activity_ids = [block.activityId for page in self.pages for block in page.blocks if isinstance(block, CoursewareActivityBlock)]
        block_ids = [block.blockId for page in self.pages for block in page.blocks]
        reveal_ids = [step.revealId for page in self.pages for step in page.revealSteps]
        checklist_item_ids = [item.itemId for page in self.pages for block in page.blocks
                              if isinstance(block, CoursewareChecklistBlock) for item in block.items]
        if (len(activity_ids) != len(set(activity_ids)) or len(block_ids) != len(set(block_ids))
                or len(reveal_ids) != len(set(reveal_ids)) or len(checklist_item_ids) != len(set(checklist_item_ids))):
            raise ValueError("同一课件中的 block/activity/reveal/checklist ID 必须全局唯一")
        for page in self.pages:
            for block in page.blocks:
                if isinstance(block, CoursewareImageBlock):
                    asset = asset_by_id.get(block.assetId)
                    if asset is None or asset.status != "available":
                        raise ValueError("图片块必须引用可用且已登记的课件素材")
        return self


class CoursewarePageEdit(BaseModel):
    page: CoursewarePageContent
    expectedRecordVersion: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class CoursewareRegenerateRequest(BaseModel):
    expectedRecordVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)
    commandId: str = Field(min_length=1, max_length=128)


class CoursewareReviewRequest(BaseModel):
    decision: Literal["approve", "request_changes"]
    expectedRecordVersion: int = Field(ge=1)
    pageIds: list[str] = Field(default_factory=list, max_length=120)
    note: str = Field(default="", max_length=2000)


class CoursewarePublishRequest(BaseModel):
    expectedRecordVersion: int = Field(ge=1)
    confirmReview: bool = False


class CoursewarePublication(BaseModel):
    publicationId: str
    coursewareId: str
    version: int = Field(ge=1)
    sourceRevisionId: str
    sourceTitle: str | None = None
    sourceUrl: str | None = None
    attributionText: str | None = None
    distributionScope: Literal["private", "classroom", "public"]
    snapshot: CoursewareDocument
    publishedAt: float
    reviewRecords: list[dict[str, str | float | bool | list[str]]] = Field(default_factory=list)


class CoursewareJobSummary(BaseModel):
    jobId: str
    jobType: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attemptCount: int = Field(ge=0)
    progress: int = Field(ge=0, le=100)
    message: str = ""
    result: dict[str, str | int | bool | None] | None = None


class CoursewarePlanResult(BaseModel):
    plan: CoursewarePlan
    job: CoursewareJobSummary | None = None


class CoursewarePlanApprovalResult(BaseModel):
    plan: CoursewarePlan
    documents: list[CoursewareDocument]
    jobs: list[CoursewareJobSummary]


class CoursewareDocumentSummary(BaseModel):
    coursewareId: str
    title: str
    status: Literal["draft", "in_review", "ready", "published", "deleted"]
    generationStatus: Literal["queued", "running", "partial", "failed", "completed", "cancelled"]
    recordVersion: int = Field(ge=1)
    pageCount: int = Field(ge=0)
    publicationId: str | None = None
    automation: CoursewareAutomation | None = None


class CoursewareDocumentsResponse(BaseModel):
    items: list[CoursewareDocumentSummary]


class CoursewarePageOutlineRequest(BaseModel):
    pageId: str = Field(min_length=1, max_length=128)
    expectedRecordVersion: int = Field(ge=1)
    commandId: str = Field(min_length=1, max_length=128)


class CoursewarePublicActivityBlock(BaseModel):
    kind: Literal["activity"]
    blockId: str
    activityId: str
    activityKind: Literal["discussion", "reflection", "matching", "choice", "fill_blank", "short_answer"]
    instructions: str
    assessmentMode: Literal["participation", "teacher_review", "objective", "self_check"]
    objectiveIds: list[str] = Field(default_factory=list, max_length=20)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list, max_length=20)
    practiceReason: str | None = None
    responseSchema: dict[str, str | int | bool] = Field(default_factory=dict, max_length=12)
    choices: list[str] = Field(default_factory=list, max_length=12)
    selectionMode: Literal["single", "multiple"] = "single"
    authorship: Literal["teacher_authored", "ai_supplement"]


CoursewarePublicBlock = Annotated[
    Union[
        CoursewareTextBlock,
        CoursewareImageBlock,
        CoursewareFormulaBlock,
        CoursewareWordCardsBlock,
        CoursewareInputBlock,
        CoursewareChecklistBlock,
        CoursewarePublicActivityBlock,
    ],
    Field(discriminator="kind"),
]


class CoursewarePublicPage(BaseModel):
    pageId: str
    sectionId: str
    pageKind: PageKind
    layout: PageLayout
    title: str
    objectiveIds: list[str] = Field(default_factory=list)
    sourceRefs: list[CoursewareSourceRef] = Field(default_factory=list)
    attributionText: str | None = Field(default=None, max_length=500)
    blocks: list[CoursewarePublicBlock] = Field(default_factory=list)
    estimatedMinutes: int


class CoursewarePublicDocument(BaseModel):
    coursewareId: str
    publicationId: str
    version: int
    subject: CoursewareSubject
    title: str
    sourceTitle: str | None = None
    sourceUrl: str | None = None
    sourceLicense: str | None = None
    attributionText: str | None = None
    sourceDistributionScope: Literal["private", "classroom", "public"]
    durationMinutes: int
    sections: list[CoursewareSection]
    pages: list[CoursewarePublicPage]
    assets: list[CoursewareAsset]
    status: Literal["published"] = "published"


class CoursewarePresentation(BaseModel):
    coursewareId: str
    publicationId: str
    version: int
    sourceTitle: str | None = None
    sourceUrl: str | None = None
    sourceLicense: str | None = None
    attributionText: str | None = None
    distributionScope: Literal["private", "classroom", "public"]
    document: CoursewareDocument


class CoursewareAttemptRequest(BaseModel):
    publicationId: str = Field(min_length=1, max_length=64)
    activityId: str = Field(min_length=1, max_length=128)
    commandId: str = Field(min_length=1, max_length=128)
    response: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)


class CoursewareAttemptResponse(BaseModel):
    attemptId: str
    publicationId: str
    activityId: str
    assessment: Literal["correct", "incorrect", "needs_review", "participation"]
    feedback: str
    createdAt: float


class CoursewareProfileView(BaseModel):
    subjectId: CoursewareSubject
    label: str


class CoursewareProfilesResponse(BaseModel):
    items: list[CoursewareProfileView]


class CoursewareSourcePreparationRequest(BaseModel):
    """Explicitly classify a material before creating a source-only chapter."""

    confirmedMaterialKind: Literal["textbook"] | None = None
    autoGenerate: bool = False
    autoPublish: bool = False
    subject: CoursewareSubject
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pageStart: int | None = Field(default=None, ge=1, le=100_000)
    pageEnd: int | None = Field(default=None, ge=1, le=100_000)
    sourceTitle: str | None = Field(default=None, max_length=200)
    sourceUrl: str | None = Field(default=None, max_length=500)
    license: str | None = Field(default=None, max_length=256)
    attributionText: str | None = Field(default=None, max_length=500)
    distributionScope: Literal["private", "classroom", "public"] = "private"

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_source_range(self) -> CoursewareSourcePreparationRequest:
        if self.autoPublish and not self.autoGenerate:
            raise ValueError("自动完成教程需要同时启用自动生成")
        if (self.pageStart is None) != (self.pageEnd is None):
            raise ValueError("pageStart 和 pageEnd 必须同时提供")
        if self.pageStart is not None and self.pageEnd is not None:
            if self.pageEnd < self.pageStart or self.pageEnd - self.pageStart + 1 > 80:
                raise ValueError("教材来源页段必须在 1 到 80 页之间")
            if self.autoGenerate and self.pageEnd - self.pageStart + 1 > 30:
                raise ValueError("自动生成一次最多选择 30 个物理页")
        return self


class CoursewareSourcePageView(BaseModel):
    pageStart: int
    pageEnd: int


class CoursewareSourceChapterResult(BaseModel):
    chapterId: str
    title: str
    subject: CoursewareSubject
    sourceRevisionId: str
    recordVersion: int
    pageStart: int
    pageEnd: int


class CoursewareSourcePreparationResult(BaseModel):
    materialKind: Literal["textbook"]
    subject: CoursewareSubject
    chapters: list[CoursewareSourceChapterResult]
    notices: list[str] = Field(default_factory=list, max_length=30)
    requiresHumanReview: bool = True


class CoursewareCoverage(BaseModel):
    """Physical-page coverage reports structural gaps, never learning quality."""

    chapterId: str
    sourceRevisionId: str
    pageStart: int
    pageEnd: int
    sourcePages: list[int]
    plannedPages: list[int]
    generatedPages: list[int]
    reviewedPages: list[int]
    publishedPages: list[int]
    missingSourcePages: list[int]
    unplannedPages: list[int]
    ungeneratedPages: list[int]
    unreviewedPages: list[int]
    unpublishedPages: list[int]
    complete: bool
    qualityAcceptance: Literal["requires_teacher_validation"] = "requires_teacher_validation"


class CoursewareCompletionBatch(BaseModel):
    pageStart: int = Field(ge=1, le=100_000)
    pageEnd: int = Field(ge=1, le=100_000)
    sourcePages: list[int] = Field(min_length=1, max_length=20)


class CoursewareCompletionPlan(BaseModel):
    """Existing workflow identity, retained when suggestions describe another range."""

    sourceRevisionId: str
    planVersion: int
    recordVersion: int
    status: Literal["draft", "awaiting_confirmation", "approved", "superseded"]
    generationStatus: Literal["queued", "running", "completed", "failed", "cancelled"]
    pageStart: int
    pageEnd: int
    stale: bool


class CoursewareCompletion(BaseModel):
    """Read-only preparation advice; human decisions stay in the existing workflow."""

    chapterId: str
    sourceRevisionId: str
    expectedRecordVersion: int
    maxPagesPerBatch: int = Field(ge=1, le=20)
    batches: list[CoursewareCompletionBatch]
    blockedPages: list[int]
    alreadyGeneratedPages: list[int]
    alreadyPlannedPages: list[int]
    pendingGenerationPages: list[int]
    pendingReviewPages: list[int]
    pendingPublicationPages: list[int]
    existingPlan: CoursewareCompletionPlan | None = None
    canCreatePlan: bool
    # When true the suggested batch must be created with supplement=True so the
    # approved plan's courseware is retained instead of replaced.
    supplementsExistingPlan: bool = False
    action: Literal["create_plan", "continue_plan", "wait_generation", "review_generated", "repair_source", "complete"]
    requiresHumanReview: Literal[True] = True


class CoursewareMediaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publicationId: str = Field(min_length=1, max_length=64)
    sourceRevisionId: str = Field(min_length=1, max_length=64)
    pageIds: list[str] = Field(min_length=3, max_length=8)

    @field_validator("pageIds")
    @classmethod
    def unique_pages(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(not item or len(item) > 128 for item in values):
            raise ValueError("课件视频 pageIds 必须唯一且有效")
        return values


class CoursewareVoiceSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learnerId: str = Field(min_length=1, max_length=128)
    publicationId: str = Field(min_length=1, max_length=64)
    pageId: str = Field(min_length=1, max_length=128)
    blockId: str = Field(min_length=1, max_length=128)
