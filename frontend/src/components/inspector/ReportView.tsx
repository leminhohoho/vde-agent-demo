import { useReport } from "../../api/queries";
import { formatDateTime } from "../../ui/format";
import { MarkdownText } from "../Markdown";
import { ChartView } from "./ChartView";
import { DatasetTable } from "./DatasetTable";

/** `{{chart:<id>}}` / `{{dataset:<id>}}` embeds; the capture groups make `split` interleave them. */
const EMBED = /\{\{\s*(chart|dataset)\s*:\s*([^}\s]+)\s*\}\}/;

type Segment = { kind: "markdown"; text: string } | { kind: "chart" | "dataset"; id: string };

export function splitReport(markdown: string): Segment[] {
  const parts = markdown.split(EMBED);
  const out: Segment[] = [];
  for (let i = 0; i < parts.length; i += 3) {
    const text = parts[i] ?? "";
    if (text.trim()) out.push({ kind: "markdown", text });
    const kind = parts[i + 1];
    const id = parts[i + 2];
    if ((kind === "chart" || kind === "dataset") && id) out.push({ kind, id });
  }
  return out;
}

/** Saved report: markdown with embedded charts and dataset tables. */
export function ReportView({ id }: { id: string }) {
  const query = useReport(id);
  if (query.isPending) return <div className="muted small">Loading {id}…</div>;
  if (query.isError) return <div className="error-text">{id}: {query.error.message}</div>;
  const report = query.data;
  return (
    <article className="report">
      <header className="report-head">
        <h3>{report.title}</h3>
        <span className="muted small">
          <span className="mono">{report.id}</span> · {formatDateTime(report.created_at)}
        </span>
      </header>
      {splitReport(report.markdown).map((seg, i) => {
        if (seg.kind === "markdown") return <MarkdownText key={i} text={seg.text} />;
        if (seg.kind === "chart") return <ChartView key={i} id={seg.id} />;
        return <DatasetTable key={i} id={seg.id} compact />;
      })}
    </article>
  );
}
