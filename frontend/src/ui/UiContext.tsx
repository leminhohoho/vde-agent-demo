import { createContext, useContext, useMemo, useReducer, type ReactNode } from "react";

export type InspectorTab = "task" | "artifact";

export interface UiState {
  /** Agent whose chat is shown; null → default (orchestrator). */
  agent: string | null;
  /** Task shown in the Inspector; null → most recent task. */
  taskId: string | null;
  tab: InspectorTab;
  artifactId: string | null;
  /** Recently opened artifacts, most recent first. */
  recentArtifacts: string[];
}

export interface Ui extends UiState {
  selectAgent: (agent: string) => void;
  selectTask: (taskId: string) => void;
  openArtifact: (artifactId: string) => void;
  setTab: (tab: InspectorTab) => void;
}

type Action =
  | { type: "agent"; agent: string }
  | { type: "task"; taskId: string }
  | { type: "artifact"; artifactId: string }
  | { type: "tab"; tab: InspectorTab };

const RECENT_LIMIT = 8;

function reducer(state: UiState, action: Action): UiState {
  switch (action.type) {
    case "agent":
      return { ...state, agent: action.agent };
    case "task":
      return { ...state, taskId: action.taskId, tab: "task" };
    case "artifact":
      return {
        ...state,
        artifactId: action.artifactId,
        tab: "artifact",
        recentArtifacts: [
          action.artifactId,
          ...state.recentArtifacts.filter((id) => id !== action.artifactId),
        ].slice(0, RECENT_LIMIT),
      };
    case "tab":
      return { ...state, tab: action.tab };
  }
}

const initialState: UiState = {
  agent: null,
  taskId: null,
  tab: "task",
  artifactId: null,
  recentArtifacts: [],
};

const UiContext = createContext<Ui | null>(null);

export function UiProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const value = useMemo<Ui>(
    () => ({
      ...state,
      selectAgent: (agent) => dispatch({ type: "agent", agent }),
      selectTask: (taskId) => dispatch({ type: "task", taskId }),
      openArtifact: (artifactId) => dispatch({ type: "artifact", artifactId }),
      setTab: (tab) => dispatch({ type: "tab", tab }),
    }),
    [state],
  );
  return <UiContext.Provider value={value}>{children}</UiContext.Provider>;
}

export function useUi(): Ui {
  const ui = useContext(UiContext);
  if (!ui) throw new Error("useUi must be used inside <UiProvider>");
  return ui;
}
