import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
} from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { ApiClient } from "./client";
import { queryKeys } from "./keys";

export const ApiContext = createContext<ApiClient>(new ApiClient(null));

export function useApi(): ApiClient {
  return useContext(ApiContext);
}

export const MESSAGE_PAGE_SIZE = 50;

export function useUsers() {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.users, queryFn: () => api.listUsers() });
}

export function useAgents() {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.agents, queryFn: () => api.listAgents() });
}

/** Newest page first; `fetchNextPage` loads the next older page via `before_seq`. */
export function useMessages(agent: string) {
  const api = useApi();
  return useInfiniteQuery({
    queryKey: queryKeys.messages(agent),
    queryFn: ({ pageParam }) => api.getMessages(agent, pageParam, MESSAGE_PAGE_SIZE),
    initialPageParam: null as number | null,
    // Seqs are contiguous from 1 per stack, so anything older exists iff the oldest seq > 1.
    getNextPageParam: (oldest) => {
      const first = oldest.messages[0];
      return first && first.seq > 1 ? first.seq : undefined;
    },
  });
}

export function useTasks() {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.tasks, queryFn: () => api.listTasks() });
}

export function useTask(id: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.task(id ?? ""),
    queryFn: () => api.getTask(id as string),
    enabled: id !== null,
  });
}

export function useDataset(id: string, offset: number, limit: number) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.dataset(id, offset, limit),
    queryFn: () => api.getDataset(id, offset, limit),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
}

export function useChart(id: string) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.chart(id),
    queryFn: () => api.getChart(id),
    staleTime: Infinity,
  });
}

export function useReports() {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.reports, queryFn: () => api.listReports() });
}

export function useReport(id: string) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.report(id),
    queryFn: () => api.getReport(id),
    staleTime: Infinity,
  });
}
