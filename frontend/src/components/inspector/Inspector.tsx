import { useReports } from "../../api/queries";
import { artifactKind } from "../../ui/artifacts";
import { formatDateTime } from "../../ui/format";
import { useUi } from "../../ui/UiContext";
import { ChartView } from "./ChartView";
import { DatasetTable } from "./DatasetTable";
import { ReportView } from "./ReportView";
import { TaskTree } from "./TaskTree";

/** Right column: the selected task's invocation tree, or an artifact viewer. */
export function Inspector({ taskId }: { taskId: string | null }) {
  const { tab, setTab } = useUi();
  return (
    <div className="inspector-inner">
      <div className="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "task"}
          className={`tab${tab === "task" ? " active" : ""}`}
          onClick={() => setTab("task")}
        >
          Task
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "artifact"}
          className={`tab${tab === "artifact" ? " active" : ""}`}
          onClick={() => setTab("artifact")}
        >
          Artifacts
        </button>
      </div>
      <div className="inspector-body">
        {tab === "task" ? (
          taskId ? (
            <TaskTree taskId={taskId} />
          ) : (
            <div className="muted small">No tasks yet. Message an agent to start one.</div>
          )
        ) : (
          <ArtifactPanel />
        )}
      </div>
    </div>
  );
}

function ArtifactPanel() {
  const { artifactId, recentArtifacts, openArtifact } = useUi();
  const reports = useReports();
  return (
    <div className="artifact-panel">
      {recentArtifacts.length > 0 && (
        <div className="recent-artifacts">
          {recentArtifacts.map((id) => (
            <button
              key={id}
              type="button"
              className={`artifact-chip artifact-${artifactKind(id)}${id === artifactId ? " active" : ""}`}
              onClick={() => openArtifact(id)}
            >
              {id}
            </button>
          ))}
        </div>
      )}
      {artifactId ? (
        <ArtifactViewer id={artifactId} />
      ) : (
        <div className="muted small">Click a ds_…, ch_… or rp_… id in any chat to open it here.</div>
      )}
      <section className="reports-list">
        <div className="section-label">Reports</div>
        {reports.isError && <div className="error-text">{reports.error.message}</div>}
        {reports.data?.length === 0 && <div className="muted small">No reports yet.</div>}
        <ul>
          {reports.data?.map((r) => (
            <li key={r.id}>
              <button type="button" className="report-item" onClick={() => openArtifact(r.id)}>
                <span className="report-title">{r.title}</span>
                <span className="muted small">{formatDateTime(r.created_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function ArtifactViewer({ id }: { id: string }) {
  switch (artifactKind(id)) {
    case "dataset":
      return <DatasetTable key={id} id={id} />;
    case "chart":
      return <ChartView key={id} id={id} />;
    case "report":
      return <ReportView key={id} id={id} />;
  }
}
