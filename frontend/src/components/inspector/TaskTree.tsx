import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { queryKeys } from "../../api/keys";
import { useApi, useTask } from "../../api/queries";
import type { InvocationDTO, TaskDetailDTO } from "../../api/types";
import { agentColor } from "../../ui/artifacts";
import { formatDuration, truncate } from "../../ui/format";
import { useUi } from "../../ui/UiContext";
import { ArtifactText } from "../ArtifactLink";
import { StatusIcon } from "../StatusIcon";

interface Node {
  inv: InvocationDTO;
  children: Node[];
}

/** Flat invocations → forest via `parent_id`, siblings in creation order. */
export function buildTree(invocations: InvocationDTO[]): Node[] {
  const nodes = new Map<string, Node>();
  const sorted = [...invocations].sort((a, b) => a.created_at.localeCompare(b.created_at));
  for (const inv of sorted) nodes.set(inv.id, { inv, children: [] });
  const roots: Node[] = [];
  for (const node of nodes.values()) {
    const parent = node.inv.parent_id ? nodes.get(node.inv.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

export function TaskTree({ taskId }: { taskId: string }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const query = useTask(taskId);
  const cancel = useMutation({
    mutationFn: () => api.cancelTask(taskId),
    onSuccess: ({ task }) => {
      queryClient.setQueryData<TaskDetailDTO>(queryKeys.task(taskId), (old) => (old ? { ...old, task } : old));
      void queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) });
    },
  });
  const roots = useMemo(() => buildTree(query.data?.invocations ?? []), [query.data]);

  if (query.isPending) return <div className="muted small">Loading task…</div>;
  if (query.isError) return <div className="error-text">{query.error.message}</div>;
  const { task } = query.data;

  return (
    <div className="task-tree">
      <div className="task-tree-head">
        <StatusIcon status={task.status} />
        <span className="mono">{task.id}</span>
        <span className={`status-text status-${task.status}`}>{task.status}</span>
        <span className="muted small">{formatDuration(task.created_at, task.finished_at)}</span>
        {task.status === "running" && (
          <button
            type="button"
            className="btn btn-danger small cancel-btn"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
            title="Cancel this task"
          >
            ✕ Cancel
          </button>
        )}
      </div>
      {cancel.error && <div className="error-text">{cancel.error.message}</div>}
      <ul className="tree">
        {roots.map((n) => (
          <TreeNode key={n.inv.id} node={n} />
        ))}
      </ul>
    </div>
  );
}

function TreeNode({ node }: { node: Node }) {
  const { selectAgent } = useUi();
  const { inv } = node;
  const tooltip = [inv.id, inv.error ? `error: ${inv.error}` : null].filter(Boolean).join("\n");
  return (
    <li className={`tree-node inv-${inv.status}`}>
      <div className="tree-row" title={tooltip}>
        <StatusIcon status={inv.status} />
        <button
          type="button"
          className="tree-agent"
          style={{ color: agentColor(inv.agent) }}
          onClick={() => selectAgent(inv.agent)}
          title={`Open ${inv.agent}'s chat`}
        >
          {inv.agent}
        </button>
        <span className="muted small">← {inv.caller}</span>
        <span className="muted small tree-time">{formatDuration(inv.started_at, inv.finished_at)}</span>
      </div>
      <div className="tree-detail">
        <div className="tree-inbound">
          <ArtifactText text={truncate(inv.inbound_text, 160)} />
        </div>
        {inv.error && <div className="tree-error">{inv.error}</div>}
        {inv.result_text && (
          <div className="tree-result">
            ↳ <ArtifactText text={truncate(inv.result_text, 200)} />
          </div>
        )}
      </div>
      {node.children.length > 0 && (
        <ul className="tree">
          {node.children.map((c) => (
            <TreeNode key={c.inv.id} node={c} />
          ))}
        </ul>
      )}
    </li>
  );
}
