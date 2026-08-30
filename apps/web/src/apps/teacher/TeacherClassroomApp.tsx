import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { addClassMember, createAssignment, createClass, loadClass, loadClassDashboard, loadClasses } from "../../api/classroom";
import { loadPublishedPublications } from "../../api/publications";
import type { ClassDashboard, ClassDetail, ClassSummary } from "../../types/classroom";
import type { PublicationSummary } from "../../types/publication";
import "./teacher.css";

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
  const [className, setClassName] = useState("");
  const [learnerId, setLearnerId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [publicationId, setPublicationId] = useState("");
  const [assignmentTitle, setAssignmentTitle] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const selectedPublication = useMemo(
    () => publications.find((item) => item.publicationId === publicationId),
    [publicationId, publications],
  );

  const refreshClasses = async () => {
    const items = await loadClasses();
    setClasses(items);
    if (!selectedClassId && items[0]) setSelectedClassId(items[0].classId);
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
    Promise.all([
      loadClass(selectedClassId),
      loadClassDashboard(selectedClassId).catch(() => null),
    ]).then(([detail, nextDashboard]) => {
      setClassDetail(detail);
      setDashboard(nextDashboard);
    }).catch((requestError) => setError(requestError instanceof Error ? requestError.message : "班级数据加载失败"));
  }, [selectedClassId]);

  const saveClass = async () => {
    if (!className.trim()) return;
    setSaving(true); setError("");
    try {
      const created = await createClass({ name: className.trim() });
      setClassName("");
      await refreshClasses();
      setSelectedClassId(created.classId);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "创建班级失败"); }
    finally { setSaving(false); }
  };

  const saveMember = async () => {
    if (!selectedClassId || !learnerId.trim() || !displayName.trim()) return;
    setSaving(true); setError("");
    try {
      await addClassMember(selectedClassId, { learnerId: learnerId.trim(), displayName: displayName.trim() });
      setLearnerId(""); setDisplayName("");
      setClassDetail(await loadClass(selectedClassId));
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "添加学生失败"); }
    finally { setSaving(false); }
  };

  const saveAssignment = async () => {
    if (!selectedClassId || !publicationId) return;
    setSaving(true); setError("");
    try {
      await createAssignment(selectedClassId, {
        publicationId,
        title: assignmentTitle.trim() || selectedPublication?.title,
        dueAt: dueDate ? new Date(`${dueDate}T23:59:59`).getTime() / 1000 : null,
      });
      setAssignmentTitle(""); setDueDate("");
      setClassDetail(await loadClass(selectedClassId));
      setDashboard(await loadClassDashboard(selectedClassId));
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "布置作业失败"); }
    finally { setSaving(false); }
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
            <div className="teacher-section-heading"><h2>我的班级</h2><span>{classes.length} 个</span></div>
            <div className="teacher-class-list">
              {classes.map((item) => (
                <button key={item.classId} className={item.classId === selectedClassId ? "selected" : ""} onClick={() => setSelectedClassId(item.classId)}>
                  <strong>{item.name}</strong><small>{item.gradeBand} · {item.memberCount} 位学生</small>
                </button>
              ))}
            </div>
            <div className="teacher-form compact-form">
              <label>新班级<input value={className} onChange={(event) => setClassName(event.target.value)} placeholder="例如：初二数学一班" /></label>
              <button onClick={saveClass} disabled={saving || !className.trim()}>创建班级</button>
            </div>
          </aside>
          {classDetail && <section className="teacher-main">
            <section className="teacher-card panel">
              <div className="teacher-section-heading"><div><span className="eyebrow">CLASS</span><h2>{classDetail.name}</h2></div><span>{classDetail.subject} · {classDetail.gradeBand}</span></div>
              <div className="teacher-members"><strong>班级成员</strong><div className="member-pills">{classDetail.members.map((member) => <span key={member.learnerId}>{member.displayName} <small>{member.learnerId}</small></span>)}{!classDetail.members.length && <small className="muted">还没有学生</small>}</div></div>
              <div className="teacher-form member-form">
                <label>learner ID<input value={learnerId} onChange={(event) => setLearnerId(event.target.value)} placeholder="例如：local-demo" /></label>
                <label>学生姓名<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：小安" /></label>
                <button onClick={saveMember} disabled={saving || !learnerId.trim() || !displayName.trim()}>添加学生</button>
              </div>
            </section>
            <section className="teacher-card panel">
              <div className="teacher-section-heading"><div><span className="eyebrow">ASSIGNMENT</span><h2>布置作业</h2></div><span>{classDetail.assignments.length} 次</span></div>
              <div className="teacher-form assignment-form">
                <label>已发布试卷<select value={publicationId} onChange={(event) => setPublicationId(event.target.value)}><option value="">请选择</option>{publications.map((item) => <option key={item.publicationId} value={item.publicationId}>{item.title}</option>)}</select></label>
                <label>作业名称<input value={assignmentTitle} onChange={(event) => setAssignmentTitle(event.target.value)} placeholder="默认使用试卷名称" /></label>
                <label>截止日期<input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} /></label>
                <button onClick={saveAssignment} disabled={saving || !publicationId || !classDetail.members.length}>布置给全班</button>
              </div>
              {classDetail.assignments.length > 0 && <ul className="assignment-list">{classDetail.assignments.map((assignment) => <li key={assignment.assignmentId}><strong>{assignment.title}</strong><span>{assignment.questionCount} 题 · {formatDueAt(assignment.dueAt)}</span></li>)}</ul>}
            </section>
            {dashboard && <section className="teacher-card panel dashboard-card">
              <div className="teacher-section-heading"><div><span className="eyebrow">DASHBOARD</span><h2>{dashboard.assignment.title}</h2></div><span>截止 {formatDueAt(dashboard.assignment.dueAt)}</span></div>
              <div className="dashboard-summary-grid"><div><span>班级人数</span><strong>{dashboard.summary.memberCount}</strong></div><div><span>已开始</span><strong>{dashboard.summary.startedCount}</strong></div><div><span>已完成</span><strong>{dashboard.summary.completedCount}</strong></div><div><span>完成率</span><strong>{formatRate(dashboard.summary.completionRate)}</strong></div></div>
              <p className="dashboard-definition" role="note">{dashboard.metricDefinition}</p>
              <div className="dashboard-table-wrapper"><table className="dashboard-table"><caption>学生完成情况</caption><thead><tr><th>学生</th><th>状态</th><th>进度</th><th>平均掌握度</th></tr></thead><tbody>{dashboard.students.map((student) => <tr key={student.learnerId}><th scope="row">{student.displayName}</th><td><span className={`student-status ${student.status}`}>{student.status === "completed" ? "已完成" : student.status === "in_progress" ? "进行中" : student.status === "overdue" ? "已逾期" : "未开始"}</span></td><td>{student.attemptedCount}/{student.questionCount}</td><td>{formatRate(student.averageMastery)}</td></tr>)}</tbody></table></div>
              <div className="knowledge-point-grid">{dashboard.knowledgePoints.map((point) => <article key={point.knowledgePointId}><div><strong>{point.knowledgePoint}</strong><span>已观测 {point.observedStudentCount} 人 · 平均 {formatRate(point.averageScore)}</span></div><dl><div><dt>未开始</dt><dd>{point.distribution.notStarted}</dd></div><div><dt>需帮助</dt><dd>{point.distribution.needsSupport}</dd></div><div><dt>发展中</dt><dd>{point.distribution.developing}</dd></div><div><dt>已掌握</dt><dd>{point.distribution.mastered}</dd></div></dl></article>)}</div>
            </section>}
            {!dashboard && classDetail.assignments.length === 0 && <p className="teacher-empty-note">先布置一份作业，班级掌握度看板会显示在这里。</p>}
          </section>}
        </div>
      )}
    </main>
  );
}
