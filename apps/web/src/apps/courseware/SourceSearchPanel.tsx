import { useId, useState } from "react";
import { apiResourceUrl } from "../../api/client";
import { useLearnerId } from "../../api/identity";
import { Button, Card, Notice } from "../../components/ui";
import { useCoursewareSourceSearch } from "./useCoursewareSourceSearch";

/** Original excerpts and page previews stay available when the optional model cannot answer. */
export function SourceSearchPanel({ coursewareId, publicationId, onOpenPage }: {
  coursewareId: string; publicationId: string; onOpenPage?: (pageId: string) => void;
}) {
  const inputId = useId();
  const learnerId = useLearnerId();
  const scope = JSON.stringify([coursewareId, publicationId, learnerId]);
  const [question, setQuestion] = useState({ scope, text: "" });
  const query = question.scope === scope ? question.text : "";
  const state = useCoursewareSourceSearch(coursewareId, publicationId, learnerId);
  return <Card className="courseware-source-search">
    <details><summary>查教材原文与讲解依据</summary>
      <form onSubmit={(event) => { event.preventDefault(); void state.submit(query, false); }}>
        <label htmlFor={inputId}>教材关键词或问题</label>
        <input id={inputId} value={query} maxLength={500} onChange={(event) => setQuestion({ scope, text: event.target.value })} placeholder="例如：一次函数、细胞结构" />
        <Button type="submit" busy={state.busy} disabled={!query.trim()}>查找原文</Button>
        <Button busy={state.busy} disabled={!query.trim()} onClick={() => void state.submit(query, true)}>依据原文讲解</Button>
      </form>
      {state.busy && <p role="status">正在查找当前发布版本的教材依据…</p>}
      {state.error && <Notice tone="warning">{state.error}。请重试，或继续学习当前课件。</Notice>}
      {state.explanation && <p aria-live="polite">{state.explanation}</p>}
      {!state.busy && state.searched && !state.error && !state.items.length && !state.explanation && <p role="status">未找到教材原文，请缩短问题或换一个关键词。</p>}
      {state.items.length > 0 && <ol aria-label="教材原文依据">{state.items.map((item) => <li key={item.documentId}>
        <p>{item.title} · 原页 {item.sourcePage} · 发布版本 {item.publicationVersion}</p>
        <blockquote>{item.excerpt}</blockquote>
        <a href={apiResourceUrl(item.previewUrl)} target="_blank" rel="noreferrer">查看教材原页 {item.sourcePage}</a>
        {onOpenPage && <Button variant="quiet" onClick={() => onOpenPage(item.coursewarePageId)}>回到引用课件页</Button>}
      </li>)}</ol>}
      <p>只检索当前已发布课件引用的教材句子。AI 讲解仍需对照原文核对。</p>
    </details>
  </Card>;
}
