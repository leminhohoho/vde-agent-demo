import type { InfiniteData } from "@tanstack/react-query";
import type { MessagesPageDTO } from "./types";

/**
 * Query keys shared by the query hooks and the SSE reducer (spec §11.3).
 * The cache is per selected user (one QueryClient per user), so keys carry no user id.
 */
export const queryKeys = {
  users: ["users"] as const,
  agents: ["agents"] as const,
  messages: (agent: string) => ["messages", agent] as const,
  tasks: ["tasks"] as const,
  task: (id: string) => ["task", id] as const,
  dataset: (id: string, offset: number, limit: number) => ["dataset", id, offset, limit] as const,
  chart: (id: string) => ["chart", id] as const,
  reports: ["reports"] as const,
  report: (id: string) => ["report", id] as const,
};

/**
 * Cache shape of `['messages', agent]`: an infinite query whose `pages[0]` is the newest page
 * (fetched without `before_seq`) and each further page is older. Page param = `before_seq`.
 */
export type MessagesCache = InfiniteData<MessagesPageDTO, number | null>;
