import type { AiPageReview, CoursewareAiReviewStatus } from "../../api/aiReview";

export const verdictLabel: Record<NonNullable<AiPageReview["verdict"]>, string> = {
  pass: "AI 未发现问题",
  concern: "AI 存疑",
  fail: "AI 发现阻断问题",
};

export type AiIssue = NonNullable<AiPageReview["issues"]>[number];

export const issueLabel: Record<AiIssue["code"], string> = {
  excerpt_mismatch: "摘录与原文不一致",
  formula_error: "公式/数字错误",
  figure_mismatch: "图文不对应",
  answer_error: "答案或步骤错误",
  unsupported_content: "超出来源的内容",
  missing_condition: "遗漏条件",
  other: "其他",
};

/** Group pages by where the server routed them; the server alone decides routing and sampling. */
export function aiReviewGroups(status: CoursewareAiReviewStatus) {
  const by = (route: AiPageReview["route"]) => status.pages.filter((page) => page.route === route);
  return {
    needsReview: by("needs_review"),
    bulkConfirmable: by("bulk_confirmable"),
    // Edited after the verdict, or never reviewed: both need a (re)run before routing.
    unreviewed: [...by("stale"), ...by("not_reviewed")],
    decided: by("decided"),
  };
}

export function samplingLabel(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/** Plain-language reliability line; nothing is claimed until a teacher has checked a page. */
export function metricsSummary(metrics: CoursewareAiReviewStatus["metrics"]): string {
  if (!metrics.compared) return "还没有教师复核过 AI 结论，暂无一致率。";
  return `教师已对照 ${metrics.compared} 页：一致 ${metrics.agreements} 页，AI 漏报 ${metrics.missed} 页，误报 ${metrics.falseAlarms} 页。`;
}
