import type { operations } from "../types/generated/api";

/**
 * 当前匿名单用户 Demo 的固定学习者身份。接入服务端登录后必须整体移除，
 * 各调用点改为传递真实身份；不要在业务代码里再硬编码这个字符串。
 */
export const DEMO_LEARNER_ID = "local-demo";

/** Extract the documented 200 response while keeping domain adapters local to each API module. */
export type GeneratedSuccess<Operation extends keyof operations> =
  operations[Operation] extends { responses: infer Responses }
    ? Responses extends { 200: { content: { "application/json": infer Payload } } }
      ? Payload
      : never
    : never;

/**
 * Parse the shared JSON error envelope used by every frontend API module.
 *
 * Keeping response normalization here prevents individual product modules from
 * disagreeing about the current Problem Details envelope, and prevents silently
 * accepting empty JSON.
 */
export async function parse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null) as (T & {
    detail?: string | { message?: string; code?: string };
    message?: string;
    errorCode?: string;
    requestId?: string;
  }) | null;
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new Error(data?.message || message || `请求失败：${response.status}`);
  }
  if (!data) throw new Error("后端返回了无法解析的数据");
  return data;
}
