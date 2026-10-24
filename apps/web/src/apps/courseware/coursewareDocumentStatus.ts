import type { CoursewareAuthoringDocument } from "./coursewareAuthoringView";

type DocumentStatusInput = {
  status: CoursewareAuthoringDocument["status"];
  generationStatus: CoursewareAuthoringDocument["generationStatus"];
  sourceReadiness: CoursewareAuthoringDocument["sourceReadiness"];
  automation?: Pick<NonNullable<CoursewareAuthoringDocument["automation"]>, "status" | "message"> | null;
  reviewIssues: CoursewareAuthoringDocument["reviewIssues"];
  pages: Array<Pick<CoursewareAuthoringDocument["pages"][number], "generationStatus" | "error">>;
  sourceDistributionScope: CoursewareAuthoringDocument["sourceDistributionScope"];
};

export type CoursewareDocumentStatusView = {
  kind: "published" | "making" | "needs-attention" | "unpublished";
  label: string;
  tone: "success" | "info" | "warning" | "neutral";
  message: string;
  canPublish: boolean;
};

/** Derive the visible management state without overriding the server's publication state. */
export function coursewareDocumentStatus(document: DocumentStatusInput): CoursewareDocumentStatusView {
  if (document.status === "published") {
    return {
      kind: "published",
      label: "已发布",
      tone: "success",
      message: "课件已发布，可直接授课。",
      canPublish: false,
    };
  }

  const automationStatus = document.automation?.status ?? "";
  const failedPages = document.pages.some((page) => page.generationStatus !== "completed" || Boolean(page.error));
  const explicitNeedsAttention = automationStatus === "needs_attention"
    || document.generationStatus === "failed"
    || document.generationStatus === "partial"
    || document.generationStatus === "cancelled"
    || document.sourceReadiness !== "ready"
    || document.reviewIssues.length > 0;
  if (explicitNeedsAttention) {
    const message = document.generationStatus === "failed" || document.generationStatus === "partial"
      ? "部分页面未能完成。打开问题页核对教材来源，修订或重做后再预览发布。"
      : document.automation?.message
        || (document.sourceReadiness !== "ready" ? "教材来源尚未就绪。请处理来源问题后再继续。" : "有内容或来源问题需要处理。请检查问题页后再继续。");
    return {
      kind: "needs-attention",
      label: "需要处理",
      tone: "warning",
      message,
      canPublish: false,
    };
  }

  const making = ["generating", "checking", "repairing"].includes(automationStatus)
    || document.generationStatus === "queued"
    || document.generationStatus === "running";
  if (making) {
    return {
      kind: "making",
      label: "制作中",
      tone: "info",
      message: document.automation?.message || (document.generationStatus === "queued" ? "课件制作正在排队；完成后会显示预览。" : "课件正在制作；完成后会显示预览。"),
      canPublish: false,
    };
  }

  if (document.pages.length === 0 || failedPages) {
    return {
      kind: "needs-attention",
      label: "需要处理",
      tone: "warning",
      message: "有页面尚未完成。请检查问题页并处理后再继续。",
      canPublish: false,
    };
  }

  const canPublish = document.status !== "deleted"
    && document.generationStatus === "completed"
    && document.sourceReadiness === "ready"
    && document.reviewIssues.length === 0
    && document.pages.length > 0
    && document.pages.every((page) => page.generationStatus === "completed" && !page.error);
  return {
    kind: "unpublished",
    label: "未发布",
    tone: "neutral",
    message: document.sourceDistributionScope === "private"
      ? "课件已生成，可预览后保存并使用；需要调整时可展开编辑页面。"
      : "课件已生成，可预览后直接发布并授课；需要调整时可展开编辑页面。",
    canPublish,
  };
}
