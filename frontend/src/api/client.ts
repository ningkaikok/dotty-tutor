/** Parse the shared JSON error envelope used by every frontend API module. */
export async function parse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null) as (T & { detail?: string }) | null;
  if (!response.ok) {
    throw new Error(data?.detail || `请求失败：${response.status}`);
  }
  if (!data) throw new Error("后端返回了无法解析的数据");
  return data;
}
