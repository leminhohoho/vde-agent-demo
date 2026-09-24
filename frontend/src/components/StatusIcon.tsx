import type { InvocationStatus, TaskStatus } from "../api/types";

type Status = InvocationStatus | TaskStatus;

const ICONS: Record<Exclude<Status, "running">, string> = {
  queued: "⏳",
  completed: "✓",
  failed: "✗",
  cancelled: "⊘",
  rejected: "⛔",
};

export function StatusIcon({ status }: { status: Status }) {
  if (status === "running") {
    return <span className="status-icon status-running spinner" title="running" />;
  }
  return (
    <span className={`status-icon status-${status}`} title={status}>
      {ICONS[status]}
    </span>
  );
}
