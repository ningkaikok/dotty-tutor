import { ApiRequestError } from "../../api/client";

export function coursewareErrorMessage(error: unknown, fallback = "操作失败，请检查网络后重试。") {
  if (error instanceof ApiRequestError) return error.message;
  return error instanceof Error ? error.message : fallback;
}
