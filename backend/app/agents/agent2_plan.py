"""Agent 2: turn APPROVED requirements into draft user stories and tasks.

The agent never touches the database. It only returns a typed PlanningOutput; the job handler is
the single writer (through save_draft_plan). Only approved requirements are passed in.

The professional edition (v2) adds three things:
- project context: the brief, the transcript and the PM's scope-review comments, so stories use the
  client's own wording and respect decisions already made;
- a self-correction loop: deterministic checks (plan_checks.py) run on the agent's own output and
  any HARD issue is fed back to the model before the plan is handed over;
- richer output: execution order, critical path and a rationale per task.
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.plan_checks import plan_issues
from app.llm.structured import structured_call
from app.llm.runner import StructuredRun, run_structured
from app.llm.usage import unwrap_parsed
from app.schemas.planning import PlanningOutput
from app.services.injection_guard import detect_injection, wrap_untrusted

log = logging.getLogger("nexus.agent2")

PROMPT_VERSION = "agent2_plan_v2"
_PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"

# Self-correction: passes x attempts-per-pass is the worst-case number of model calls, and that
# product is also what the budget layer reserves for (see run_structured).
MAX_PASSES = 3
ATTEMPTS_PER_PASS = 2

# Context caps, so one planning job can never blow the per-job token budget (2 chars/token, worst case).
MAX_BRIEF_CHARS = 2500
MAX_TRANSCRIPT_CHARS = 3000
MAX_DECISIONS_CHARS = 1000


class PlanQualityError(ValueError):
    """Agent 2 could not produce a plan that passes its deterministic checks."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("plan failed the deterministic quality checks: " + "; ".join(issues))


@dataclass(frozen=True)
class PlanningContext:
    """Background the PM already knows. All of it is untrusted data for the agent."""

    brief: str | None = None
    transcript: str | None = None
    pm_decisions: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class PlanRun:
    """The agent run plus the polish issues that survived self-correction.

    HARD issues never appear here: they raise PlanQualityError and fail the run, so a plan that
    cannot be executed is never handed to the database or the PM.
    """

    parsed: PlanningOutput
    run_id: int | None
    soft_issues: list[str] = field(default_factory=list)


def injection_flags(requirements: list[dict]) -> dict[int, list[str]]:
    """requirement id -> instruction-like patterns found in its approved text."""
    found = {int(r["id"]): detect_injection(r["text"]) for r in requirements}
    return {rid: flags for rid, flags in found.items() if flags}


def context_flags(context: PlanningContext | None) -> dict[str, list[str]]:
    """instruction-like patterns found in the optional project context."""
    if context is None:
        return {}
    texts = {
        "brief": context.brief or "",
        "transcript": context.transcript or "",
        "pm_decisions": "\n".join(context.pm_decisions),
    }
    return {name: flags for name, flags in ((k, detect_injection(v)) for k, v in texts.items() if v) if flags}


def context_blocks(context: PlanningContext | None) -> list[str]:
    """The optional background as wrapped, size-capped untrusted blocks."""
    if context is None:
        return []
    parts: list[str] = []
    if context.brief:
        parts.append(wrap_untrusted(source_id=0, kind="project_brief", text=context.brief[:MAX_BRIEF_CHARS]))
    if context.transcript:
        parts.append(
            wrap_untrusted(source_id=0, kind="meeting_transcript", text=context.transcript[:MAX_TRANSCRIPT_CHARS])
        )
    if context.pm_decisions:
        joined = "\n".join(f"- {d}" for d in context.pm_decisions)[:MAX_DECISIONS_CHARS]
        parts.append(wrap_untrusted(source_id=0, kind="pm_scope_review_comments", text=joined))
    return parts


def build_messages(requirements: list[dict], context: PlanningContext | None = None) -> list[dict]:
    blocks = [
        wrap_untrusted(
            source_id=int(r["id"]),
            kind=f"approved_requirement:{r.get('type', 'unknown')}",
            text=json.dumps(
                {"id": r["id"], "type": r["type"], "priority": r.get("priority"), "text": r["text"]},
                ensure_ascii=False,
            ),
        )
        for r in requirements
    ]
    context_part = context_blocks(context)
    intro = (
        "Approved requirements and project context (untrusted data). Create stories and tasks for "
        "them using the rules in the system prompt. The context explains the client's situation and "
        "the decisions the PM already made; the requirements are the only work you may plan. Return "
        "only the JSON object."
    )
    user = "\n\n".join([*context_part, *blocks])
    return [
        {"role": "system", "content": _PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": intro + "\n\n" + user},
    ]


def _feedback(hard: list[str], soft: list[str]) -> str:
    lines = [f"- {i}" for i in (*hard, *soft)]
    return (
        "Your plan failed these deterministic checks:\n"
        + "\n".join(lines)
        + "\nThe first issues are blocking and must be fixed. Fix all of them and return the "
        "complete corrected JSON object in the same schema, nothing else."
    )


def self_correcting_call(requirements: list[dict], *, passes: int = MAX_PASSES):
    """A run_structured `call` that checks the agent's own plan and feeds failures back to the model.

    The state dict holds the issues of the last pass, so the caller can record them in the audit.
    """
    state: dict[str, list[str]] = {"hard": [], "soft": []}

    def call(*, model, messages, schema, max_attempts, max_tokens):
        per_pass = max(1, max_attempts // passes)
        current = list(messages)
        for _ in range(passes):
            result = structured_call(
                model=model, messages=current, schema=schema, max_attempts=per_pass, max_tokens=max_tokens
            )
            parsed = unwrap_parsed(result, schema)
            state["hard"], state["soft"] = plan_issues(parsed, requirements)
            if not state["hard"]:
                break
            log.warning("Agent 2 plan needs another pass: %s", state["hard"])
            current = [
                *current,
                {"role": "assistant", "content": result.raw_text},
                {"role": "user", "content": _feedback(state["hard"], state["soft"])},
            ]
        if state["hard"]:
            raise PlanQualityError(state["hard"])
        return result

    call.state = state  # type: ignore[attr-defined]
    return call


def generate_plan(
    *, project_id: int, requirements: list[dict], context: PlanningContext | None = None, job_id: int | None = None
) -> PlanRun:
    if not requirements:
        raise ValueError("Agent 2 needs at least one approved requirement")
    flags = injection_flags(requirements)
    ctx_flags = context_flags(context)
    if flags or ctx_flags:
        # The text is still used as data; the PM sees the warning in the audit trail.
        log.warning(
            "instruction-like text in Agent-2 input of project %s: requirements=%s context=%s",
            project_id, flags, ctx_flags,
        )
    messages = build_messages(requirements, context)
    loop = self_correcting_call(requirements)
    run: StructuredRun = run_structured(
        project_id=project_id,
        job_id=job_id,
        agent="agent2_plan",
        prompt_version=PROMPT_VERSION,
        messages=messages,
        schema=PlanningOutput,
        max_attempts=MAX_PASSES * ATTEMPTS_PER_PASS,
        call=loop,
    )
    return PlanRun(
        parsed=run.parsed,
        run_id=run.run_id,
        soft_issues=list(loop.state["soft"]),  # type: ignore[attr-defined]
    )
