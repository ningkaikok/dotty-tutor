import { errorReasonLabel } from "../mistake/errorReasons";
import type { LectureChecklist, LectureChecklistMistake } from "../../types/classroom";
import { useLectureChecklist } from "./useLectureChecklist";

const attributionSourceLabel: Record<LectureChecklistMistake["students"][number]["attributionSource"], string> = {
  ai: "AI 归因",
  self: "学生自评",
  unknown: "未知来源",
};

function assessmentLabel(value: string): string {
  return value === "correct" ? "对" : value === "incorrect" ? "错" : value === "partial" ? "部分正确" : value;
}

async function copyEvidenceRef(reference: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(reference);
  }
}

function ChecklistItem({ item }: { item: LectureChecklistMistake }) {
  return (
    <article className="lecture-checklist-item">
      <div className="lecture-checklist-item-heading">
        <div>
          <strong>{item.title}</strong>
          <small>第 {item.questionOrder + 1} 题 · {item.involvedStudentCount} 人 · 错误率 {Math.round(item.errorRate * 100)}%</small>
        </div>
        <span>{item.attemptedStudentCount} 人作答</span>
      </div>
      <div className="lecture-checklist-reasons">
        {item.errorReasons.map((reason) => (
          <span key={reason.reason}>{errorReasonLabel(reason.reason as Parameters<typeof errorReasonLabel>[0]) ?? reason.reason} {reason.count}</span>
        ))}
      </div>
      <ul className="lecture-checklist-students">
        {item.students.map((student) => (
          <li key={student.learnerId}>
            <span>{student.displayName} · {assessmentLabel(student.assessment)}</span>
            {student.reviewStatus === "overturned" && <small>教师改判为{assessmentLabel(student.correctedAssessment ?? "")}</small>}
            <em>{attributionSourceLabel[student.attributionSource]} · {errorReasonLabel(student.errorReason as Parameters<typeof errorReasonLabel>[0]) ?? student.errorReason}</em>
            <span className="lecture-checklist-evidence" aria-label={`${student.displayName} 的证据引用`}>
              {student.evidenceRefs.map((reference) => (
                <span key={reference} className="lecture-checklist-evidence-ref">
                  <code>{reference}</code>
                  <button
                    type="button"
                    aria-label={`复制证据引用 ${reference}`}
                    onClick={() => { void copyEvidenceRef(reference); }}
                  >复制</button>
                </span>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export function LectureChecklistPanel({ classId, assignmentId }: { classId: string; assignmentId: string }) {
  const { checklist, error, loading } = useLectureChecklist(classId, assignmentId);
  if (loading) return <section className="teacher-card panel lecture-checklist"><p role="status" className="muted">讲评清单加载中…</p></section>;
  if (error) return <section className="teacher-card panel lecture-checklist"><p className="teacher-notice error-text" role="alert">讲评清单加载失败：{error}</p></section>;
  if (!checklist) return null;
  return <section className="teacher-card panel lecture-checklist">
    <div className="teacher-section-heading">
      <div><span className="eyebrow">讲评准备</span><h2>老师讲评清单</h2></div>
      <span>优先展示 {checklist.commonMistakes.length} 道共性错题</span>
    </div>
    {checklist.commonMistakes.length === 0 ? (
      <p className="teacher-empty-note">目前还没有可讲评的共性错题。</p>
    ) : (
      <div className="lecture-checklist-list">
        {checklist.commonMistakes.map((item) => <ChecklistItem key={item.questionId} item={item} />)}
      </div>
    )}
  </section>;
}

export type { LectureChecklist };
