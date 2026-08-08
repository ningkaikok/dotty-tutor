import MathText from "../../../MathText";
import type { MistakeItem } from "../../../types";

interface MistakeLibraryProps {
  items: MistakeItem[];
  loading: boolean;
  error: string;
  onCapture: () => void;
  onOpen: (item: MistakeItem) => void;
  onTutor: (item: MistakeItem) => void;
  onArchive: (item: MistakeItem) => void;
}

const ERROR_LABELS: Record<string, string> = {
  concept: "概念不清",
  reading: "审题错误",
  calculation: "计算失误",
  missing_step: "步骤遗漏",
  unknown: "完全不会",
  careless: "粗心大意",
};

export function MistakeLibrary({ items, loading, error, onCapture, onOpen, onTutor, onArchive }: MistakeLibraryProps) {
  const pendingCount = items.filter((item) => item.status === "pending_confirmation").length;
  const unmasteredCount = items.filter((item) => item.status === "unmastered").length;

  return (
    <>
      <section className="mistake-library-hero">
        <div>
          <span className="eyebrow">PERSONAL MISTAKE BOOK</span>
          <h1>我的错题本</h1>
          <p>先确认 AI 的识别与归类，再进入后续陪练。数据不确认，就不让错误继续传播。</p>
        </div>
        <button className="mistake-primary-action compact" onClick={onCapture}>录入一道错题</button>
      </section>

      <section className="mistake-summary" aria-label="错题统计">
        <div><strong>{items.length}</strong><span>全部错题</span></div>
        <div><strong>{pendingCount}</strong><span>待确认</span></div>
        <div><strong>{unmasteredCount}</strong><span>待掌握</span></div>
      </section>

      {error && <p className="mistake-error" role="alert">{error}</p>}
      {loading ? (
        <div className="mistake-empty">正在读取错题本…</div>
      ) : items.length === 0 ? (
        <section className="mistake-empty">
          <span className="empty-sheet" aria-hidden="true" />
          <h2>还没有错题</h2>
          <p>从一道最近做错的初中数学题开始，先建立最小学习闭环。</p>
          <button className="mistake-primary-action compact" onClick={onCapture}>拍照录入第一题</button>
        </section>
      ) : (
        <section className="mistake-list" aria-label="错题列表">
          {items.map((item) => (
            <article key={item.mistakeId} className="mistake-list-item">
              <img src={item.sourceImageUrl} alt="错题原图" loading="lazy" />
              <div className="mistake-list-content">
                <div className="mistake-list-meta">
                  <span className={`mistake-status ${item.status}`}>
                    {item.status === "pending_confirmation" ? "待确认" : item.status === "mastered" ? "已掌握" : "待掌握"}
                  </span>
                  <span>{item.gradeBand} · {item.subject}</span>
                  {item.errorReason && <span>{ERROR_LABELS[item.errorReason]}</span>}
                </div>
                <MathText text={item.questionPayload.question.prompt} className="mistake-list-prompt" />
                <small>{item.chapter} · {item.knowledgePoint}</small>
              </div>
              <div className="mistake-list-actions">
                {item.status !== "pending_confirmation" && (
                  <button className="primary" onClick={() => onTutor(item)}>开始陪练</button>
                )}
                <button onClick={() => onOpen(item)}>{item.status === "pending_confirmation" ? "继续确认" : "查看并编辑"}</button>
                <button className="danger" onClick={() => onArchive(item)}>归档</button>
              </div>
            </article>
          ))}
        </section>
      )}
    </>
  );
}
