import { useState } from "react";
import type { ChapterSourceLocator, ChapterSourceRevision } from "../../types/chapter";

interface ChapterSourceReviewProps {
  revisions: ChapterSourceRevision[];
  locator?: ChapterSourceLocator;
}

export function ChapterSourceReview({ revisions, locator }: ChapterSourceReviewProps) {
  const [focused, setFocused] = useState<{ revisionId: string; page: number; regionId?: string } | null>(null);
  const [failedPreviews, setFailedPreviews] = useState<Set<string>>(() => new Set());
  const [enlarged, setEnlarged] = useState<{ url: string; label: string } | null>(null);

  const focus = (revisionId: string, page: number, regionId?: string) => {
    setFocused({ revisionId, page, regionId });
    requestAnimationFrame(() => {
      const imageRegion = regionId ? document.getElementById(`source-image-region-${revisionId}-${regionId}`) : null;
      const target = imageRegion ?? document.getElementById(
        regionId ? `source-region-${revisionId}-${regionId}` : `source-${revisionId}-${page}`,
      );
      target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  };

  return (
    <section className="chapter-source-review" aria-label="章节来源页与区域">
      <header>
        <div><span className="eyebrow">原文与定位</span><h2>来源页面</h2></div>
        {locator && <button type="button" onClick={() => focus(locator.sourceRevisionId, locator.page)}>
          回看课程依据：第 {locator.page} 页
        </button>}
      </header>
      {revisions.map((revision) => (
        <div className="chapter-source-version" key={revision.sourceRevisionId}>
          <h3>来源版本 {revision.sourceVersion} · {revision.pageStart}–{revision.pageEnd} 页</h3>
          <p className="chapter-source-license">许可：{revision.license || "待核实"} · 指纹 {revision.fingerprint.slice(0, 12)}</p>
          {revision.issues.map((issue) => <p className="chapter-issue blocking" role="status" key={`${issue.code}-${issue.message}`}>{issue.message}</p>)}
          {revision.pages.map((page) => {
            const pageFocused = focused?.revisionId === revision.sourceRevisionId && focused.page === page.page;
            return (
              <article
                id={`source-${revision.sourceRevisionId}-${page.page}`}
                className={`chapter-source-page${pageFocused ? " focused" : ""}`}
                key={page.page}
                aria-label={`来源第 ${page.page} 页`}
              >
                <div className="chapter-source-page-heading"><strong>第 {page.page} 页</strong><span>{page.regions?.length ?? 0} 个定位区域</span></div>
                {page.previewUrl && !failedPreviews.has(page.previewUrl) ? (
                  <div className="chapter-page-preview-wrap">
                    <img
                      className="chapter-page-preview"
                      src={page.previewUrl}
                      alt={`教材原页，第 ${page.page} 页`}
                      onError={() => setFailedPreviews((current) => new Set(current).add(page.previewUrl!))}
                    />
                    {page.regions?.length ? <div className="chapter-region-overlay" role="group" aria-label={`第 ${page.page} 页原图区域定位`}>
                      {page.regions.map((region, index) => {
                        const regionId = region.regionId || `${page.page}-${index}`;
                        const active = focused?.regionId === regionId && pageFocused;
                        return <button
                          key={regionId}
                          id={`source-image-region-${revision.sourceRevisionId}-${regionId}`}
                          type="button"
                          aria-label={`回看第 ${page.page} 页原图区域 ${index + 1}`}
                          className={active ? "active" : ""}
                          style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%` }}
                          onClick={() => focus(revision.sourceRevisionId, page.page, regionId)}
                        ><span>{index + 1}</span></button>;
                      })}
                    </div> : null}
                    <button type="button" className="chapter-preview-enlarge" onClick={() => setEnlarged({ url: page.previewUrl!, label: `教材原页，第 ${page.page} 页` })}>放大查看原页</button>
                  </div>
                ) : <p className="chapter-preview-unavailable" role="status">{page.previewUrl ? "原页图片加载失败，无法显示教材预览。" : "此来源没有可用的教材原页图片，当前仅供文字复核。"}</p>}
                <p>{page.text || "（本页没有可识别原文）"}</p>
                {page.regions?.length ? (
                  <>
                    <ol className="chapter-source-regions" aria-label={`第 ${page.page} 页区域`}>
                      {page.regions.map((region, index) => <li key={region.regionId || `${page.page}-${index}`}>
                        <button id={`source-region-${revision.sourceRevisionId}-${region.regionId || `${page.page}-${index}`}`} type="button" className={focused?.regionId === (region.regionId || `${page.page}-${index}`) && pageFocused ? "active" : ""} onClick={() => focus(revision.sourceRevisionId, page.page, region.regionId || `${page.page}-${index}`)}>
                          回看区域 {index + 1} · x {region.x.toFixed(2)}，y {region.y.toFixed(2)} · {Math.round(region.width * 100)}% × {Math.round(region.height * 100)}%
                        </button>
                      </li>)}
                    </ol>
                  </>
                ) : <small>当前来源未提供区域坐标，可按页码复核原文。</small>}
                {page.flags?.map((flag) => <span className="chapter-flag" key={flag}>{flag}</span>)}
              </article>
            );
          })}
        </div>
      ))}
      {revisions.length === 0 && <p>还没有可回看的来源版本。</p>}
      {enlarged && <div className="chapter-preview-dialog" role="dialog" aria-modal="true" aria-label={enlarged.label}>
        <button type="button" onClick={() => setEnlarged(null)}>关闭原页放大预览</button>
        <img src={enlarged.url} alt={enlarged.label} />
      </div>}
    </section>
  );
}
