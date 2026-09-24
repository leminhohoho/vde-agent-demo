import { useState } from "react";
import { useDataset } from "../../api/queries";
import { formatCell } from "../../ui/format";

const PAGE_SIZE = 50;

/** Paged dataset rows (`GET /api/datasets/{id}?offset&limit`). */
export function DatasetTable({ id, compact = false }: { id: string; compact?: boolean }) {
  const [offset, setOffset] = useState(0);
  const pageSize = compact ? 10 : PAGE_SIZE;
  const query = useDataset(id, offset, pageSize);

  if (query.isPending) return <div className="muted small">Loading {id}…</div>;
  if (query.isError) return <div className="error-text">{id}: {query.error.message}</div>;
  const ds = query.data;
  const last = Math.min(offset + ds.rows.length, ds.row_count);

  return (
    <div className={`dataset${compact ? " compact" : ""}`}>
      <div className="dataset-head">
        <span className="mono artifact-id">{ds.id}</span>
        {ds.name && <span className="dataset-name">{ds.name}</span>}
        <span className="muted small">
          {ds.row_count.toLocaleString()} rows{ds.truncated && " (truncated at 10 000)"}
        </span>
      </div>
      {!compact && (
        <details className="dataset-sql">
          <summary className="muted small">SQL</summary>
          <pre className="mono">{ds.source_sql}</pre>
        </details>
      )}
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              {ds.columns.map((c) => (
                <th key={c.name} title={c.type}>
                  {c.name}
                  <span className="col-type">{c.type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ds.rows.map((row, i) => (
              <tr key={offset + i}>
                {row.map((cell, j) => (
                  <td key={j} className={typeof cell === "number" ? "num" : cell === null ? "null" : undefined}>
                    {formatCell(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {ds.row_count > pageSize && (
        <div className="pager">
          <button
            type="button"
            className="btn btn-ghost small"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - pageSize))}
          >
            ‹ Prev
          </button>
          <span className="muted small">
            {ds.row_count === 0 ? 0 : offset + 1}–{last} of {ds.row_count.toLocaleString()}
          </span>
          <button
            type="button"
            className="btn btn-ghost small"
            disabled={offset + pageSize >= ds.row_count}
            onClick={() => setOffset(offset + pageSize)}
          >
            Next ›
          </button>
        </div>
      )}
    </div>
  );
}
