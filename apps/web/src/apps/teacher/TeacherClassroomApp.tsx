import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { addClassMember, createAssignment, createClass, createPersonalizedAssignment, loadClass, loadClassDashboard, loadClasses, recordTeacherReview } from "../../api/classroom";
import { loadPublishedPublications } from "../../api/publications";
import type { ClassDashboard, ClassDetail, ClassSummary } from "../../types/classroom";
import type { PublicationSummary } from "../../types/publication";
import "./teacher.css";
import { AssignmentComposer } from "./AssignmentComposer";
import { AssignmentPlanReview } from "./AssignmentPlanReview";
import { useAssignmentPlanning } from "./useAssignmentPlanning";

/** 同一时刻只允许一个写操作在飞；用动作名而不是布尔量，避免三个表单互相禁用。 */
type PendingAction = "" | "class" | "member" | "assignment" | "review";

const STUDENT_STATUS_LABELS: Record<string, string> = {
  completed: "已完成",
  in_progress: "进行中",
  overdue: "已逾期",
  not_started: "未开始",
};

function formatRate(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function formatDueAt(value: number | null): string {
  return value ? new Date(value * 1000).toLocaleDateString("zh-CN") : "未设置截止时间";
}

export function TeacherClassroomApp() {
  const navigate = useNavigate();
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

  const refreshDashboard = async (classId: string, assignmentId: string) => {
    setDashboardError("");
    try {
      setDashboard(await loadClassDashboard(classId, assignmentId || undefined));
    } catch (requestError) {
      setDashboard(null);
      setDashboardError(requestError instanceof Error ? requestError.message : "掌握度看板加载失败");
    }
  };

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
    setDashboard(null);
    setDashboardError("");
    setSelectedAssignmentId("");
    clearPlanning();
    loadClass(selectedClassId)
      .then((detail) => {
        setClassDetail(detail);
        return detail.assignments.length ? refreshDashboard(selectedClassId, "") : undefined;
      })
      .catch((requestError) => setError(requestError instanceof Error ? requestError.message : "班级数据加载失败"));
  }, [clearPlanning, selectedClassId]);

  const selectAssignment = (assignmentId: string) => {
    setSelectedAssignmentId(assignmentId);
    void refreshDashboard(selectedClassId, assignmentId);
  };

  const saveClass = async () => {
    if (!className.trim()) return;
    setPending("class");
    setError("");
    try {
      const created = await createClass({ name: className.trim() });
      setClassName("");
      await refreshClasses();
      setSelectedClassId(created.classId);
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

  return (
    <main className="teacher-shell">
      <header className="teacher-header">
        <button className="route-back-button" onClick={() => navigate("/")}>← 返回入口</button>
        <div className="brand-mark">D</div>
        <div><strong>Dotty</strong><span>教师工作台</span></div>
        <span className="demo-badge">LOCAL DEMO</span>
      </header>

      <section className="teacher-hero">
        <span className="eyebrow">教师主任务</span>
        <h1>班级学习进展</h1>
        <p>布置一套作业，然后从完成情况和知识点掌握分布里找到需要帮助的学生。</p>
      </section>

      {error && <p className="teacher-notice error-text" role="alert">{error}</p>}
      {loading && <p role="status" className="muted">教师工作台加载中…</p>}

      {!loading && (
        <div className="teacher-layout">
          <aside className="teacher-sidebar panel">
            <div className="teacher-section-heading">
              <h2>我的班级</h2>
              <span>{classes.length} 个</span>
            </div>
            <div className="teacher-class-list">
              {classes.map((item) => (
                <button
                  key={item.classId}
                  className={item.classId === selectedClassId ? "selected" : ""}
                  onClick={() => setSelectedClassId(item.classId)}
                >
                  <strong>{item.name}</strong>
                  <small>{item.gradeBand} · {item.memberCount} 位学生</small>
                </button>
              ))}
            </div>
            <div className="teacher-form compact-form">
              <label>
                新班级
                <input
                  value={className}
                  onChange={(event) => setClassName(event.target.value)}
                  placeholder="例如：初二数学一班"
                />
              </label>
              <button onClick={saveClass} disabled={pending === "class" || !className.trim()}>
                {pending === "class" ? "创建中…" : "创建班级"}
              </button>
            </div>
          </aside>

          {!classDetail ? (
            <section className="teacher-main">
              {/* 首次进入时右侧不能是一片空白：这是主用户的第一印象，必须说明从哪一步开始。 */}
              <section className="teacher-card panel teacher-onboarding">
                <h2>还没有班级</h2>
                <p>教师工作台按三步展开，先在左侧创建一个班级即可开始。</p>
                <ol className="teacher-onboarding-steps">
                  <li><strong>创建班级</strong><span>在左侧填写班级名称。</span></li>
                  <li><strong>添加学生</strong><span>把学生的标识加入班级名单。</span></li>
                  <li><strong>布置作业</strong><span>选一套已发布试卷指派给全班，随后这里会显示完成情况和知识点掌握分布。</span></li>
                </ol>
              </section>
            </section>
          ) : (
            <section className="teacher-main">
              <section className="teacher-card panel">
                <div className="teacher-section-heading">
                  <div>
                    <span className="eyebrow">班级</span>
                    <h2>{classDetail.name}</h2>
                  </div>
                  <span>{classDetail.subject} · {classDetail.gradeBand}</span>
                </div>
                <div className="teacher-members">
                  <strong>班级成员</strong>
                  <div className="member-pills">
                    {classDetail.members.map((member) => (
                      <span key={member.learnerId}>
                        {member.displayName} <small>{member.learnerId}</small>
                      </span>
                    ))}
                    {!classDetail.members.length && <small className="muted">还没有学生</small>}
                  </div>
                </div>
                <div className="teacher-form member-form">
                  <label>
                    学生标识
                    <input
                      value={learnerId}
                      onChange={(event) => setLearnerId(event.target.value)}
                      placeholder="例如：local-demo"
                    />
                  </label>
                  <label>
                    学生姓名
                    <input
                      value={displayName}
                      onChange={(event) => setDisplayName(event.target.value)}
                      placeholder="例如：小安"
                    />
                  </label>
                  <button
                    onClick={saveMember}
                    disabled={pending === "member" || !learnerId.trim() || !displayName.trim()}
                  >
                    {pending === "member" ? "添加中…" : "添加学生"}
                  </button>
                </div>
                {/* 当前没有账号体系，学生标识需要老师手工对齐，必须说明它从哪来。 */}
                <small className="teacher-field-hint">
                  学生标识是学生端使用的账号名，本地演示环境固定为 <code>local-demo</code>；它必须和学生实际作答时使用的标识一致，否则看板统计不到这名学生。
                </small>
              </section>

              <section className="teacher-card panel">
                <div className="teacher-section-heading">
                  <div>
                    <span className="eyebrow">作业指派</span>
                    <h2>布置作业</h2>
                  </div>
                  <span>{classDetail.assignments.length} 次</span>
                </div>
                <AssignmentComposer
                  publications={publications}
                  publicationId={publicationId}
                  title={assignmentTitle}
                  dueDate={dueDate}
                  disabled={planning.planning || pending === "assignment" || !classDetail.members.length}
                  onPublicationChange={(value) => { setPublicationId(value); planning.clear(); }}
                  onTitleChange={setAssignmentTitle}
                  onDueDateChange={setDueDate}
                  onAnalyze={() => void analyzeAssignment()}
                />
                {!classDetail.members.length && (
                  <small className="teacher-field-hint">班级还没有学生，先添加成员才能布置作业。</small>
                )}
                {planning.error && <p className="teacher-notice error-text" role="alert">{planning.error}</p>}
                {planning.plan && (
                  <AssignmentPlanReview
                    plan={planning.plan}
                    confirming={pending === "assignment"}
                    onConfirm={(confirmWarnings) => void saveAssignment(confirmWarnings)}
                    onRegenerate={() => void analyzeAssignment()}
                    onPersonalize={() => void generatePersonalized()}
                    personalizing={personalizing}
                  />
                )}
                {classDetail.assignments.length > 0 && (
                  <ul className="assignment-list">
                    {classDetail.assignments.map((assignment) => (
                      <li key={assignment.assignmentId}>
                        <strong>{assignment.title}</strong>
                        <span>{assignment.questionCount} 题 · {formatDueAt(assignment.dueAt)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {dashboard && (
                <section className="teacher-card panel dashboard-card">
                  <div className="teacher-section-heading">
                    <div>
                      <span className="eyebrow">掌握度看板</span>
                      <h2>{dashboard.assignment.title}</h2>
                    </div>
                    <span>截止 {formatDueAt(dashboard.assignment.dueAt)}</span>
                  </div>
                  {classDetail.assignments.length > 1 && (
                    // 后端一直支持按作业查看，之前只是没有入口；老师布置多次后需要回看历史那几次。
                    <label className="dashboard-assignment-picker">
                      查看作业
                      <select
                        value={selectedAssignmentId || dashboard.assignment.assignmentId}
                        onChange={(event) => selectAssignment(event.target.value)}
                      >
                        {classDetail.assignments.map((assignment) => (
                          <option key={assignment.assignmentId} value={assignment.assignmentId}>
                            {assignment.title}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <div className="dashboard-summary-grid">
                    <div><span>班级人数</span><strong>{dashboard.summary.memberCount}</strong></div>
                    <div><span>已开始</span><strong>{dashboard.summary.startedCount}</strong></div>
                    <div><span>已完成</span><strong>{dashboard.summary.completedCount}</strong></div>
                    <div><span>完成率</span><strong>{formatRate(dashboard.summary.completionRate)}</strong></div>
                  </div>
                  <p className="dashboard-definition" role="note">{dashboard.metricDefinition}</p>
                  <div className="dashboard-review-metrics" role="status">
                    <span>AI 判定 {reviewMetrics.judgedCount} 条</span>
                    <span>已复核 {reviewMetrics.reviewedCount} 条（{formatRate(reviewMetrics.reviewRate)}）</span>
                    <span>推翻 {reviewMetrics.overturnedCount} 条（{formatRate(reviewMetrics.overturnRate)}）</span>
                    <span>掌握度覆盖 {reviewMetrics.overrideCount} 条</span>
                  </div>
                  {reviewMessage && <p className="teacher-review-message" role="status">{reviewMessage}</p>}
                  <div className="dashboard-table-wrapper">
                    <table className="dashboard-table">
                      <caption>学生完成情况</caption>
                      <thead>
                        <tr>
                          <th>学生</th>
                          <th>状态</th>
                          <th>进度</th>
                          <th>平均掌握度</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dashboard.students.map((student) => (
                          <tr key={student.learnerId}>
                            <th scope="row">{student.displayName}</th>
                            <td>
                              <span className={`student-status ${student.status}`}>
                                {STUDENT_STATUS_LABELS[student.status] ?? "未开始"}
                              </span>
                            </td>
                            <td>{student.attemptedCount}/{student.questionCount}</td>
                            <td>{formatRate(student.averageMastery)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="knowledge-point-grid">
                    {dashboard.knowledgePoints.map((point) => {
                      const evidenceItems = point.evidence ?? [];
                      return (
                      <article key={point.knowledgePointId}>
                        <div>
                          <strong>{point.knowledgePoint}</strong>
                          <span>已观测 {point.observedStudentCount} 人 · 平均 {formatRate(point.averageScore)}</span>
                        </div>
                        <dl>
                          <div><dt>未开始</dt><dd>{point.distribution.notStarted}</dd></div>
                          <div><dt>需帮助</dt><dd>{point.distribution.needsSupport}</dd></div>
                          <div><dt>发展中</dt><dd>{point.distribution.developing}</dd></div>
                          <div><dt>已掌握</dt><dd>{point.distribution.mastered}</dd></div>
                        </dl>
                        <div className="dashboard-evidence-list">
                          <strong>作答证据与教师复核</strong>
                          {evidenceItems.length === 0 && <small>暂无作答证据</small>}
                          {evidenceItems.map((evidence) => {
                            const reviewKey = `${point.knowledgePointId}:${evidence.learnerId}:${evidence.questionId}`;
                            const overrideKey = `${point.knowledgePointId}:${evidence.learnerId}`;
                            return (
                              <div className="dashboard-evidence-row" key={reviewKey} aria-busy={reviewPendingKey === reviewKey}>
                                <span>
                                  {evidence.displayName} · {evidence.questionId} · {evidence.assessment}
                                  {evidence.reviewStatus === "overturned" && ` → ${evidence.correctedAssessment}`}
                                </span>
                                <div>
                                  <button
                                    className="secondary-button"
                                    disabled={pending === "review"}
                                    onClick={() => void saveTeacherReview({
                                      learnerId: evidence.learnerId,
                                      questionId: evidence.questionId,
                                      knowledgePointId: point.knowledgePointId,
                                      action: "reviewed",
                                    }, `${reviewKey}:review`)}
                                  >复核</button>
                                  <button
                                    className="secondary-button"
                                    disabled={pending === "review"}
                                    onClick={() => void saveTeacherReview({
                                      learnerId: evidence.learnerId,
                                      questionId: evidence.questionId,
                                      knowledgePointId: point.knowledgePointId,
                                      action: "overturned",
                                      correctedAssessment: "correct",
                                    }, `${reviewKey}:overturn`)}
                                  >判错了</button>
                                  <select
                                    aria-label={`${evidence.displayName} 的掌握度覆盖`}
                                    value={overrideScores[overrideKey] ?? ""}
                                    onChange={(event) => setOverrideScores((current) => ({ ...current, [overrideKey]: event.target.value }))}
                                  >
                                    <option value="">覆盖掌握度…</option>
                                    <option value="0.2">需帮助</option>
                                    <option value="0.55">发展中</option>
                                    <option value="0.85">已掌握</option>
                                  </select>
                                  <button
                                    className="secondary-button"
                                    disabled={pending === "review" || !overrideScores[overrideKey]}
                                    onClick={() => void saveTeacherReview({
                                      learnerId: evidence.learnerId,
                                      knowledgePointId: point.knowledgePointId,
                                      action: "mastery_override",
                                      masteryScore: Number(overrideScores[overrideKey]),
                                    }, `${reviewKey}:override`)}
                                  >保存掌握度</button>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </article>
                      );
                    })}
                  </div>
                </section>
              )}

              {!dashboard && dashboardError && (
                <p className="teacher-notice error-text" role="alert">
                  掌握度看板加载失败：{dashboardError}
                  <button className="teacher-retry" onClick={() => void refreshDashboard(selectedClassId, selectedAssignmentId)}>
                    重试
                  </button>
                </p>
              )}
              {!dashboard && !dashboardError && classDetail.assignments.length === 0 && (
                <p className="teacher-empty-note">先布置一份作业，班级掌握度看板会显示在这里。</p>
              )}
            </section>
          )}
        </div>
      )}
    </main>
  );
}
