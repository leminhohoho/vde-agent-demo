import { useState } from "react";
import type { MessageDTO, PendingDTO, ToolCallDTO } from "../../api/types";
import { ARTIFACT_SPLIT, agentColor } from "../../ui/artifacts";
import { formatTime, prettyJson, truncate } from "../../ui/format";
import { ArtifactLink, ArtifactText } from "../ArtifactLink";
import { MarkdownText } from "../Markdown";

const SEND_TO_AGENT = "send_to_agent";

export function SenderBadge({ name }: { name: string }) {
  return (
    <span className="sender-badge" style={{ background: agentColor(name) }}>
      {name}
    </span>
  );
}

function parseArgs(json: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(json);
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
  } catch {
    // fall through: show the raw text
  }
  return { arguments: json };
}

/** Distinct artifact ids mentioned in a text, in order of appearance. */
function artifactIds(text: string): string[] {
  const ids = text.split(ARTIFACT_SPLIT).filter((_, i) => i % 2 === 1);
  return [...new Set(ids)];
}

function ToolCallChip({ call, result }: { call: ToolCallDTO; result: MessageDTO | undefined }) {
  const [open, setOpen] = useState(false);
  const args = parseArgs(call.arguments_json);
  const isAgentCall = call.name === SEND_TO_AGENT;
  const failed = result?.content.startsWith("error:") ?? false;
  const produced = result && !failed ? artifactIds(result.content).slice(0, 3) : [];

  return (
    <div className={`tool-chip${open ? " open" : ""}`}>
      <div className="tool-chip-head">
        <button type="button" className="tool-chip-toggle" onClick={() => setOpen((v) => !v)}>
          <span className="caret">{open ? "▾" : "▸"}</span>
          <span className="mono tool-name">{call.name}</span>
          {isAgentCall ? (
            <span className="tool-target">
              → <SenderBadge name={String(args.agent ?? "?")} />
              <span className="muted"> {truncate(String(args.message ?? ""), 60)}</span>
            </span>
          ) : (
            <span className="mono muted">({Object.keys(args).join(", ")})</span>
          )}
        </button>
        <span className="tool-outcome">
          {!result && <span className="spinner small" title="waiting for result" />}
          {failed && <span className="outcome-error">→ error</span>}
          {result && !failed && produced.length === 0 && <span className="outcome-ok">✓</span>}
          {produced.map((id) => (
            <span key={id}>
              → <ArtifactLink id={id} />
            </span>
          ))}
        </span>
      </div>
      {open && (
        <div className="tool-args">
          {Object.entries(args).map(([key, value]) => (
            <div key={key} className="tool-arg">
              <div className="tool-arg-key">{key}</div>
              <pre className="tool-arg-value">
                <ArtifactText text={typeof value === "string" ? value : JSON.stringify(value, null, 2)} />
              </pre>
            </div>
          ))}
          <div className="muted small mono">call id {call.id}</div>
        </div>
      )}
    </div>
  );
}

function ToolResult({ message, call }: { message: MessageDTO; call: ToolCallDTO | undefined }) {
  const [open, setOpen] = useState(false);
  const isAgentReply = call?.name === SEND_TO_AGENT;
  const failed = message.content.startsWith("error:");
  const agent = isAgentReply ? String(parseArgs(call.arguments_json).agent ?? "agent") : null;

  return (
    <div className={`tool-result${failed ? " failed" : ""}`}>
      <div className="tool-result-head" onClick={() => setOpen((v) => !v)}>
        <button type="button" className="caret-btn" aria-expanded={open}>
          {open ? "▾" : "▸"}
        </button>
        <span className="tool-result-label">
          {agent ? (
            <>
              reply from <SenderBadge name={agent} />
            </>
          ) : (
            <>
              ↳ <span className="mono">{call?.name ?? "tool"}</span> result
            </>
          )}
        </span>
        {!open && (
          <span className="tool-result-preview">
            <ArtifactText text={truncate(message.content, 140)} />
          </span>
        )}
      </div>
      {open &&
        (isAgentReply && !failed ? (
          <MarkdownText text={message.content} className="tool-result-body" />
        ) : (
          <pre className="tool-result-body mono">
            <ArtifactText text={prettyJson(message.content)} />
          </pre>
        ))}
    </div>
  );
}

interface MessageItemProps {
  message: MessageDTO;
  agent: string;
  toolCalls: Map<string, ToolCallDTO>;
  toolResults: Map<string, MessageDTO>;
}

export function MessageItem({ message, agent, toolCalls, toolResults }: MessageItemProps) {
  const cls = `msg msg-${message.role}${message.compacted ? " compacted" : ""}`;
  const time = <span className="msg-time">{formatTime(message.created_at)}</span>;

  if (message.role === "tool") {
    const call = message.tool_call_id ? toolCalls.get(message.tool_call_id) : undefined;
    return (
      <div className={cls} data-seq={message.seq}>
        <ToolResult message={message} call={call} />
      </div>
    );
  }

  if (message.role === "user") {
    const sender = message.sender ?? "user";
    return (
      <div className={`${cls}${sender === "user" ? " from-human" : " from-agent"}`} data-seq={message.seq}>
        <div className="msg-head">
          <SenderBadge name={sender} />
          {time}
        </div>
        <MarkdownText text={message.content} />
      </div>
    );
  }

  const failed = message.content.startsWith("[turn failed:");
  return (
    <div className={`${cls}${failed ? " turn-failed" : ""}`} data-seq={message.seq}>
      <div className="msg-head">
        <span className="assistant-label" style={{ color: agentColor(agent) }}>
          {agent}
        </span>
        {time}
      </div>
      {message.content && <MarkdownText text={message.content} />}
      {message.tool_calls && message.tool_calls.length > 0 && (
        <div className="tool-chips">
          {message.tool_calls.map((c) => (
            <ToolCallChip key={c.id} call={c} result={toolResults.get(c.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Inbound message queued behind the agent's current turn (not yet on the stack). */
export function PendingItem({ pending }: { pending: PendingDTO }) {
  return (
    <div className="msg msg-pending">
      <div className="msg-head">
        <span className="pending-badge">pending</span>
        <span className="muted small">from</span>
        <SenderBadge name={pending.caller} />
        <span className="msg-time">{formatTime(pending.created_at)}</span>
      </div>
      <div className="pending-text">
        <ArtifactText text={truncate(pending.inbound_text, 400)} />
      </div>
    </div>
  );
}
