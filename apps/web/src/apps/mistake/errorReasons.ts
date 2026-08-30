import type { MistakeErrorReason } from "../../types/index";

// 错因标签和描述由确认页迁移而来；陪练与错题本共用这份文案，避免两处再次漂移。
export const ERROR_REASONS: ReadonlyArray<readonly [MistakeErrorReason, string, string]> = [
  ["concept", "概念不清", "定义、公式或原理没有理解"],
  ["reading", "审题错误", "遗漏或误解了题目条件"],
  ["calculation", "计算失误", "方法正确但运算出错"],
  ["missing_step", "步骤遗漏", "推导、证明或单位不完整"],
  ["unknown", "完全不会", "不知道从哪里开始"],
  ["careless", "粗心大意", "会做但抄错、看错或没检查"],
];

export const ERROR_REASON_LABELS: Record<MistakeErrorReason, string> = Object.fromEntries(
  ERROR_REASONS.map(([value, label]) => [value, label]),
) as Record<MistakeErrorReason, string>;

export function errorReasonLabel(reason: MistakeErrorReason): string {
  return ERROR_REASON_LABELS[reason];
}
