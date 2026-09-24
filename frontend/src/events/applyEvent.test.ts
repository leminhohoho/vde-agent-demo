import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it } from "vitest";
import { queryKeys, type MessagesCache } from "../api/keys";
import type { AgentDTO, InvocationDTO, MessageDTO, TaskDTO, TaskDetailDTO } from "../api/types";
import { applyEvent } from "./applyEvent";

function msg(id: number, seq: number, content = `m${id}`): MessageDTO {
  return {
    id,
    seq,
    task_id: "t_000000000001",
    invocation_id: "inv_000000000001",
    role: "user",
    sender: "user",
    content,
    tool_calls: null,
    tool_call_id: null,
    compacted: false,
    created_at: "2026-09-24T10:00:00.000Z",
  };
}

function inv(id: string, status: InvocationDTO["status"], agent = "data"): InvocationDTO {
  return {
    id,
    task_id: "t_000000000001",
    agent,
    caller: "orchestrator",
    parent_id: null,
    tool_call_id: null,
    depth: 1,
    inbound_text: `inbound ${id}`,
    status,
    result_text: null,
    error: null,
    created_at: "2026-09-24T10:00:00.000Z",
    started_at: null,
    finished_at: null,
  };
}

function task(id: string, status: TaskDTO["status"]): TaskDTO {
  return { id, root_agent: "orchestrator", status, created_at: "2026-09-24T10:00:00.000Z", finished_at: null };
}

function messagesCache(pages: MessageDTO[][]): MessagesCache {
  return {
    pages: pages.map((messages) => ({ summary: null, messages, pending: [] })),
    pageParams: pages.map((_, i) => (i === 0 ? null : 1)),
  };
}

let qc: QueryClient;
beforeEach(() => {
  qc = new QueryClient();
});

describe("message.appended", () => {
  it("appends to the newest page of that agent and dedupes by id", () => {
    qc.setQueryData(queryKeys.messages("data"), messagesCache([[msg(3, 3)], [msg(1, 1), msg(2, 2)]]));
    applyEvent(qc, { event: "message.appended", data: { agent: "data", message: msg(4, 4) } });
    applyEvent(qc, { event: "message.appended", data: { agent: "data", message: msg(4, 4) } });
    applyEvent(qc, { event: "message.appended", data: { agent: "data", message: msg(2, 2) } });

    const cache = qc.getQueryData<MessagesCache>(queryKeys.messages("data"))!;
    expect(cache.pages[0]!.messages.map((m) => m.id)).toEqual([3, 4]);
    expect(cache.pages[1]!.messages.map((m) => m.id)).toEqual([1, 2]);
  });

  it("does not touch other agents or create caches that were never fetched", () => {
    qc.setQueryData(queryKeys.messages("data"), messagesCache([[msg(1, 1)]]));
    applyEvent(qc, { event: "message.appended", data: { agent: "compare", message: msg(9, 1) } });
    expect(qc.getQueryData(queryKeys.messages("compare"))).toBeUndefined();
    expect(qc.getQueryData<MessagesCache>(queryKeys.messages("data"))!.pages[0]!.messages).toHaveLength(1);
  });
});

describe("invocation.updated", () => {
  it("adds pending while queued and removes it once it starts", () => {
    qc.setQueryData(queryKeys.messages("data"), messagesCache([[msg(1, 1)]]));
    applyEvent(qc, { event: "invocation.updated", data: { invocation: inv("inv_a", "queued") } });
    applyEvent(qc, { event: "invocation.updated", data: { invocation: inv("inv_a", "queued") } });
    applyEvent(qc, { event: "invocation.updated", data: { invocation: inv("inv_b", "queued") } });
    let pending = qc.getQueryData<MessagesCache>(queryKeys.messages("data"))!.pages[0]!.pending;
    expect(pending.map((p) => [p.invocation_id, p.caller, p.inbound_text])).toEqual([
      ["inv_a", "orchestrator", "inbound inv_a"],
      ["inv_b", "orchestrator", "inbound inv_b"],
    ]);

    applyEvent(qc, { event: "invocation.updated", data: { invocation: inv("inv_a", "running") } });
    applyEvent(qc, { event: "invocation.updated", data: { invocation: inv("inv_b", "cancelled") } });
    pending = qc.getQueryData<MessagesCache>(queryKeys.messages("data"))!.pages[0]!.pending;
    expect(pending).toEqual([]);
  });

  it("upserts the invocation into its task detail", () => {
    const detail: TaskDetailDTO = { task: task("t_000000000001", "running"), invocations: [inv("inv_a", "running", "orchestrator")] };
    qc.setQueryData(queryKeys.task("t_000000000001"), detail);
    applyEvent(qc, { event: "invocation.updated", data: { invocation: inv("inv_b", "queued") } });
    applyEvent(qc, { event: "invocation.updated", data: { invocation: inv("inv_a", "completed", "orchestrator") } });
    const got = qc.getQueryData<TaskDetailDTO>(queryKeys.task("t_000000000001"))!;
    expect(got.invocations.map((i) => [i.id, i.status])).toEqual([
      ["inv_a", "completed"],
      ["inv_b", "queued"],
    ]);
  });
});

describe("task.updated", () => {
  it("prepends new tasks, replaces known ones, and patches the task detail", () => {
    qc.setQueryData(queryKeys.tasks, [task("t_old", "completed")]);
    qc.setQueryData<TaskDetailDTO>(queryKeys.task("t_old"), { task: task("t_old", "running"), invocations: [] });
    applyEvent(qc, { event: "task.updated", data: { task: task("t_new", "running") } });
    applyEvent(qc, { event: "task.updated", data: { task: task("t_new", "failed") } });
    applyEvent(qc, { event: "task.updated", data: { task: task("t_old", "cancelled") } });

    expect(qc.getQueryData<TaskDTO[]>(queryKeys.tasks)!.map((t) => [t.id, t.status])).toEqual([
      ["t_new", "failed"],
      ["t_old", "cancelled"],
    ]);
    expect(qc.getQueryData<TaskDetailDTO>(queryKeys.task("t_old"))!.task.status).toBe("cancelled");
    expect(qc.getQueryData(queryKeys.task("t_new"))).toBeUndefined();
  });
});

describe("agent.status", () => {
  it("patches only the named agent", () => {
    const agents: AgentDTO[] = [
      { name: "data", description: "d", healthy: true, busy: false, queue_len: 0 },
      { name: "report", description: "r", healthy: true, busy: false, queue_len: 0 },
    ];
    qc.setQueryData(queryKeys.agents, agents);
    applyEvent(qc, { event: "agent.status", data: { agent: "data", healthy: false, busy: true, queue_len: 2 } });
    expect(qc.getQueryData<AgentDTO[]>(queryKeys.agents)).toEqual([
      { name: "data", description: "d", healthy: false, busy: true, queue_len: 2 },
      agents[1],
    ]);
  });
});
