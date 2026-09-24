import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { queryKeys } from "../../api/keys";
import { useApi } from "../../api/queries";
import type { AgentDTO } from "../../api/types";
import { useUi } from "../../ui/UiContext";

/** Posts a human message into the agent's chat (creates a task); disabled while unhealthy. */
export function Composer({ agent }: { agent: AgentDTO }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const { selectTask } = useUi();
  const [text, setText] = useState("");

  const post = useMutation({
    mutationFn: (content: string) => api.postMessage(agent.name, content),
    onSuccess: (res) => {
      setText("");
      selectTask(res.task_id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
    },
  });

  const disabled = !agent.healthy;
  const content = text.trim();
  const canSend = !disabled && content.length > 0 && !post.isPending;

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (canSend) post.mutate(content);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form className={`composer${disabled ? " disabled" : ""}`} onSubmit={submit}>
      <textarea
        rows={2}
        value={text}
        disabled={disabled}
        placeholder={
          disabled
            ? `${agent.name} is unavailable`
            : `Message ${agent.name}…  (Enter to send, Shift+Enter for a new line)`
        }
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <button type="submit" className="btn send-btn" disabled={!canSend} title="Send">
        {post.isPending ? <span className="spinner small" /> : "➤"}
      </button>
      {post.error && <div className="composer-error error-text">{post.error.message}</div>}
    </form>
  );
}
