import { MarkdownText } from "../Markdown";

interface Props {
  summary: string | null;
  /** Distinct tasks among the loaded compacted messages. */
  taskCount: number;
  /** More (older) history exists that is not loaded yet. */
  moreOlder: boolean;
  expanded: boolean;
  onToggle: () => void;
}

/**
 * Boundary between compacted history and live context. Collapsed: compacted messages are hidden.
 * Expanded: shows the rolling summary, and the compacted messages above it render greyed out.
 */
export function CompactedDivider({ summary, taskCount, moreOlder, expanded, onToggle }: Props) {
  const tasks =
    taskCount > 0 ? `${taskCount}${moreOlder ? "+" : ""} task${taskCount === 1 && !moreOlder ? "" : "s"}` : "earlier tasks";
  return (
    <div className={`compacted-divider${expanded ? " expanded" : ""}`}>
      <button type="button" className="compacted-toggle" onClick={onToggle} aria-expanded={expanded}>
        <span className="rule" />
        <span className="compacted-label">
          {expanded ? "▾" : "▸"} summarized ({tasks})
        </span>
        <span className="rule" />
      </button>
      {expanded && (
        <div className="compacted-summary">
          <div className="compacted-summary-title">Summary the agent sees instead of the greyed messages above</div>
          {summary ? <MarkdownText text={summary} /> : <div className="muted small">No summary yet.</div>}
        </div>
      )}
    </div>
  );
}
