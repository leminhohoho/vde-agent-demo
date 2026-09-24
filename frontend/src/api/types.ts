// DTOs mirroring the REST / SSE contract (spec §10).

export type Role = "user" | "assistant" | "tool";

export interface ToolCallDTO {
  id: string;
  name: string;
  arguments_json: string;
}

export interface MessageDTO {
  id: number;
  seq: number;
  task_id: string;
  invocation_id: string;
  role: Role;
  sender: string | null;
  content: string;
  tool_calls: ToolCallDTO[] | null;
  tool_call_id: string | null;
  compacted: boolean;
  created_at: string;
}

export type TaskStatus = "running" | "completed" | "failed" | "cancelled";

export interface TaskDTO {
  id: string;
  root_agent: string;
  status: TaskStatus;
  created_at: string;
  finished_at: string | null;
}

export type InvocationStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "rejected";

export interface InvocationDTO {
  id: string;
  task_id: string;
  agent: string;
  caller: string;
  parent_id: string | null;
  tool_call_id: string | null;
  depth: number;
  inbound_text: string;
  status: InvocationStatus;
  result_text: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface UserDTO {
  id: string;
  name: string;
}

export interface AgentDTO {
  name: string;
  description: string;
  healthy: boolean;
  busy: boolean;
  queue_len: number;
}

export interface PendingDTO {
  invocation_id: string;
  caller: string;
  inbound_text: string;
  created_at: string;
}

/** `GET /api/agents/{agent}/messages` — newest page, ascending `seq`. */
export interface MessagesPageDTO {
  summary: string | null;
  messages: MessageDTO[];
  pending: PendingDTO[];
}

export interface PostMessageResponseDTO {
  task_id: string;
  invocation_id: string;
}

export interface TaskDetailDTO {
  task: TaskDTO;
  invocations: InvocationDTO[];
}

export interface CancelTaskResponseDTO {
  task: TaskDTO;
}

export interface ColumnDTO {
  name: string;
  type: string;
}

export interface DatasetDTO {
  id: string;
  name: string | null;
  columns: ColumnDTO[];
  row_count: number;
  truncated: boolean;
  source_sql: string;
  rows: unknown[][];
}

export interface ChartDTO {
  id: string;
  title: string;
  dataset_id: string;
  spec: Record<string, unknown>;
}

export interface ReportSummaryDTO {
  id: string;
  title: string;
  created_at: string;
}

export interface ReportDTO {
  id: string;
  title: string;
  markdown: string;
  created_at: string;
}

export interface ErrorEnvelopeDTO {
  error: { code: string; message: string };
}

// ---- SSE (`GET /api/events?user_id=`) ----

export interface MessageAppendedData {
  agent: string;
  message: MessageDTO;
}

export interface InvocationUpdatedData {
  invocation: InvocationDTO;
}

export interface TaskUpdatedData {
  task: TaskDTO;
}

export interface AgentStatusData {
  agent: string;
  healthy: boolean;
  busy: boolean;
  queue_len: number;
}

export type ServerEvent =
  | { event: "message.appended"; data: MessageAppendedData }
  | { event: "invocation.updated"; data: InvocationUpdatedData }
  | { event: "task.updated"; data: TaskUpdatedData }
  | { event: "agent.status"; data: AgentStatusData };

export type ServerEventName = ServerEvent["event"];

export const SERVER_EVENT_NAMES: readonly ServerEventName[] = [
  "message.appended",
  "invocation.updated",
  "task.updated",
  "agent.status",
];
