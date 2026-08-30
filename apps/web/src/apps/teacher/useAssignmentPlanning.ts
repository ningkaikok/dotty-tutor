import { useCallback, useState } from "react";
import { createAssignmentPlan, loadAssignmentPlan } from "../../api/classroom";
import type { AssignmentPlan } from "../../types/classroom";

/** Keeps draft analysis separate from the final assignment mutation. */
export function useAssignmentPlanning(classId: string) {
  const [plan, setPlan] = useState<AssignmentPlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState("");

  const analyze = async (publicationId: string) => {
    if (!classId || !publicationId) return null;
    setPlanning(true);
    setError("");
    try {
      const next = await createAssignmentPlan(classId, publicationId);
      setPlan(next);
      return next;
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "班级分析失败";
      setError(message);
      return null;
    } finally {
      setPlanning(false);
    }
  };

  const restore = async (planId: string) => {
    if (!classId || !planId) return null;
    setPlanning(true);
    setError("");
    try {
      const next = await loadAssignmentPlan(classId, planId);
      setPlan(next);
      return next;
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "作业计划恢复失败");
      return null;
    } finally {
      setPlanning(false);
    }
  };

  const clear = useCallback(() => { setPlan(null); setError(""); }, []);
  return { plan, planning, error, analyze, restore, clear };
}
