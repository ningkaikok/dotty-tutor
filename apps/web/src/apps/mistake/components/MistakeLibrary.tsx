import { RichText } from "../../../RichText";
import { useState } from "react";
import { Button, Dialog } from "../../../components/ui";
import type { MistakeItem } from "../../../types/index";
import { displayedPrompt } from "../../../questionPresentation";
import { errorReasonLabel } from "../errorReasons";

interface MistakeLibraryProps {
  items: MistakeItem[];
  loading: boolean;
  error: string;
  onCapture: () => void;
  onOpen: (item: MistakeItem) => void;
  onTutor: (item: MistakeItem) => void;
  onArchive: (item: MistakeItem) => void;
}

export function MistakeLibrary({ items, loading, error, onCapture, onOpen, onTutor, onArchive }: MistakeLibraryProps) {
  const [activeBook, setActiveBook] = useState<"mistakes" | "advanced">("mistakes");
  const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({});
  const [pendingArchive, setPendingArchive] = useState<MistakeItem | null>(null);
  const pendingCount = items.filter((item) => item.status === "pending_confirmation").length;
  const unmasteredCount = items.filter((item) => item.status === "unmastered").length;
  const masteredCount = items.filter((item) => item.status === "mastered").length;
  const visibleItems = items.filter((item) => activeBook === "advanced"
    ? item.status === "mastered"
    : item.status !== "mastered");

  const cancelArchive = () => setPendingArchive(null);

  return (
    <>
      <section className="mistake-library-hero">
        <div>
          <h1>我的错题本</h1>
          <p>在线作答的错题会自动进入这里；只有纸质作业需要拍照并确认识别结果。</p>
        </div>
        <div className="mistake-hero-actions">
          <button className="mistake-primary-action compact" onClick={onCapture}>录入纸质错题</button>
        </div>
      </section>

      <section className="mistake-summary" aria-label="错题统计">
        <div><strong>{unmasteredCount}</strong><span>待掌握</span></div>
        <div><strong>{pendingCount}</strong><span>待确认</span></div>
        <div><strong>{masteredCount}</strong><span>已掌握</span></div>
      </section>

      <nav className="mistake-book-tabs" aria-label="错题本分类">
        <button className={activeBook === "mistakes" ? "active" : ""} onClick={() => setActiveBook("mistakes")}>
          错题本 <span>{pendingCount + unmasteredCount}</span>
        </button>
        <button className={activeBook === "advanced" ? "active" : ""} onClick={() => setActiveBook("advanced")}>
          已掌握 <span>{masteredCount}</span>
        </button>
      </nav>

      {error && <p className="mistake-error" role="alert">{error}</p>}
      {loading ? (
        <div className="mistake-empty">正在读取错题本…</div>
      ) : visibleItems.length === 0 ? (
        <section className="mistake-empty">
          <span className="empty-sheet" aria-hidden="true" />
          <h2>{activeBook === "advanced" ? "还没有已掌握的题目" : "还没有错题"}</h2>
          <p>{activeBook === "advanced" ? "达到掌握策略门槛后，题目会自动出现在这里。" : "完成练习后，错题会自动出现；也可以补录纸质作业。"}</p>
          {activeBook === "mistakes" && <button className="mistake-primary-action compact" onClick={onCapture}>拍照录入纸质错题</button>}
        </section>
      ) : (
        <section className="mistake-list" aria-label="错题列表">
          {visibleItems.map((item) => {
            // 后端只在通过门禁时写入 AI 归因，unknown 是"没有可用分类"的兜底值，
            // 两者都不该当成 Dotty 的判断展示出来。
            const aiReason = item.aiErrorReason && item.aiErrorReason !== "unknown" ? item.aiErrorReason : undefined;
            return (
              <article key={item.mistakeId} className="mistake-list-item">
                {item.sourceImageUrl && !brokenImages[item.mistakeId] ? (
                  <img
                    src={item.sourceImageUrl}
                    alt="错题原图"
                    loading="lazy"
                    onError={() => setBrokenImages((current) => ({ ...current, [item.mistakeId]: true }))}
                  />
                ) : (
                  <div className="mistake-paper-source" aria-label="来自练习">
                    <strong>练习</strong>
                    <span>自动记录</span>
                  </div>
                )}
                <div className="mistake-list-content">
                  <div className="mistake-list-meta">
                    <span className={`mistake-status ${item.status}`}>
                      {item.status === "pending_confirmation" ? "待确认" : item.status === "mastered" ? "已掌握" : "待掌握"}
                    </span>
                    <span>{item.gradeBand} · {item.subject}</span>
                    {item.errorReason && (
                      <span>{aiReason ? `自评：${errorReasonLabel(item.errorReason)}` : errorReasonLabel(item.errorReason)}</span>
                    )}
                    {aiReason && <span>Dotty：{errorReasonLabel(aiReason)}</span>}
                  </div>
                  <RichText text={displayedPrompt(item.questionPayload.question)} className="mistake-list-prompt" />
                  <small>{item.chapter} · {item.knowledgePoint}</small>
                </div>
                <div className="mistake-list-actions">
                  {item.status !== "pending_confirmation" && (
                    <button className="primary" onClick={() => onTutor(item)}>{item.status === "mastered" ? "查看验证记录" : "开始辅导"}</button>
                  )}
                  <button onClick={() => onOpen(item)}>{item.status === "pending_confirmation" ? "继续确认" : "查看并编辑"}</button>
                  <button
                    className="danger"
                    onClick={() => setPendingArchive(item)}
                  >归档</button>
                </div>
              </article>
            );
          })}
        </section>
      )}
      {/* Stays mounted so closing the native dialog returns focus to the archive trigger. */}
      <Dialog
        open={pendingArchive !== null}
        onClose={cancelArchive}
        title="确认归档这道错题？"
        description="归档后会清除这道题的辅导记录，但题目和学习证据仍会保留。"
        closeButton={false}
        actions={<>
          <Button onClick={cancelArchive}>取消</Button>
          <Button
            variant="danger"
            onClick={() => {
              if (pendingArchive) onArchive(pendingArchive);
              cancelArchive();
            }}
          >确认归档</Button>
        </>}
      />

    </>
  );
}
