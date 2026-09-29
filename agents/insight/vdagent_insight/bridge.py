"""The sdk adapter of the Insight Agent: `InsightAgent.invoke(ctx)` → `run_task` → one reply.

Input (the last history message, `[from: <sender>] <text>`):

- a JSON `InsightTaskRequest`, bare or in a ```json block (docs/OPEN_QUESTIONS.md Q1). Missing
  `run_id`/`task_id`/`snapshot_id`/`semantic_config_version`/`input_artifact_refs`/`user_context`
  are filled from the invocation and the data pack; a request that still fails the schema → E01.
- **compat mode, temporary until D-10**: the current Orchestrator sends free text. The request is
  then built deterministically, without an LLM: unit codes by the config regex (D-73), tower and
  project names matched against the data pack catalogue, intent by keywords (config/bridge.yaml).
  No recognisable scope → the E01 guidance reply in Vietnamese.

Output (D-11, no backend change): a Vietnamese summary, the `artifact_id` and a compact JSON block of
the KEY insights (id, rendered_text, eligible_for_conclusion, limitations, recommendation), at most
`max_reply_chars` (6 000). The full artifact stays in the Insight store.

`compact()` is deterministic (D-18): questions and artifact ids, cut at `compact_max_chars`.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from vdagent_sdk import InvocationContext, Message

from .agent import TaskResult, run_task
from .contracts import AnalysisScope, InsightTaskRequest, Intent, Level, TaskCode
from .export_reader import Catalog
from .memory import CtxMemory
from .runtime import InsightRuntime
from .settings import CONFIG_DIR, ConfigError, SemanticConfig

NAME = "insight"
DESCRIPTION = (
    "Insight Agent v2: giải thích vì sao căn hộ / tòa / dự án bán chậm (nguyên nhân theo căn, phân bố nguyên nhân, "
    "pattern, bối cảnh thị trường, giới hạn dữ liệu) trên data pack snapshot, mọi con số có bằng chứng. "
    "Gửi JSON InsightTaskRequest, hoặc câu hỏi có mã căn (vd SAPPHIRE1-16.231), tên tòa (vd The Sapphire 1) "
    "hoặc tên dự án."
)
MAX_REPLY_CHARS = 6000
COMPAT_NOTE = (
    "_(chế độ tương thích: câu hỏi tự do được chuyển thành InsightTaskRequest bằng luật cố định, tạm thời tới khi chốt D-10)_"
)
ID_NAMESPACE = uuid.UUID("5f1d3c7e-9a51-4f7b-8f4e-2b6a1c0d9e11")
INBOUND = re.compile(r"^\[from: [^\]]*\]\s*")
FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)
ARTIFACT_ID = re.compile(r"ART-INSIGHT-[0-9a-f]{16}")

log = logging.getLogger("vdagent.plugin.vdagent_insight")


# ---- config/bridge.yaml ------------------------------------------------------------------------------


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    intent_keywords: dict[Intent, list[str]]
    default_intent: Intent
    tasks: dict[Intent, dict[Literal["UNIT", "ZONE", "PROJECT"], list[TaskCode]]]
    name_prefixes: list[str]
    compat_role: Literal["SALES_OPS", "SALES_MANAGER", "EVALUATOR"]
    max_reply_chars: int = Field(gt=0, le=MAX_REPLY_CHARS)
    compact_max_chars: int = Field(gt=0)


def load_bridge_config(path: Path = CONFIG_DIR / "bridge.yaml") -> BridgeConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return BridgeConfig.model_validate(data["insight_bridge_config"])
    except (OSError, KeyError, TypeError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"{path.name}: {exc}") from None


# ---- free text → request (compat mode) ---------------------------------------------------------------


def fold(text: str) -> str:
    """Lower case, no diacritics (đ → d), single spaces: the form keywords and names are matched in."""
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text.lower()).strip()


def _mentions(folded: str, name: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", folded) is not None


def _aliases(name: str, prefixes: list[str]) -> list[str]:
    out = [fold(name)]
    for part in [p.strip() for p in name.split(" - ")[1:]]:
        out.append(fold(part))
    for alias in list(out):
        for prefix in prefixes:
            if alias.startswith(prefix):
                out.append(alias[len(prefix) :])
    return sorted(set(a for a in out if a), key=len, reverse=True)


@dataclass(frozen=True)
class ParsedText:
    scope: AnalysisScope
    intent: Intent
    tasks: list[TaskCode]
    label: str


def parse_free_text(text: str, catalog: Catalog, cfg: SemanticConfig, bridge: BridgeConfig | None = None) -> ParsedText | None:
    """The request a free-text question asks for, or None when no unit / tower / project is named."""
    bridge = bridge or load_bridge_config()
    folded = fold(text)
    level: Level
    codes = re.findall(cfg.language.unit_code_pattern, text.upper())
    units = list(dict.fromkeys(catalog.unit_by_code[c] for c in codes if c in catalog.unit_by_code))
    zones = [z for z in catalog.zones if any(_mentions(folded, a) for a in _aliases(z.zone_name, bridge.name_prefixes))]
    projects = [p for p in catalog.projects if _mentions(folded, fold(p.project_name))]
    if units:
        level, scope = "UNIT", AnalysisScope(level="UNIT", unit_ids=[u.unit_id for u in units])
        label = ", ".join(u.unit_code for u in units)
    elif zones:
        level, scope = "ZONE", AnalysisScope(level="ZONE", zone_ids=[z.zone_id for z in zones])
        label = ", ".join(z.zone_name for z in zones)
    elif projects:
        # the longest name wins ("Vinhomes Ocean Park - The Beverly" also contains "Vinhomes Ocean Park")
        best = max(projects, key=lambda p: len(p.project_name))
        level, scope = "PROJECT", AnalysisScope(level="PROJECT", project_ids=[best.project_id])
        label = best.project_name
    else:
        return None
    intent: Intent = bridge.default_intent
    for candidate, words in bridge.intent_keywords.items():
        if any(_mentions(folded, w) for w in words):
            intent = candidate
            break
    return ParsedText(scope=scope, intent=intent, tasks=list(bridge.tasks[intent][level]), label=label)


# ---- replies -----------------------------------------------------------------------------------------


def guidance(catalog: Catalog | None, reason: str) -> str:
    zones = ", ".join(z.zone_name for z in catalog.zones) if catalog else ""
    projects = ", ".join(p.project_name for p in catalog.projects) if catalog else ""
    example = next(iter(catalog.unit_by_code), "SAPPHIRE1-16.231") if catalog else "SAPPHIRE1-16.231"
    lines = [
        f"Insight chưa nhận yêu cầu (E01 INPUT_SCHEMA_INVALID): {reason}",
        "",
        "Hãy nêu rõ phạm vi cần phân tích, ví dụ:",
        "- một căn theo mã: “Vì sao căn SAPPHIRE1-16.231 bán chậm?”"
        + (f" (mã khác trong dữ liệu, vd {example})" if example != "SAPPHIRE1-16.231" else ""),
        "- một tòa: “Vì sao tòa The Sapphire 1 có nhiều căn bán chậm?”",
        "- một dự án: “Thống kê DOM trung bình theo hướng ban công của dự án Vinhomes Ocean Park”",
    ]
    if zones:
        lines += [f"Tòa có trong dữ liệu: {zones}.", f"Dự án: {projects}."]
    lines += [
        "",
        "Hoặc gửi InsightTaskRequest trong khối ```json với tối thiểu các trường: intent, tasks, question_normalized, "
        "analysis_scope {level, project_ids | zone_ids | unit_ids}; các trường còn lại (run_id, task_id, snapshot_id, "
        "semantic_config_version, input_artifact_refs, user_context) được điền từ data pack.",
    ]
    return "\n".join(lines)


def _compact_insight(ins: Any) -> dict[str, Any]:
    return {
        "id": ins.insight_id,
        "rendered_text": ins.claim.rendered_text,
        "eligible_for_conclusion": ins.eligible_for_conclusion,
        "limitations": list(ins.limitations),
        "recommendation": ins.recommendation.text if ins.recommendation else None,
    }


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: max(n - 1, 0)] + "…"


def render_reply(result: TaskResult, request: InsightTaskRequest, *, compat: bool, max_chars: int = MAX_REPLY_CHARS) -> str:
    env = result.envelope
    if env is None:
        return f"Insight không chạy được phân tích ({result.error_code}): {result.error_message}."
    p = env.payload
    cov = p.summary.coverage
    key = [i for i in p.insights if i.materiality == "KEY"]
    shown = key or p.insights[:3]
    scope = request.analysis_scope
    ids = scope.unit_ids or scope.zone_ids or scope.project_ids
    if p.summary.narrative_mode == "LLM":
        mode = f"LLM ({env.producer.model_id})"
    elif env.producer.model_id:  # some items fell back to a template after validation (spec 8.2)
        mode = f"LLM ({env.producer.model_id}), một phần dùng mẫu cố định (TEMPLATE)"
    else:
        mode = "mẫu cố định (TEMPLATE)"
    cost = "không gọi LLM" if result.task_cost_usd is None else f"${result.task_cost_usd}"
    head = [
        f"**Insight {env.status}** · artifact `{env.artifact_id}`" + (" · kết quả đã có, dùng lại" if result.reused else ""),
        f"Phạm vi: {scope.level} {', '.join(ids)} · snapshot {request.snapshot_id} · {cov.units_in_scope} căn, "
        f"{cov.overdue_units} căn quá hạn, {cov.units_explained} căn có giải thích.",
        f"Diễn giải: {mode} · chi phí LLM: {cost} · {result.latency_ms} ms.",
    ]
    if compat:
        head.append(COMPAT_NOTE)
    limits = [lim.message for lim in [*env.limitations, *p.limitations]][:5]

    def build(k: int, text_max: int) -> str:
        items = shown[:k]
        lines = [*head, "", "Điểm chính:" if key else "Kết quả:"]
        for n, ins in enumerate(items, start=1):
            rec = f" → Đề xuất: {_cut(ins.recommendation.text, text_max)}" if ins.recommendation else ""
            lines.append(f"{n}. {_cut(ins.claim.rendered_text, text_max)}{rec}")
        if len(items) < len(shown):
            lines.append(f"… và {len(shown) - len(items)} ý khác trong artifact.")
        if limits:
            lines += ["", "Giới hạn dữ liệu: " + "; ".join(limits) + "."]
        body = {
            "artifact_id": env.artifact_id,
            "status": env.status,
            "snapshot_id": request.snapshot_id,
            "narrative_mode": p.summary.narrative_mode,
            "coverage": cov.model_dump(mode="json"),
            "insights": [
                {**c, "rendered_text": _cut(c["rendered_text"], text_max)}
                for c in (_compact_insight(i) for i in items if i in key)
            ],
            "truncated": len(items) < len(shown),
        }
        return "\n".join(lines) + "\n\n```json\n" + json.dumps(body, ensure_ascii=False) + "\n```"

    for text_max in (2000, 600, 200):
        for k in range(len(shown), 0, -1):
            reply = build(k, text_max)
            if len(reply) <= max_chars:
                return reply
    return build(0, 100)[:max_chars]


# ---- compact (D-18) ------------------------------------------------------------------------------------


def compact_summary(previous: str, messages: list[Message], max_chars: int = 2000) -> str:
    lines: list[str] = []
    for m in messages:
        content = m.get("content") or ""
        if m.get("role") == "user":
            lines.append("Hỏi Insight: " + _cut(INBOUND.sub("", content).strip().replace("\n", " "), 200))
        elif m.get("role") == "assistant":
            ids = list(dict.fromkeys(ARTIFACT_ID.findall(content)))
            if ids:
                lines.append("Insight trả artifact: " + ", ".join(ids))
    text = "\n".join(x for x in [previous.strip(), *lines] if x)
    if len(text) <= max_chars:
        return text
    kept: list[str] = []
    for line in reversed(text.splitlines()):
        if len("\n".join([line, *kept])) > max_chars:
            break
        kept.insert(0, line)
    return "\n".join(kept)


# ---- the agent ----------------------------------------------------------------------------------------


def _inbound(ctx: InvocationContext) -> str:
    last = ctx.history[-1] if ctx.history else {}
    return INBOUND.sub("", str(last.get("content") or "")).strip()


def _json_text(text: str) -> str | None:
    fenced = FENCE.search(text)
    if fenced:
        return fenced.group(1).strip()
    return text if text.startswith("{") else None


def _schema_errors(exc: ValidationError) -> str:
    parts = [".".join(str(x) for x in e["loc"]) or "(gốc)" for e in exc.errors()[:8]]
    return "các trường sai hoặc thiếu: " + ", ".join(parts)


class InputError(Exception):
    pass


class InsightAgent:
    def __init__(self, runtime: InsightRuntime, bridge: BridgeConfig | None = None) -> None:
        self._rt = runtime
        self._cfg = bridge or load_bridge_config()

    def _ids(self, ctx: InvocationContext) -> dict[str, str]:
        return {
            "run_id": str(uuid.uuid5(ID_NAMESPACE, f"run:{ctx.invocation_id}")),
            "task_id": str(uuid.uuid5(ID_NAMESPACE, f"insight:{ctx.invocation_id}")),
            "conversation_id": str(uuid.uuid5(ID_NAMESPACE, f"conversation:{ctx.task_id}")),  # D-41
        }

    async def _user(self, ctx: InvocationContext) -> dict[str, Any]:
        catalog = await self._rt.reader.catalog()
        return {
            "user_id": ctx.user_id,
            "role": self._cfg.compat_role,
            "authorized_scope": {"project_ids": [p.project_id for p in catalog.projects], "zone_ids": []},  # D-14
        }

    async def _complete(self, ctx: InvocationContext, data: dict[str, Any]) -> InsightTaskRequest:
        manifest = await self._rt.reader.manifest()
        data = {
            **self._ids(ctx), "attempt": 1, "fencing_token": 1, "question_normalized": "",
            "snapshot_id": manifest.snapshot_id, "semantic_config_version": manifest.semantic_version, **data,
        }  # fmt: skip
        if "user_context" not in data:
            data["user_context"] = await self._user(ctx)
        try:
            scope = AnalysisScope.model_validate(data.get("analysis_scope"))
        except ValidationError as exc:
            raise InputError(_schema_errors(exc)) from None
        arts = await self._rt.reader.prepare(scope)  # the refs of an orchestrator resolve against these
        if "input_artifact_refs" not in data:
            data["input_artifact_refs"] = [
                {"artifact_id": a.artifact_id, "artifact_type": a.artifact_type, "version": a.version, "status": a.status,
                 "content_hash": a.content_hash}
                for a in arts
            ]  # fmt: skip
        try:
            return InsightTaskRequest.model_validate(data)
        except ValidationError as exc:
            raise InputError(_schema_errors(exc)) from None

    async def answer(self, ctx: InvocationContext) -> str:
        text = _inbound(ctx)
        raw = _json_text(text)
        compat = raw is None
        try:
            if raw is not None:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    raise InputError("JSON không đọc được") from None
                if not isinstance(data, dict):
                    raise InputError("JSON phải là một object InsightTaskRequest")
                request = await self._complete(ctx, data)
            else:
                catalog = await self._rt.reader.catalog()
                parsed = parse_free_text(text, catalog, self._rt.reader.cfg, self._cfg) if text else None
                if parsed is None:
                    return guidance(catalog, "không nhận ra mã căn, tên tòa hay tên dự án trong câu hỏi.")
                request = await self._complete(
                    ctx,
                    {
                        "intent": parsed.intent, "tasks": parsed.tasks, "question_normalized": text[:1000],
                        "analysis_scope": parsed.scope.model_dump(mode="json"),
                    },
                )  # fmt: skip
        except InputError as exc:
            return guidance(await self._rt.reader.catalog(), str(exc))
        memory = CtxMemory(ctx.memory, self._rt.llm.memory, self._rt.clock)
        result = await run_task(request, self._rt.deps(memory))
        return render_reply(result, request, compat=compat, max_chars=self._cfg.max_reply_chars)

    async def invoke(self, ctx: InvocationContext) -> None:
        try:
            reply = await self.answer(ctx)
        except Exception as exc:  # the Orchestrator always gets an answer
            log.exception("insight: turn failed")
            reply = f"Insight gặp lỗi nội bộ ({type(exc).__name__}); chưa có artifact nào được ghi. Vui lòng thử lại."
        await ctx.emit_assistant(reply)

    async def compact(self, previous_summary: str, messages: list[Message]) -> str:
        return compact_summary(previous_summary, messages, self._cfg.compact_max_chars)
