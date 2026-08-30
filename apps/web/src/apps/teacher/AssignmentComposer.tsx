import type { PublicationSummary } from "../../types/publication";

interface Props {
  publications: PublicationSummary[];
  publicationId: string;
  title: string;
  dueDate: string;
  disabled: boolean;
  onPublicationChange: (value: string) => void;
  onTitleChange: (value: string) => void;
  onDueDateChange: (value: string) => void;
  onAnalyze: () => void;
}

export function AssignmentComposer(props: Props) {
  return (
    <div className="teacher-form assignment-form">
      <label>
        已发布试卷
        <select value={props.publicationId} onChange={(event) => props.onPublicationChange(event.target.value)}>
          <option value="">请选择</option>
          {props.publications.map((item) => <option key={item.publicationId} value={item.publicationId}>{item.title}</option>)}
        </select>
      </label>
      <label>
        作业名称
        <input value={props.title} onChange={(event) => props.onTitleChange(event.target.value)} placeholder="默认使用试卷名称" />
      </label>
      <label>
        截止日期
        <input type="date" value={props.dueDate} onChange={(event) => props.onDueDateChange(event.target.value)} />
      </label>
      <button onClick={props.onAnalyze} disabled={props.disabled || !props.publicationId}>
        {props.disabled ? "分析中…" : "分析并生成计划"}
      </button>
    </div>
  );
}
