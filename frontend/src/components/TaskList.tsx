import { useTasks } from "../api/queries";
import { agentColor } from "../ui/artifacts";
import { formatRelative } from "../ui/format";
import { useUi } from "../ui/UiContext";
import { StatusIcon } from "./StatusIcon";

/** Recent tasks (newest first); click shows the task in the Inspector. */
export function TaskList({ activeTaskId }: { activeTaskId: string | null }) {
  const tasks = useTasks();
  const { selectTask } = useUi();
  return (
    <section className="sidebar-section tasks-section">
      <div className="section-label">Tasks</div>
      {tasks.isError && <div className="error-text">{tasks.error.message}</div>}
      {tasks.data?.length === 0 && <div className="muted small">No tasks yet.</div>}
      <ul className="task-list">
        {tasks.data?.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              className={`task-item${t.id === activeTaskId ? " selected" : ""}`}
              onClick={() => selectTask(t.id)}
              title={`${t.id} · ${t.status}`}
            >
              <StatusIcon status={t.status} />
              <span className="task-id mono">{t.id}</span>
              <span className="task-agent" style={{ color: agentColor(t.root_agent) }}>
                {t.root_agent}
              </span>
              <span className="task-time muted">{formatRelative(t.created_at)}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
