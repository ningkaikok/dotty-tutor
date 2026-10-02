import { useNavigate } from "react-router";
import { PRODUCT_TERMS } from "../../productTerms";
import { StudentNav } from "./StudentNav";
import { useStudentTodayQueue } from "./useStudentTodayQueue";
import "./student.css";

interface QueueRow {
  key: string;
  title: string;
  description: string;
  badge: string;
  actionLabel: string;
  /** 可见文案只有“开始/继续”，读屏时一串同名按钮无法区分，因此无障碍名称由 actionLabel + title 组合。 */
  onAction: () => void;
}

/** 今日只计算仍需行动的任务；已完成作业保留独立回看入口。 */
export function StudentLearningApp() {
  const navigate = useNavigate();
  const { pendingConfirmCount, dueReviewCount, unmasteredCount, papers, assignments, loading, error, allFailed } = useStudentTodayQueue();

  const rows: QueueRow[] = [];
  const completedRows: QueueRow[] = [];

  assignments.forEach((assignment) => {
    const statusLabel = assignment.learnerStatus === "completed"
      ? "已完成"
      : assignment.learnerStatus === "overdue"
        ? "已逾期"
        : assignment.learnerStatus === "in_progress" ? "进行中" : "待开始";
    const target = assignment.learnerStatus === "completed" ? completedRows : rows;
    target.push({
      key: `assignment-${assignment.assignmentId}`,
      title: assignment.title,
      description: `${statusLabel} · ${assignment.className || "班级作业"}${assignment.dueAt ? ` · 截止 ${new Date(assignment.dueAt * 1000).toLocaleDateString("zh-CN")}` : ""}`,
      badge: `${assignment.attemptedCount}/${assignment.questionCount} 题`,
      actionLabel: assignment.learnerStatus === "completed" ? "回看" : assignment.learnerStatus === "in_progress" ? "继续" : "开始",
      onAction: () => navigate(`/learn/papers/${assignment.publicationId}?assignmentId=${encodeURIComponent(assignment.assignmentId)}`),
    });
  });

  if (pendingConfirmCount > 0) {
    rows.push({
      key: "confirm",
      title: "确认新录入的错题",
      description: "先确认识别结果，这些错题才能进入辅导订正。",
      badge: `${pendingConfirmCount} 道`,
      actionLabel: "去确认",
      onAction: () => navigate("/mistakes"),
    });
  }

  if (dueReviewCount > 0) {
    rows.push({
      key: "review",
      title: "今日复习",
      description: "这些复习已经到期，完成后会安排下一次复习。",
      badge: `${dueReviewCount} 道`,
      actionLabel: "开始复习",
      onAction: () => navigate("/mistakes/progress"),
    });
  }

  if (unmasteredCount > 0) {
    rows.push({
      key: "correct",
      title: "订正错题",
      description: "这些错题还没订正，完成之后才会进入复习计划。",
      badge: `${unmasteredCount} 道`,
      actionLabel: "去订正",
      onAction: () => navigate("/mistakes"),
    });
  }

  // 自由练习目录没有完成期限，不计入今日待办。
  const practiceRows: QueueRow[] = papers.map((paper) => ({
    key: `paper-${paper.publicationId}`,
    title: paper.title,
    description: paper.started ? "继续练习，或回看已提交的答案。" : "本机还没有这套练习的学习记录。",
    badge: `${paper.lessonCount} 题`,
    actionLabel: paper.started ? "继续" : "开始",
    onAction: () => navigate(`/learn/papers/${paper.publicationId}`),
  }));

  const taskCount = rows.length;

  return (
    <main className="student-shell">
      <header className="student-header">
        <button className="route-back-button" onClick={() => navigate("/")}>← 返回入口</button>
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>学生学习空间</span>
        </div>
        <span className="demo-badge">{PRODUCT_TERMS.demoBadge}</span>
      </header>

      <StudentNav />

      <section className="student-hero">
        <span className="eyebrow">今日</span>
        <h1>
          {loading
            ? "正在整理今天的任务…"
            // 全部请求失败时不能说“没有待办”：那是读不到数据的假象，会让学生
            // 以为今天已经做完。
            : allFailed
              ? "暂时读不到今天的任务"
              : taskCount > 0 ? `今天有 ${taskCount} 件事` : error ? "已加载的内容中没有待办" : "今天没有待办任务"}
        </h1>
        <p>
          {loading
            ? "正在读取作业、错题、复习计划和练习进度。"
            : allFailed
              ? "作业、错题、复习和练习都没有加载成功。请检查网络后刷新页面重试。"
              : error
                ? "部分内容还未加载成功，当前列表可能不完整。请刷新后重试。"
                : taskCount > 0
                  ? "可以从下面的任务开始，也可以选择你现在需要的练习。"
                  : "当前没有未完成作业、待确认或待订正错题，也没有到期复习。可以自由练习或回看已完成作业。"}
        </p>
      </section>

      {error && <p className="student-empty-note" role="alert">部分内容未能加载：{error}</p>}

      {loading && (
        <ol className="student-today-queue" aria-label="今日任务队列" aria-busy="true">
          {[0, 1, 2].map((index) => (
            <li key={index} className="today-queue-row today-queue-skeleton" aria-hidden="true">
              <span className="today-queue-index" />
              <div className="today-queue-body">
                <span className="today-queue-skeleton-line title" />
                <span className="today-queue-skeleton-line" />
              </div>
              <span className="today-queue-badge" />
              <span className="today-queue-action" />
            </li>
          ))}
        </ol>
      )}

      {!loading && taskCount > 0 && (
        <ol className="student-today-queue" aria-label="今日任务队列">
          {rows.map((row, index) => <QueueItem key={row.key} row={row} index={index + 1} />)}
        </ol>
      )}

      {!loading && !allFailed && (
        <section className="student-practice-section" aria-labelledby="student-practice-heading">
          <h2 id="student-practice-heading">练习</h2>
          {practiceRows.length ? (
            <ol className="student-today-queue" aria-label="练习">
              {practiceRows.map((row) => <QueueItem key={row.key} row={row} index="卷" />)}
            </ol>
          ) : (
            <p className="student-empty-note">{error ? "练习目录可能尚未完整加载，请刷新后重试。" : "老师还没有发布新的练习，练习任务会自动出现在这里。"}</p>
          )}
        </section>
      )}
      {!loading && completedRows.length > 0 && (
        <section className="student-practice-section" aria-labelledby="completed-assignments-heading">
          <h2 id="completed-assignments-heading">已完成作业</h2>
          <ol className="student-today-queue" aria-label="已完成作业">
            {completedRows.map((row) => <QueueItem key={row.key} row={row} index="✓" />)}
          </ol>
        </section>
      )}
    </main>
  );
}

function QueueItem({ row, index }: { row: QueueRow; index: number | string }) {
  return (
    <li className="today-queue-row">
      <span className="today-queue-index" aria-hidden="true">{index}</span>
      <div className="today-queue-body"><h3>{row.title}</h3><p>{row.description}</p></div>
      <span className="today-queue-badge">{row.badge}</span>
      <button className="today-queue-action" aria-label={`${row.actionLabel}：${row.title}`} onClick={row.onAction}>
        {row.actionLabel}
      </button>
    </li>
  );
}
