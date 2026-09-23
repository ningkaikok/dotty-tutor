import { useEffect, useState } from "react";
import { loadLectureChecklist } from "../../api/classroom";
import type { LectureChecklist } from "../../types/classroom";

export function useLectureChecklist(classId: string, assignmentId: string) {
  const [checklist, setChecklist] = useState<LectureChecklist | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!classId || !assignmentId) {
      setChecklist(null);
      setError("");
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    loadLectureChecklist(classId, assignmentId)
      .then((result) => {
        if (!active) return;
        // 兼容旧演示后端/缓存返回的看板载荷，避免新卡片阻断原有教师工作台。
        const commonMistakes = result.commonMistakes ?? result.items ?? [];
        setChecklist({ ...result, commonMistakes, items: result.items ?? commonMistakes });
      })
      .catch((requestError) => {
        if (active) {
          setChecklist(null);
          setError(requestError instanceof Error ? requestError.message : "讲评清单加载失败");
        }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [assignmentId, classId]);

  return { checklist, error, loading };
}
