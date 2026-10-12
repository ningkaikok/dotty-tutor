import { useEffect, useState } from "react";
import { loadCoursewareCoverage, type CoursewareCoverage } from "../../api/courseware";
import { Badge, Card, Notice, Skeleton } from "../../components/ui";

/** Report the whole saved source range, even when the current lesson covers only a small segment. */
export function CoursewareCoveragePanel({ chapterId, revisionKey }: { chapterId: string; revisionKey?: number }) {
  const [state, setState] = useState<{ chapterId: string; report?: CoursewareCoverage; error?: string }>({ chapterId: "" });
  useEffect(() => {
    const controller = new AbortController();
    setState({ chapterId });
    void loadCoursewareCoverage(chapterId, controller.signal).then((report) => {
      if (!controller.signal.aborted) setState({ chapterId, report });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ chapterId, error: error instanceof Error ? error.message : "无法读取单元覆盖" });
    });
    return () => controller.abort();
  }, [chapterId, revisionKey]);
  const { report, error } = state.chapterId === chapterId ? state : { report: undefined, error: undefined };
  if (error) return <Notice tone="danger">完整单元覆盖暂时无法读取：{error}</Notice>;
  if (!report) return <Skeleton label="正在核对完整单元覆盖…" rows={1} />;
  const gaps = [
    ["缺失来源", report.missingSourcePages], ["未规划", report.unplannedPages],
    ["未生成", report.ungeneratedPages], ["未复核", report.unreviewedPages], ["未发布", report.unpublishedPages],
  ] as const;
  return <Card className="courseware-coverage" aria-label="完整单元覆盖">
    <div className="ui-actions"><h2>完整单元覆盖</h2><Badge tone={report.complete ? "success" : "warning"}>{report.complete ? "结构覆盖完整" : "仍有缺口"}</Badge></div>
    <p>教材原页 {report.pageStart}–{report.pageEnd}。页段通过不等于完整单元通过；教学质量仍需教师验收。</p>
    <dl>{gaps.map(([label, pages]) => <div key={label}><dt>{label}</dt><dd>{pages.length ? `原页 ${pages.join("、")}` : "无缺口"}</dd></div>)}</dl>
  </Card>;
}
