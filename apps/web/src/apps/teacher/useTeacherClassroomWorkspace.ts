import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { addClassMember, createAssignment, createClass, createPersonalizedAssignment, loadClass, loadClassDashboard, loadClasses, recordTeacherReview } from "../../api/classroom";
import { loadPublishedPublications } from "../../api/publications";
import type { ClassDashboard, ClassDetail, ClassSummary } from "../../types/classroom";
import type { PublicationSummary } from "../../types/publication";
import { useAssignmentPlanning } from "./useAssignmentPlanning";

/** 同一时刻只允许一个写操作在飞；用动作名而不是布尔量，避免三个表单互相禁用。 */
type PendingAction = "" | "class" | "member" | "assignment" | "review";


/** Own networking, selections and review actions outside page composition. */
export function useTeacherClassroomWorkspace() {
  const [classes, setClasses] = useState<ClassSummary[]>([]);
  const [publications, setPublications] = useState<PublicationSummary[]>([]);
  const [selectedClassId, setSelectedClassId] = useState("");
  const [classDetail, setClassDetail] = useState<ClassDetail | null>(null);
  const [dashboard, setDashboard] = useState<ClassDashboard | null>(null);
  // 看板可以按作业查看。空串表示"交给后端选默认那次"，切换班级时必须重置，
  // 否则会带着上一个班级的 assignmentId 去请求。
  const [selectedAssignmentId, setSelectedAssignmentId] = useState("");
  const [className, setClassName] = useState("");
  const [learnerId, setLearnerId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [publicationId, setPublicationId] = useState("");
  const [assignmentTitle, setAssignmentTitle] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<PendingAction>("");
  const [error, setError] = useState("");
  // 看板失败必须和"还没布置作业"区分开。合成一个状态会让加载失败看起来像没有数据，
  // 老师只会看到一片空白，无从判断是该布置作业还是该重试。
  const [dashboardError, setDashboardError] = useState("");
  const [reviewMessage, setReviewMessage] = useState("");
  const [reviewPendingKey, setReviewPendingKey] = useState("");
  const [personalizing, setPersonalizing] = useState(false);
  const [overrideScores, setOverrideScores] = useState<Record<string, string>>({});
  const dashboardRequest = useRef(0);
  const classRequest = useRef(0);
  const planning = useAssignmentPlanning(selectedClassId);
  const clearPlanning = planning.clear;

  const selectedPublication = useMemo(
    () => publications.find((item) => item.publicationId === publicationId),
    [publicationId, publications],
  );
  // 兼容旧的演示数据和缓存响应：复核指标属于增量字段，缺失时按尚未记录处理。
  const reviewMetrics = dashboard?.reviewMetrics ?? {
    judgedCount: 0,
    reviewedCount: 0,
    overturnedCount: 0,
    reviewRate: null,
    overturnRate: null,
    overrideCount: 0,
  };

  const refreshClasses = async () => {
    const items = await loadClasses();
    setClasses(items);
    if (!selectedClassId && items[0]) setSelectedClassId(items[0].classId);
  };

  const refreshDashboard = useCallback(async (classId: string, assignmentId: string) => {
    const requestId = ++dashboardRequest.current;
    setDashboardError("");
    try {
      const result = await loadClassDashboard(classId, assignmentId || undefined);
      if (requestId !== dashboardRequest.current || classId !== selectedClassId) return;
      setDashboard(result);
    } catch (requestError) {
      if (requestId !== dashboardRequest.current || classId !== selectedClassId) return;
      setDashboard(null);
      setDashboardError(requestError instanceof Error ? requestError.message : "掌握度看板加载失败");
    }
  }, [selectedClassId]);

  useEffect(() => {
    Promise.all([loadClasses(), loadPublishedPublications()])
      .then(([classItems, publicationItems]) => {
        setClasses(classItems);
        setPublications(publicationItems);
        if (classItems[0]) setSelectedClassId(classItems[0].classId);
        if (publicationItems[0]) setPublicationId(publicationItems[0].publicationId);
      })
      .catch((requestError) => setError(requestError instanceof Error ? requestError.message : "教师工作台加载失败"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedClassId) return;
    const requestId = ++classRequest.current;
    dashboardRequest.current += 1;
    setDashboard(null);
    setDashboardError("");
    setClassDetail(null);
    setSelectedAssignmentId("");
    clearPlanning();
    loadClass(selectedClassId)
      .then((detail) => {
        if (requestId !== classRequest.current || detail.classId !== selectedClassId) return;
        setClassDetail(detail);
        return detail.assignments.length ? refreshDashboard(selectedClassId, "") : undefined;
      })
      .catch((requestError) => {
        if (requestId !== classRequest.current) return;
        setError(requestError instanceof Error ? requestError.message : "班级数据加载失败");
      });
  }, [clearPlanning, refreshDashboard, selectedClassId]);

  const selectAssignment = (assignmentId: string) => {
    setSelectedAssignmentId(assignmentId);
    void refreshDashboard(selectedClassId, assignmentId);
  };

  const selectClass = (classId: string) => {
    if (classId === selectedClassId) return;
    // Invalidate in the user action itself so an old response cannot land between
    // the selection event and the next effect flush.
    classRequest.current += 1;
    dashboardRequest.current += 1;
    setSelectedClassId(classId);
  };

  const saveClass = async () => {
    if (!className.trim()) return;
    setPending("class");
    setError("");
    try {
      const created = await createClass({ name: className.trim() });
      setClassName("");
      await refreshClasses();
      selectClass(created.classId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "创建班级失败");
    } finally {
      setPending("");
    }
  };

  const saveMember = async () => {
    if (!selectedClassId || !learnerId.trim() || !displayName.trim()) return;
    setPending("member");
    setError("");
    try {
      await addClassMember(selectedClassId, { learnerId: learnerId.trim(), displayName: displayName.trim() });
      setLearnerId("");
      setDisplayName("");
      setClassDetail(await loadClass(selectedClassId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "添加学生失败");
    } finally {
      setPending("");
    }
  };

  const analyzeAssignment = async () => {
    if (!selectedClassId || !publicationId || !classDetail?.members.length) return;
    await planning.analyze(publicationId);
  };

  const saveAssignment = async (confirmWarnings: boolean) => {
    if (!selectedClassId || !publicationId || !planning.plan) return;
    setPending("assignment");
    setError("");
    try {
      const created = await createAssignment(selectedClassId, {
        planId: planning.plan.planId,
        publicationId,
        title: assignmentTitle.trim() || selectedPublication?.title,
        dueAt: dueDate ? new Date(`${dueDate}T23:59:59`).getTime() / 1000 : null,
        sourceFingerprint: planning.plan.sourceFingerprint,
        confirmWarnings,
      });
      setAssignmentTitle("");
      setDueDate("");
      setClassDetail(await loadClass(selectedClassId));
      // 刚布置的这次就是老师想看的那次，直接切过去，不要停在默认作业上。
      setSelectedAssignmentId(created.assignmentId);
      await refreshDashboard(selectedClassId, created.assignmentId);
      planning.clear();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "布置作业失败");
    } finally {
      setPending("");
    }
  };

  const generatePersonalized = async () => {
    if (!selectedClassId || !planning.plan) return;
    setPersonalizing(true);
    setError("");
    try {
      const finalPlan = await createPersonalizedAssignment(selectedClassId, planning.plan.planId, 3);
      await planning.restore(finalPlan.planId);
      setPublicationId(finalPlan.publicationId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "生成个性化作业失败");
    } finally {
      setPersonalizing(false);
    }
  };

  const saveTeacherReview = async (input: {
    learnerId: string;
    questionId?: string;
    knowledgePointId?: string;
    action: "reviewed" | "overturned" | "mastery_override";
    masteryScore?: number;
    correctedAssessment?: "correct" | "partial" | "incorrect";
  }, key: string) => {
    if (!selectedClassId || !dashboard?.assignment.assignmentId) return;
    setPending("review");
    setReviewPendingKey(key);
    setReviewMessage("");
    try {
      await recordTeacherReview(selectedClassId, dashboard.assignment.assignmentId, input);
      await refreshDashboard(selectedClassId, dashboard.assignment.assignmentId);
      setReviewMessage("教师判断已追加保存，原始 AI 判定仍保留。");
    } catch (requestError) {
      setReviewMessage(requestError instanceof Error ? requestError.message : "保存教师判断失败");
    } finally {
      setPending("");
      setReviewPendingKey("");
    }
  };

  return { classes, publications, selectedClassId, classDetail, dashboard, selectedAssignmentId, className, setClassName, learnerId, setLearnerId, displayName, setDisplayName, publicationId, setPublicationId, assignmentTitle, setAssignmentTitle, dueDate, setDueDate, loading, pending, error, dashboardError, reviewMessage, reviewPendingKey, personalizing, overrideScores, setOverrideScores, planning, reviewMetrics, refreshDashboard, selectAssignment, selectClass, saveClass, saveMember, analyzeAssignment, saveAssignment, generatePersonalized, saveTeacherReview };
}
