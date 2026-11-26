"""Agent 4 middleware (LangChain v1 AgentMiddleware).

* ToolAuditMiddleware - records every tool call (name, args, output) on the run context and writes
  an audit row for it. Also the data source for the ToolCorrectness evaluation.
* BudgetMiddleware    - stops the loop when the run's tokens pass the budget.
SummarizationMiddleware / ModelCallLimitMiddleware / ToolCallLimitMiddleware are the stock ones.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage

from app.tools.plan_tools import Agent4Context, ToolCallRecord

log = logging.getLogger("nexus.agent4")
AuditSink = Callable[[Agent4Context, ToolCallRecord], None]


class AgentBudgetStop(RuntimeError):
    """The run used its token budget; the pipeline keeps whatever was saved and falls back to rules."""


class FinishAfterReviewMiddleware(AgentMiddleware):
    """End once the bundled flow has collected a valid review from the model."""

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        if runtime.context.pending_review is not None:
            return {"jump_to": "end"}
        return None


def total_tokens(messages) -> tuple[int, int]:
    tin = tout = 0
    for m in messages:
        if isinstance(m, AIMessage) and m.usage_metadata:
            tin += int(m.usage_metadata.get("input_tokens", 0) or 0)
            tout += int(m.usage_metadata.get("output_tokens", 0) or 0)
    return tin, tout


class ToolAuditMiddleware(AgentMiddleware):
    def __init__(self, sink: AuditSink | None = None):
        super().__init__()
        self._sink = sink

    def wrap_tool_call(self, request, handler):
        ctx: Agent4Context = request.runtime.context
        name, args = request.tool_call["name"], dict(request.tool_call.get("args") or {})
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001
            rec = ToolCallRecord(name, args, "", error=f"{type(exc).__name__}: {exc}"[:300])
            self._record(ctx, rec)
            raise
        content = getattr(result, "content", result)
        rec = ToolCallRecord(name, args, content if isinstance(content, str) else json.dumps(content, default=str))
        self._record(ctx, rec)
        return result

    def _record(self, ctx: Agent4Context, rec: ToolCallRecord) -> None:
        ctx.calls.append(rec)
        if self._sink is not None:
            try:
                self._sink(ctx, rec)
            except Exception:  # noqa: BLE001 - auditing must never break the run
                log.warning("tool audit failed", exc_info=True)


class BudgetMiddleware(AgentMiddleware):
    """Counts the run's tokens on the context (so summarisation can't hide them) and stops at the budget."""

    def __init__(self, max_total_tokens: int):
        super().__init__()
        self.max_total_tokens = max_total_tokens

    def before_model(self, state, runtime):
        ctx: Agent4Context = runtime.context
        if ctx.tokens_in + ctx.tokens_out >= self.max_total_tokens:
            raise AgentBudgetStop(
                f"token budget reached ({ctx.tokens_in + ctx.tokens_out} >= {self.max_total_tokens})"
            )
        return None

    def after_model(self, state, runtime):
        ctx: Agent4Context = runtime.context
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.usage_metadata:
            ctx.tokens_in += int(last.usage_metadata.get("input_tokens", 0) or 0)
            ctx.tokens_out += int(last.usage_metadata.get("output_tokens", 0) or 0)
        return None
