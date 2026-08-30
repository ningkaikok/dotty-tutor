import type { LibraryItem } from "../../../types/index";

interface TextbookLibraryProps {
  items: LibraryItem[];
  loadingId: string;
  deletingId: string;
  onOpen: (item: LibraryItem) => void;
  onDelete: (item: LibraryItem) => void;
}

export function TextbookLibrary({ items, loadingId, deletingId, onOpen, onDelete }: TextbookLibraryProps) {
  if (!items.length) return null;
  const busy = Boolean(loadingId) || Boolean(deletingId);

  return (
    <section className="library-panel panel">
      <div className="library-heading">
        <div>
          <span className="eyebrow">教材库</span>
          <strong>已持久化教材</strong>
          <small>PDF、处理状态和生成题目已保存在本机，重启后仍可继续。</small>
        </div>
        <span>{items.length} 本</span>
      </div>
      <div className="library-list">
        {items.map((item) => (
          <div className="library-item" key={item.uploadId}>
            <button className="library-open" disabled={busy} onClick={() => onOpen(item)}>
              <span className="library-pdf">PDF</span>
              <span>
                <strong>{item.filename}</strong>
                <small>{item.chapter} · {item.pageCount ?? "?"} 页 · {item.questionCount} 道题</small>
              </span>
              <b>{loadingId === item.uploadId ? "读取中…" : "继续学习 →"}</b>
            </button>
            <button
              className="library-delete"
              disabled={busy}
              title="从教材库移除"
              aria-label={`删除教材 ${item.filename}`}
              onClick={() => onDelete(item)}
            >
              {deletingId === item.uploadId ? "删除中…" : "删除"}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
