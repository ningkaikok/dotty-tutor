import type { MistakeErrorReason, TutorMessage } from "../../types/index";

export type AiErrorReason = Exclude<MistakeErrorReason, "unknown">;

export interface MistakeAttribution {
  selfAssessment?: MistakeErrorReason;
  aiAssessment?: AiErrorReason;
}

const AI_ERROR_REASONS: ReadonlySet<string> = new Set<AiErrorReason>([
  "concept",
  "reading",
  "calculation",
  "missing_step",
  "careless",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAiErrorReason(value: unknown): value is AiErrorReason {
  return typeof value === "string" && AI_ERROR_REASONS.has(value);
}

/**
 * 取线程中最后一条可信的 AI 归因。
 *
 * 门禁必须与后端保持一致：只有明确不需要确认、且分类属于 AI 可采信集合时才算判断。
 * `unknown` 是后端"模型没给出可用分类"的归一化兜底值，既不入库也不参与展示，
 * 前端放宽这条会让界面显示的归因与实际驱动出题的归因对不上。
 */
function latestTrustedAiErrorReason(messages?: readonly TutorMessage[]): AiErrorReason | undefined {
  let latest: AiErrorReason | undefined;
  for (const message of messages ?? []) {
    const action = isRecord(message.action) ? message.action : undefined;
    const plan = action && isRecord(action.tutorTurnPlan) ? action.tutorTurnPlan : undefined;
    const misconception = plan && isRecord(plan.misconception) ? plan.misconception : undefined;
    if (misconception?.needsConfirmation === false && isAiErrorReason(misconception.category)) {
      latest = misconception.category;
    }
  }
  return latest;
}

export function resolveMistakeAttribution(
  selfAssessment: MistakeErrorReason | undefined,
  messages?: readonly TutorMessage[],
): MistakeAttribution {
  return {
    selfAssessment,
    aiAssessment: latestTrustedAiErrorReason(messages),
  };
}
