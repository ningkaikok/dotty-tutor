import MathText from "../../../MathText";
import { useState } from "react";
import type { MistakeItem } from "../../../types";

interface MistakeLibraryProps {
  items: MistakeItem[];
  loading: boolean;
  error: string;
  onCapture: () => void;
  onOpen: (item: MistakeItem) => void;
  onTutor: (item: MistakeItem) => void;
  onArchive: (item: MistakeItem) => void;
  onProgress: () => void;
}

const ERROR_LABELS: Record<string, string> = {
  concept: "概念不清",
  reading: "审题错误",
  calculation: "计算失误",
  missing_step: "步骤遗漏",
  unknown: "完全不会",
  careless: "粗心大意",
};

export function MistakeLibrary({ items, loading, error, onCapture, onOpen, onTutor, onArchive, onProgress }: MistakeLibraryProps) {
  const [activeBook, setActiveBook] = useState<"mistakes" | "advanced">("mistakes");
  const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({});
  const pendingCount = items.filter((item) => item.status === "pending_confirmation").length;
  const unmasteredCount = items.filter((item) => item.status === "unmastered").length;
  const masteredCount = items.filter((item) => item.status === "mastered").length;
  const visibleItems = items.filter((item) => activeBook === "advanced"
    ? item.status === "mastered"
    : item.status !== "mastered");

  return (
    <>
      <section className="mistake-library-hero">
        <div>
          <span className="eyebrow">PERSONAL MISTAKE BOOK</span>
          <h1>我的错题本</h1>
          <p>在线作答的错题会自动进入这里；只有纸质作业需要拍照并确认识别结果。</p>
        </div>
        <div className="mistake-hero-actions">
          <button onClick={onProgress}>查看学习进度</button>
          <button className="mistake-primary-action compact" onClick={onCapture}>录入纸质错题</button>
        </div>
      </section>

      <section className="mistake-summary" aria-label="错题统计">
        <div><strong>{unmasteredCount}</strong><span>待掌握</span></div>
        <div><strong>{pendingCount}</strong><span>待确认</span></div>
        <div><strong>{masteredCount}</strong><span>进阶本</span></div>
      </section>

      <nav className="mistake-book-tabs" aria-label="错题本分类">
        <button className={activeBook === "mistakes" ? "active" : ""} onClick={() => setActiveBook("mistakes")}>
          错题本 <span>{pendingCount + unmasteredCount}</span>
        </button>
        <button className={activeBook === "advanced" ? "active" : ""} onClick={() => setActiveBook("advanced")}>
          进阶本 <span>{masteredCount}</span>
        </button>
      </nav>

      {error && <p className="mistake-error" role="alert">{error}</p>}
      {loading ? (
        <div className="mistake-empty">正在读取错题本…</div>
      ) : visibleItems.length === 0 ? (
        <section className="mistake-empty">
          <span className="empty-sheet" aria-hidden="true" />
          <h2>{activeBook === "advanced" ? "还没有进入进阶本的题目" : "还没有错题"}</h2>
          <p>{activeBook === "advanced" ? "连续答对两道不同变式题后，题目会自动出现在这里。" : "完成互动试卷后，错题会自动出现；也可以补录纸质作业。"}</p>
          {activeBook === "mistakes" && <button className="mistake-primary-action compact" onClick={onCapture}>拍照录入纸质错题</button>}
        </section>
      ) : (
        <section className="mistake-list" aria-label="错题列表">
          {visibleItems.map((item) => (
            <article key={item.mistakeId} className="mistake-list-item">
              {item.sourceImageUrl && !brokenImages[item.mistakeId] ? (
                <img
                  src={item.sourceImageUrl}
                  alt="错题原图"
                  loading="lazy"
                  onError={() => setBrokenImages((current) => ({ ...current, [item.mistakeId]: true }))}
                />
              ) : (
                <div className="mistake-paper-source" aria-label="来自互动试卷">
                  <strong>互动试卷</strong>
                  <span>自动记录</span>
                </div>
              )}
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
                  <button className="primary" onClick={() => onTutor(item)}>{item.status === "mastered" ? "查看验证记录" : "开始陪练"}</button>
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
