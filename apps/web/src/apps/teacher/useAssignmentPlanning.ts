import { useCallback, useEffect, useRef, useState } from "react";
import { createAssignmentPlan, loadAssignmentPlan } from "../../api/classroom";
import type { AssignmentPlan } from "../../types/classroom";

/** Keeps draft analysis separate from the final assignment mutation. */
export function useAssignmentPlanning(classId: string) {
  const [plan, setPlan] = useState<AssignmentPlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const activeClassId = useRef(classId);

  useEffect(() => {
    activeClassId.current = classId;
    generation.current += 1;
    setPlan(null);
    setError("");
    setPlanning(false);
  }, [classId]);

  const analyze = async (publicationId: string) => {
    if (!classId || !publicationId) return null;
    const requestClassId = classId;
    const requestGeneration = ++generation.current;
    setPlanning(true);
    setError("");
    try {
      const next = await createAssignmentPlan(classId, publicationId);
      if (generation.current !== requestGeneration || activeClassId.current !== requestClassId) return null;
      setPlan(next);
      return next;
    } catch (requestError) {
      if (generation.current !== requestGeneration || activeClassId.current !== requestClassId) return null;
      const message = requestError instanceof Error ? requestError.message : "班级分析失败";
      setError(message);
      return null;
    } finally {
      if (generation.current === requestGeneration && activeClassId.current === requestClassId) setPlanning(false);
    }
  };

  const restore = async (planId: string) => {
    if (!classId || !planId) return null;
    const requestClassId = classId;
    const requestGeneration = ++generation.current;
    setPlanning(true);
    setError("");
    try {
      const next = await loadAssignmentPlan(classId, planId);
      if (generation.current !== requestGeneration || activeClassId.current !== requestClassId) return null;
      setPlan(next);
      return next;
    } catch (requestError) {
      if (generation.current !== requestGeneration || activeClassId.current !== requestClassId) return null;
      setError(requestError instanceof Error ? requestError.message : "作业计划恢复失败");
      return null;
    } finally {
      if (generation.current === requestGeneration && activeClassId.current === requestClassId) setPlanning(false);
    }
  };

  const clear = useCallback(() => {
    generation.current += 1;
    setPlan(null);
    setError("");
    setPlanning(false);
  }, []);
  return { plan, planning, error, analyze, restore, clear };
}
