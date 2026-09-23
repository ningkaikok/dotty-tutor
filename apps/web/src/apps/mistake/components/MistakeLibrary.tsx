import { RichText } from "../../../RichText";
import { useEffect, useRef, useState } from "react";
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
  const archiveTriggerRef = useRef<HTMLButtonElement | null>(null);
  const archiveCancelRef = useRef<HTMLButtonElement | null>(null);
  const archiveDialogRef = useRef<HTMLElement | null>(null);
  const pendingCount = items.filter((item) => item.status === "pending_confirmation").length;
  const unmasteredCount = items.filter((item) => item.status === "unmastered").length;
  const masteredCount = items.filter((item) => item.status === "mastered").length;
  const visibleItems = items.filter((item) => activeBook === "advanced"
    ? item.status === "mastered"
    : item.status !== "mastered");

  useEffect(() => {
    if (!pendingArchive) {
      archiveTriggerRef.current?.focus();
      return;
    }

    archiveCancelRef.current?.focus();
    const handleDialogKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setPendingArchive(null);
        return;
      }
      if (event.key !== "Tab") return;

      const dialog = archiveDialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
      ));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleDialogKeyDown);
    return () => document.removeEventListener("keydown", handleDialogKeyDown);
  }, [pendingArchive]);

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
                    onClick={(event) => {
                      archiveTriggerRef.current = event.currentTarget;
                      setPendingArchive(item);
                    }}
                  >归档</button>
                </div>
              </article>
            );
          })}
        </section>
      )}
      {pendingArchive && (
        <div className="mistake-dialog-backdrop" role="presentation">
          <section
            className="mistake-dialog"
            ref={archiveDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="archive-mistake-title"
            aria-describedby="archive-mistake-description"
          >
            <h2 id="archive-mistake-title">确认归档这道错题？</h2>
            <p id="archive-mistake-description">归档后会清除这道题的辅导记录，但题目和学习证据仍会保留。</p>
            <div className="mistake-dialog-actions">
              <button ref={archiveCancelRef} onClick={cancelArchive}>取消</button>
              <button
                className="danger"
                onClick={() => {
                  onArchive(pendingArchive);
                  setPendingArchive(null);
                }}
              >确认归档</button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
