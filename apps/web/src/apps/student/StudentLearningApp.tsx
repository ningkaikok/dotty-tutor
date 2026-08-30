import { useNavigate } from "react-router";
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

/**
 * 学生首页：一条有序的今日任务队列，而不是三张等权重的功能卡片。
 *
 * 班级、作业指派这些后端概念还不存在，队列完全由 useStudentTodayQueue 从三个
 * 已有的只读接口派生。顺序是产品判断（先清阻塞项，再做有时效的复习，再订正，
 * 最后才是练习），页面本身只负责渲染，不做任何业务判断。
 */
export function StudentLearningApp() {
  const navigate = useNavigate();
  const { pendingConfirmCount, dueReviewCount, unmasteredCount, papers, loading, error, allFailed } = useStudentTodayQueue();

  const rows: QueueRow[] = [];

  if (pendingConfirmCount > 0) {
    rows.push({
      key: "confirm",
      title: "确认新录入的错题",
      description: "先确认识别结果，这些错题才能进入陪练订正。",
      badge: `${pendingConfirmCount} 道`,
      actionLabel: "去确认",
      onAction: () => navigate("/mistakes"),
    });
  }

  if (dueReviewCount > 0) {
    rows.push({
      key: "review",
      title: "今日复习",
      description: "复习是有时效的，过期就失去间隔重复的效果，尽快完成。",
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

  // 练习刻意不计入“今天有 N 件事”。作业指派的后端概念还不存在，已发布试卷会一直
  // 挂在目录里（做完也不会消失），把它们算进待办会让计数永远降不下来，队列也就退化
  // 成了列表。等 class/assignment 落地、能判断“这套卷子是今天布置的”之后，练习才应该
  // 升进上面的队列。
  const practiceRows: QueueRow[] = papers.map((paper) => ({
    key: `paper-${paper.publicationId}`,
    title: paper.title,
    description: paper.started ? "继续上次没做完的练习。" : "还没有开始过这套练习。",
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
        <span className="demo-badge">STUDENT DEMO</span>
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
              : taskCount > 0 ? `今天有 ${taskCount} 件事` : "今天没有待办任务"}
        </h1>
        <p>
          {loading
            ? "正在读取错题、复习计划和练习进度。"
            : allFailed
              ? "错题、复习和练习都没有加载成功。请检查网络后刷新页面重试。"
              : taskCount > 0
                ? "按顺序处理完这条队列，今天的学习任务就完成了。"
                : "没有待确认的错题，也没有到期的复习。可以做下面的练习，或者今天就到这里。"}
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
          {rows.map((row, index) => (
            <li key={row.key} className="today-queue-row">
              <span className="today-queue-index" aria-hidden="true">{index + 1}</span>
              <div className="today-queue-body">
                <h3>{row.title}</h3>
                <p>{row.description}</p>
              </div>
              <span className="today-queue-badge">{row.badge}</span>
              <button
                className="today-queue-action"
                aria-label={`${row.actionLabel}：${row.title}`}
                onClick={row.onAction}
              >{row.actionLabel}</button>
            </li>
          ))}
        </ol>
      )}

      {!loading && !allFailed && (
        <section className="student-practice-section" aria-labelledby="student-practice-heading">
          <h2 id="student-practice-heading">练习</h2>
          {practiceRows.length ? (
            <ol className="student-today-queue" aria-label="练习">
              {practiceRows.map((row) => (
                <li key={row.key} className="today-queue-row">
                  <span className="today-queue-index" aria-hidden="true">卷</span>
                  <div className="today-queue-body">
                    <h3>{row.title}</h3>
                    <p>{row.description}</p>
                  </div>
                  <span className="today-queue-badge">{row.badge}</span>
                  <button
                    className="today-queue-action"
                    aria-label={`${row.actionLabel}：${row.title}`}
                    onClick={row.onAction}
                  >{row.actionLabel}</button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="student-empty-note">老师还没有发布新的练习，练习任务会自动出现在这里。</p>
          )}
        </section>
      )}
    </main>
  );
}
