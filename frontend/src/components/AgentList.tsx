import type { AgentDTO } from "../api/types";
import { agentColor } from "../ui/artifacts";
import { useUi } from "../ui/UiContext";

interface Props {
  agents: AgentDTO[] | undefined;
  selected: string | null;
  error: Error | null;
}

/** Agents with health dot, busy spinner and this user's queue length; click selects the chat. */
export function AgentList({ agents, selected, error }: Props) {
  const { selectAgent } = useUi();
  return (
    <section className="sidebar-section">
      <div className="section-label">Agents</div>
      {error && <div className="error-text">{error.message}</div>}
      {!agents && !error && <div className="muted small">Loading…</div>}
      <ul className="agent-list">
        {agents?.map((a) => (
          <li key={a.name}>
            <button
              type="button"
              className={`agent-item${a.name === selected ? " selected" : ""}`}
              title={`${a.description}\n${a.healthy ? "healthy" : "unavailable"}`}
              onClick={() => selectAgent(a.name)}
            >
              <span className={`health-dot ${a.healthy ? "ok" : "down"}`} />
              <span className="agent-name" style={{ color: agentColor(a.name) }}>
                {a.name}
              </span>
              <span className="agent-meta">
                {a.busy && <span className="spinner" aria-label="busy" />}
                {a.queue_len > 0 && (
                  <span className="queue-badge" title={`${a.queue_len} queued`}>
                    {a.queue_len}
                  </span>
                )}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
