import type { QueryClient } from "@tanstack/react-query";
import { queryKeys, type MessagesCache } from "../api/keys";
import type {
  AgentDTO,
  AgentStatusData,
  InvocationDTO,
  MessageDTO,
  ServerEvent,
  TaskDTO,
  TaskDetailDTO,
} from "../api/types";

/** `GET /api/tasks` returns at most this many tasks; the cached list is kept to the same bound. */
const TASK_LIST_LIMIT = 50;

/**
 * Apply one SSE event to the query cache (spec §11.3). Pure with respect to the cache: every
 * update is an immutable replacement, and caches that were never fetched are left absent (the
 * next fetch returns the server's state, which already includes the event).
 */
export function applyEvent(queryClient: QueryClient, ev: ServerEvent): void {
  switch (ev.event) {
    case "message.appended": {
      const { agent, message } = ev.data;
      queryClient.setQueryData<MessagesCache>(queryKeys.messages(agent), (old) =>
        old ? appendMessage(old, message) : undefined,
      );
      return;
    }
    case "invocation.updated": {
      const inv = ev.data.invocation;
      queryClient.setQueryData<MessagesCache>(queryKeys.messages(inv.agent), (old) =>
        old ? updatePending(old, inv) : undefined,
      );
      queryClient.setQueryData<TaskDetailDTO>(queryKeys.task(inv.task_id), (old) =>
        old ? { ...old, invocations: upsertById(old.invocations, inv, "append") } : undefined,
      );
      return;
    }
    case "task.updated": {
      const task = ev.data.task;
      queryClient.setQueryData<TaskDTO[]>(queryKeys.tasks, (old) =>
        old ? upsertById(old, task, "prepend").slice(0, TASK_LIST_LIMIT) : undefined,
      );
      queryClient.setQueryData<TaskDetailDTO>(queryKeys.task(task.id), (old) =>
        old ? { ...old, task } : undefined,
      );
      return;
    }
    case "agent.status": {
      const status = ev.data;
      queryClient.setQueryData<AgentDTO[]>(queryKeys.agents, (old) =>
        old ? old.map((a) => (a.name === status.agent ? patchAgent(a, status) : a)) : undefined,
      );
      return;
    }
  }
}

/** Append to the newest page unless a message with the same id is already cached on any page. */
function appendMessage(cache: MessagesCache, message: MessageDTO): MessagesCache {
  if (cache.pages.some((p) => p.messages.some((m) => m.id === message.id))) return cache;
  const [newest, ...older] = cache.pages;
  if (!newest) return cache;
  const messages = [...newest.messages, message];
  const last = newest.messages[newest.messages.length - 1];
  if (last && last.seq > message.seq) messages.sort((a, b) => a.seq - b.seq);
  return { ...cache, pages: [{ ...newest, messages }, ...older] };
}

/** Pending = queued inbound messages: add when the invocation is `queued`, remove otherwise. */
function updatePending(cache: MessagesCache, inv: InvocationDTO): MessagesCache {
  const [newest, ...older] = cache.pages;
  if (!newest) return cache;
  if (inv.status === "queued") {
    if (newest.pending.some((p) => p.invocation_id === inv.id)) return cache;
    const pending = [
      ...newest.pending,
      {
        invocation_id: inv.id,
        caller: inv.caller,
        inbound_text: inv.inbound_text,
        created_at: inv.created_at,
      },
    ];
    return { ...cache, pages: [{ ...newest, pending }, ...older] };
  }
  if (!cache.pages.some((p) => p.pending.some((x) => x.invocation_id === inv.id))) return cache;
  return {
    ...cache,
    pages: cache.pages.map((p) => ({
      ...p,
      pending: p.pending.filter((x) => x.invocation_id !== inv.id),
    })),
  };
}

function upsertById<T extends { id: string }>(list: T[], item: T, insert: "append" | "prepend"): T[] {
  const index = list.findIndex((x) => x.id === item.id);
  if (index === -1) return insert === "append" ? [...list, item] : [item, ...list];
  const next = list.slice();
  next[index] = item;
  return next;
}

/** Health broadcasts may omit per-user fields; only patch what the event carries. */
function patchAgent(agent: AgentDTO, status: AgentStatusData): AgentDTO {
  return {
    ...agent,
    healthy: status.healthy ?? agent.healthy,
    busy: status.busy ?? agent.busy,
    queue_len: status.queue_len ?? agent.queue_len,
  };
}
