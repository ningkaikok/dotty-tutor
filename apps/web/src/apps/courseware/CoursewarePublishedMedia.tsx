import { useEffect, useState } from "react";
import { apiResourceUrl } from "../../api/client";
import { listPublishedCoursewareMedia, type PublishedLessonMedia } from "../../api/lessonMedia";
import { Card, Notice } from "../../components/ui";

/** Media is scoped to the displayed immutable publication; drafts never substitute for a missing public video. */
export function CoursewarePublishedMedia({ coursewareId, publicationId }: { coursewareId: string; publicationId: string }) {
  const key = JSON.stringify([coursewareId, publicationId]);
  const [state, setState] = useState<{ key: string; items: PublishedLessonMedia[]; error: string }>({ key: "", items: [], error: "" });
  useEffect(() => {
    const controller = new AbortController();
    void listPublishedCoursewareMedia(coursewareId, publicationId, controller.signal).then(({ items }) => {
      if (!controller.signal.aborted) setState({ key, items, error: "" });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ key, items: [], error: error instanceof Error ? error.message : "视频读取失败" });
    });
    return () => controller.abort();
  }, [coursewareId, publicationId, key]);
  if (state.key !== key) return null;
  if (state.error) return <Notice tone="warning">发布课件视频暂不可用：{state.error}。课件仍可继续学习。</Notice>;
  if (!state.items.length) return null;
  return <Card className="courseware-published-media"><h2>已审校教学视频</h2>{state.items.map((media) => <div key={media.mediaId}>
    <h3>{media.title}</h3><video controls playsInline preload="metadata" aria-label={media.title}>
      <source src={apiResourceUrl(media.videoUrl)} type="video/mp4" />
      <track kind="captions" src={apiResourceUrl(media.captionsUrl)} srcLang={media.subject === "english" ? "en" : "zh"} default />
    </video><p>{media.attributionText}</p><a href={apiResourceUrl(media.captionsDownloadUrl)} download>下载字幕</a>
  </div>)}</Card>;
}
