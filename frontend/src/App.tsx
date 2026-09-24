import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { ApiClient } from "./api/client";
import { ApiContext, useAgents, useTasks } from "./api/queries";
import { AgentList } from "./components/AgentList";
import { ChatPane } from "./components/chat/ChatPane";
import { Inspector } from "./components/inspector/Inspector";
import { TaskList } from "./components/TaskList";
import { UserPicker } from "./components/UserPicker";
import { useEventStream, type StreamState } from "./events/useEventStream";
import { UiProvider, useUi } from "./ui/UiContext";

const USER_KEY = "vdagent.userId";
const DEFAULT_AGENT = "orchestrator";

function storedUser(): string | null {
  try {
    return localStorage.getItem(USER_KEY);
  } catch {
    return null;
  }
}

/** One QueryClient (cache) and one ApiClient per selected user; switching users starts fresh. */
export function App() {
  const [userId, setUserId] = useState<string | null>(storedUser);
  const selectUser = useCallback((id: string | null) => {
    setUserId(id);
    try {
      if (id) localStorage.setItem(USER_KEY, id);
      else localStorage.removeItem(USER_KEY);
    } catch {
      // storage unavailable: selection just isn't persisted
    }
  }, []);

  const queryClient = useMemo(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 } },
      }),
    // A fresh cache per user: query keys carry no user id.
    [userId],
  );
  const api = useMemo(() => new ApiClient(userId), [userId]);

  return (
    <QueryClientProvider client={queryClient}>
      <ApiContext.Provider value={api}>
        <UiProvider key={userId ?? "none"}>
          <Shell userId={userId} onSelectUser={selectUser} />
        </UiProvider>
      </ApiContext.Provider>
    </QueryClientProvider>
  );
}

function Shell({ userId, onSelectUser }: { userId: string | null; onSelectUser: (id: string | null) => void }) {
  const stream = useEventStream(userId);
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          vdagent <span className="muted small">analytics agents</span>
        </div>
        <UserPicker userId={userId} onSelect={onSelectUser} />
        {userId && <SidebarLists />}
        {userId && <StreamBadge state={stream} />}
      </aside>
      {userId ? (
        <Workspace />
      ) : (
        <main className="main">
          <div className="chat-empty muted">Pick or create a user to start.</div>
        </main>
      )}
    </div>
  );
}

function useActiveTaskId(): string | null {
  const { taskId } = useUi();
  const tasks = useTasks();
  return taskId ?? tasks.data?.[0]?.id ?? null;
}

function SidebarLists() {
  const agents = useAgents();
  const ui = useUi();
  const activeTaskId = useActiveTaskId();
  return (
    <>
      <AgentList agents={agents.data} selected={ui.agent ?? DEFAULT_AGENT} error={agents.error} />
      <TaskList activeTaskId={activeTaskId} />
    </>
  );
}

function Workspace() {
  const agents = useAgents();
  const ui = useUi();
  const activeTaskId = useActiveTaskId();
  const name = ui.agent ?? DEFAULT_AGENT;
  const agent = agents.data?.find((a) => a.name === name) ?? agents.data?.[0];
  return (
    <>
      <main className="main">
        {agent ? (
          <ChatPane key={agent.name} agent={agent} />
        ) : (
          <div className="chat-empty muted">{agents.isError ? agents.error.message : "Loading agents…"}</div>
        )}
      </main>
      <aside className="inspector">
        <Inspector taskId={activeTaskId} />
      </aside>
    </>
  );
}

function StreamBadge({ state }: { state: StreamState }) {
  const label = { connecting: "connecting…", open: "live", reconnecting: "reconnecting…" }[state];
  return (
    <div className={`stream-badge stream-${state}`} title="Server-sent events connection">
      <span className="stream-dot" /> {label}
    </div>
  );
}
