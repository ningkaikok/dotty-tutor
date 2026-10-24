import type { components } from "../../types/generated/api";

type CoursewareSummary = Pick<
  components["schemas"]["CoursewareDocumentSummary"],
  "status" | "generationStatus" | "publicationId" | "automation"
>;

export type CoursewareListStatus = {
  kind: "published" | "making" | "needs-attention" | "preview" | "ready-to-publish" | "draft" | "deleted";
  label: string;
};

const chapterStatusLabels: Record<string, string> = {
  draft: "草稿",
  in_review: "待复核",
  needs_review: "需要处理",
  published: "已发布",
};

/** Translate persisted courseware state into user-facing labels without consulting chapter.status. */
export function coursewareListStatus(document: CoursewareSummary): CoursewareListStatus {
  if (document.status === "deleted") return { kind: "deleted", label: "已删除" };
  if (document.status === "published") {
    return document.publicationId
      ? { kind: "published", label: "已发布" }
      : { kind: "published", label: "已发布，版本暂不可用" };
  }

  const automation = document.automation?.status ?? "";
  if (["needs_attention", "failed", "partial", "cancelled"].includes(automation)
    || ["failed", "partial", "cancelled"].includes(document.generationStatus)) {
    return { kind: "needs-attention", label: "需要处理" };
  }
  if (["generating", "checking", "repairing"].includes(automation)
    || ["queued", "running"].includes(document.generationStatus)) {
    return { kind: "making", label: "制作中" };
  }
  if (document.generationStatus === "completed") {
    return document.status === "ready"
      ? { kind: "ready-to-publish", label: "可发布" }
      : { kind: "preview", label: "已生成，可预览" };
  }
  if (document.status === "in_review") return { kind: "ready-to-publish", label: "待处理" };
  if (automation === "paused") return { kind: "draft", label: "手动编辑中" };
  return { kind: "draft", label: "草稿" };
}

/** Keep legacy chapter workflow values from leaking into student-facing labels. */
export function chapterStatusLabel(status: string): string {
  return chapterStatusLabels[status] ?? "状态待确认";
}

/** Prefer the configured Chinese label and never expose an unknown backend enum. */
export function coursewareSubjectLabel(subjectId: string, configuredLabel?: string): string {
  const known: Record<string, string> = { english: "英语", math: "数学", science: "科学" };
  return known[subjectId] ?? (configuredLabel && configuredLabel !== subjectId ? configuredLabel : "其他学科");
}
