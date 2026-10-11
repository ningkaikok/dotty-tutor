import { useEffect, useState } from "react";
import { lessonMediaPublicAssetUrl, listPublishedLessonMedia, type PublishedLessonMedia } from "../../api/lessonMedia";
import "./media.css";

export type MediaPlayerProps = {
  chapterId: string;
  publicationId: string;
  lessonId: string;
  sourceRevisionId: string;
};

/** Render only the approved video versions matching the currently shown lesson source. */
export default function MediaPlayer({ chapterId, publicationId, lessonId, sourceRevisionId }: MediaPlayerProps) {
  const [items, setItems] = useState<PublishedLessonMedia[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setItems(null);
    void listPublishedLessonMedia(chapterId, publicationId, lessonId, controller.signal)
      .then((response) => setItems(response.sourceRevisionId === sourceRevisionId ? response.items : []))
      .catch(() => { if (!controller.signal.aborted) setItems([]); });
    return () => controller.abort();
  }, [chapterId, publicationId, lessonId, sourceRevisionId]);

  const available = items?.filter((item) => item.sourceRevisionId === sourceRevisionId) ?? [];
  if (!items || available.length === 0) return null;

  return (
    <section className="lesson-media-player" aria-label="课程讲解视频">
      {available.map((item) => (
        <article className="lesson-media-player-card" key={item.mediaId}>
          <h3>{item.title}</h3>
          <video controls playsInline preload="metadata" aria-label={`${item.title}教学视频`}>
            <source src={lessonMediaPublicAssetUrl(item.videoUrl)} type="video/mp4" />
            <track
              kind="captions"
              src={lessonMediaPublicAssetUrl(item.captionsUrl)}
              srcLang={item.subject === "math" ? "zh" : "en"}
              label={item.subject === "math" ? "中文字幕" : "English captions"}
              default
            />
            您的浏览器不支持 HTML 视频。
          </video>
          <div className="lesson-media-player-footer">
            <div>
              {item.sourceTitle && <span>来源：{item.sourceTitle}</span>}
              {item.sourceUrl && <a href={item.sourceUrl} target="_blank" rel="noreferrer">查看教材来源</a>}
              {item.attributionText && <span>{item.attributionText}</span>}
            </div>
            <a href={lessonMediaPublicAssetUrl(item.captionsDownloadUrl)} download={`${item.mediaId}.vtt`}>
              下载字幕（WebVTT）
            </a>
          </div>
        </article>
      ))}
    </section>
  );
}
