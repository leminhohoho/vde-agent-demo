import type {
  AgentDTO,
  CancelTaskResponseDTO,
  ChartDTO,
  DatasetDTO,
  ErrorEnvelopeDTO,
  MessagesPageDTO,
  PostMessageResponseDTO,
  ReportDTO,
  ReportSummaryDTO,
  TaskDTO,
  TaskDetailDTO,
  TaskStatus,
  UserDTO,
} from "./types";

/** Error raised for any non-2xx response; `code` comes from the `{error: {code, message}}` envelope. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelopeDTO {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

type Query = Record<string, string | number | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

const seg = encodeURIComponent;

/** Typed wrapper over the BE REST API; adds `X-User-Id` when a user is selected. */
export class ApiClient {
  readonly userId: string | null;

  constructor(userId: string | null) {
    this.userId = userId;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.userId) headers["X-User-Id"] = this.userId;
    if (body !== undefined) headers["Content-Type"] = "application/json";

    let res: Response;
    try {
      res = await fetch(path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      throw new ApiError(0, "network_error", err instanceof Error ? err.message : String(err));
    }

    const text = await res.text();
    let parsed: unknown = undefined;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = undefined;
      }
    }

    if (!res.ok) {
      if (isErrorEnvelope(parsed)) {
        throw new ApiError(res.status, parsed.error.code, parsed.error.message);
      }
      throw new ApiError(res.status, `http_${res.status}`, text || res.statusText || "request failed");
    }
    if (text && parsed === undefined) {
      throw new ApiError(res.status, "invalid_response", "response is not valid JSON");
    }
    return parsed as T;
  }

  listUsers(): Promise<UserDTO[]> {
    return this.request("GET", "/api/users");
  }

  createUser(name: string): Promise<UserDTO> {
    return this.request("POST", "/api/users", { name });
  }

  listAgents(): Promise<AgentDTO[]> {
    return this.request("GET", "/api/agents");
  }

  getMessages(agent: string, beforeSeq: number | null, limit: number): Promise<MessagesPageDTO> {
    return this.request(
      "GET",
      withQuery(`/api/agents/${seg(agent)}/messages`, { before_seq: beforeSeq, limit }),
    );
  }

  postMessage(agent: string, content: string): Promise<PostMessageResponseDTO> {
    return this.request("POST", `/api/agents/${seg(agent)}/messages`, { content });
  }

  listTasks(status?: TaskStatus): Promise<TaskDTO[]> {
    return this.request("GET", withQuery("/api/tasks", { status }));
  }

  getTask(id: string): Promise<TaskDetailDTO> {
    return this.request("GET", `/api/tasks/${seg(id)}`);
  }

  cancelTask(id: string): Promise<CancelTaskResponseDTO> {
    return this.request("POST", `/api/tasks/${seg(id)}/cancel`);
  }

  getDataset(id: string, offset: number, limit: number): Promise<DatasetDTO> {
    return this.request("GET", withQuery(`/api/datasets/${seg(id)}`, { offset, limit }));
  }

  getChart(id: string): Promise<ChartDTO> {
    return this.request("GET", `/api/charts/${seg(id)}`);
  }

  listReports(): Promise<ReportSummaryDTO[]> {
    return this.request("GET", "/api/reports");
  }

  getReport(id: string): Promise<ReportDTO> {
    return this.request("GET", `/api/reports/${seg(id)}`);
  }
}
