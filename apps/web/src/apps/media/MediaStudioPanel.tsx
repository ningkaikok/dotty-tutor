import { lessonMediaTeacherAssetUrl } from "../../api/lessonMedia";
import { useLessonMediaStudio, type MediaStudioLesson } from "./useLessonMediaStudio";
import "./media.css";

export type MediaStudioPanelProps = {
  chapterId: string;
  publicationId: string;
  lessons: MediaStudioLesson[];
  selectedLessonId?: string;
};

/** Present the teacher workflow; API calls and race-safe state live in the studio hook. */
export function MediaStudioPanel({ chapterId, publicationId, lessons, selectedLessonId }: MediaStudioPanelProps) {
  const studio = useLessonMediaStudio({ chapterId, publicationId, lessons, selectedLessonId });
  const { active } = studio;
  const currentJob = active?.renderJob;

  return (
    <section className="media-studio-panel" aria-label="教学短视频工作台">
      <header className="media-studio-heading">
        <div><span className="media-eyebrow">课程媒体</span><h2>教学短视频</h2></div>
        <div className="media-studio-selectors">
          <label>已发布教程
            <select value={studio.lessonId} onChange={(event) => studio.selectLesson(event.target.value)}>
              {lessons.map((item) => <option key={item.lessonId} value={item.lessonId}>{item.title}</option>)}
            </select>
          </label>
          <button type="button" disabled={studio.busy || !studio.lesson} onClick={studio.createDraft}>从发布讲解创建分镜</button>
        </div>
      </header>
      <div className="media-studio-layout">
        <aside aria-label="视频修订列表">
          <div className="media-studio-list-heading"><strong>版本</strong><button type="button" disabled={studio.busy || !studio.lessonId} onClick={() => void studio.refresh()}>刷新</button></div>
          {studio.items.map((item) => (
            <button className={`media-version${active?.mediaId === item.mediaId ? " selected" : ""}`} key={item.mediaId} type="button" onClick={() => studio.selectMedia(item.mediaId)}>
              <strong>v{item.version}</strong><span>{item.status}</span>
            </button>
          ))}
          {studio.items.length === 0 && <p className="media-muted">此课程还没有视频分镜。</p>}
        </aside>
        <div className="media-studio-content">
          {active ? <>
            <div className="media-version-summary">
              <div><h3>{active.title}</h3><p>{active.sourceTitle ?? "教材来源"} · {active.license} · {active.distributionScope === "student" ? "可发布给学生" : "仅本地审校"}</p></div>
              <span className={`media-status media-status-${active.status}`}>{active.status}</span>
            </div>
            <details>
              <summary>来源快照与版本记录</summary>
              <p>来源修订：{active.sourceRevisionId} · 发布：{active.publicationId} · 分镜模板：{String(active.sourceSnapshot.storyboardTemplateVersion ?? "—")}</p>
              {active.sourceUrl && <a href={active.sourceUrl} target="_blank" rel="noreferrer">打开原教材来源</a>}
              {active.attributionText && <p>{active.attributionText}</p>}
              <pre>{JSON.stringify({ storyboardModel: active.sourceSnapshot.storyboardModel, renderer: active.rendererSnapshot }, null, 2)}</pre>
            </details>
            <div className="media-scenes">
              {active.scenes.map((scene) => (
                <article key={scene.sceneId}>
                  <label htmlFor={`media-scene-${scene.sceneId}`}>片段 {scene.order} · {scene.title}</label>
                  <textarea id={`media-scene-${scene.sceneId}`} value={studio.edits[scene.sceneId] ?? scene.narration} maxLength={2_000}
                    onChange={(event) => studio.changeNarration(scene.sceneId, event.target.value)} />
                  <p className="media-citation">来源页：{scene.sourceRefs.map((ref) => `${ref.page} · ${ref.quote}`).join("；")}</p>
                </article>
              ))}
            </div>
            <div className="media-actions">
              <button type="button" disabled={studio.busy || studio.changedScenes.length === 0 || !["draft", "consistency_failed", "changes_requested", "approved", "published"].includes(active.status)} onClick={studio.saveRevision}>保存为新版本</button>
              {studio.canRender && <button type="button" disabled={studio.busy} onClick={studio.render}>提交受限渲染</button>}
              {studio.rendering && <button type="button" disabled={studio.busy} onClick={studio.cancelRender}>取消渲染</button>}
              {active.status === "approved" && <button type="button" disabled={studio.busy} onClick={studio.publish}>发布给学生</button>}
            </div>
            {currentJob && <p className="media-job-status" role="status">渲染任务 {currentJob.status} · {currentJob.attemptCount}/{currentJob.maxAttempts} 次尝试{currentJob.lastError ? ` · ${currentJob.lastError}` : ""}</p>}
            {active.consistencyReview && <div className="media-checks"><strong>一致性审校：{active.consistencyReview.passed ? "通过" : "未通过"}</strong>{active.consistencyReview.issues.map((issue) => <p key={issue.code}>{issue.message}</p>)}</div>}
            {studio.previewable && <div className="media-preview"><h4>人工审校预览</h4><video controls playsInline preload="metadata" aria-label={`${active.title}审校预览`}>
              <source src={lessonMediaTeacherAssetUrl(active.mediaId, "asset")} type="video/mp4" />
              <track kind="captions" src={lessonMediaTeacherAssetUrl(active.mediaId, "captions")} srcLang={active.subject === "math" ? "zh" : "en"} label={active.subject === "math" ? "中文字幕" : "English captions"} default />
            </video><a href={lessonMediaTeacherAssetUrl(active.mediaId, "captions/download")} download={`${active.mediaId}.vtt`}>下载 VTT 字幕</a></div>}
            {active.status === "consistency_review_required" && <div className="media-review">
              <label htmlFor="media-review-note">人工审校记录</label>
              <textarea id="media-review-note" value={studio.reviewNote} onChange={(event) => studio.setReviewNote(event.target.value)} maxLength={1_000} />
              <div><button type="button" disabled={studio.busy} onClick={() => studio.decide("request_changes")}>要求修订</button><button type="button" disabled={studio.busy} onClick={() => studio.decide("approve")}>批准此版本</button></div>
            </div>}
            {active.reviews.map((review) => <p className="media-review-record" key={review.reviewId}>{review.decision} · {review.actor.displayName || review.actor.actorKind} · {review.note}</p>)}
          </> : <p className="media-muted">选择一门教程，创建来源分镜后开始审校。</p>}
        </div>
      </div>
      {studio.error && <p className="media-error" role="alert">{studio.error}</p>}
    </section>
  );
}

export default MediaStudioPanel;
