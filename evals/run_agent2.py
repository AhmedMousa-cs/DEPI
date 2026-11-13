"""Manual evaluation: python -m evals.run_agent2 --golden project-b   (real LLM calls, real cost).

Agent 2 normally runs inside the job queue (app.services.agent2_job) where the runner persists the
run and the handler writes drafts. Here the same prompt/messages are used but the DB is skipped, so
the evaluator can score a plan without touching the application database.
"""
import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from evals.planning_metrics import evaluate_plan, oracle_plan  # noqa: E402

GOLDEN = ROOT / "evals" / "golden"
RESULTS = ROOT / "evals" / "results"


def load_golden(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}-expected.json").read_text(encoding="utf-8"))


def approved_requirements(expected: dict) -> list[dict]:
    """The requirement shape Agent 2 receives: approved rows only, id/type/priority/text."""
    return [
        {"id": r["requirement_id"], "type": r["type"], "priority": r.get("priority"), "text": r["text"]}
        for r in expected["requirements"]
    ]


def generate_plan(expected: dict, max_attempts: int | None = None) -> tuple[dict, dict]:
    """Real LLM call with the production Agent-2 prompt, without the runner's DB persistence."""
    from app.agents.agent2_plan import PROMPT_VERSION, build_messages
    from app.config import settings
    from app.llm.factory import get_llm_model
    from app.llm.pricing import compute_cost_usd
    from app.llm.structured import structured_call
    from app.llm.usage import TokenUsage, UsageUnavailable, extract_usage, unwrap_parsed
    from app.schemas.planning import PlanningOutput

    result = structured_call(
        model=get_llm_model(),
        messages=build_messages(approved_requirements(expected)),
        schema=PlanningOutput,
        max_attempts=max_attempts or settings.llm_max_attempts,
        max_tokens=settings.llm_max_output_tokens,
    )
    parsed = unwrap_parsed(result, PlanningOutput)
    try:
        usage = extract_usage(result)
    except UsageUnavailable:
        usage = TokenUsage(0, 0)
    cost, priced = compute_cost_usd(settings.model_llm, usage.input_tokens, usage.output_tokens)
    meta = {
        "prompt_version": PROMPT_VERSION,
        "attempts": getattr(result, "attempts", None),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd": str(cost),
        "pricing_configured": priced,
    }
    return parsed.model_dump(), meta


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
        ).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--mode", choices=["llm", "oracle"], default="llm")
    ap.add_argument("--max-attempts", type=int, default=None)
    ap.add_argument("--strict", action="store_true", help="exit 1 if any threshold fails")
    args = ap.parse_args()

    from app.config import settings

    expected = load_golden(args.golden)
    run_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc)
    meta: dict = {"prompt_version": "oracle", "mode": "oracle"}
    error = None
    plan: dict = {}
    if args.mode == "oracle":
        plan = oracle_plan(expected)
    else:
        try:
            plan, meta = generate_plan(expected, args.max_attempts)
            meta["mode"] = "llm"
        except Exception as exc:  # noqa: BLE001
            from app.observability.redact import redact

            error = redact(f"{type(exc).__name__}: {exc}")[:500]
            meta = {"prompt_version": "agent2_plan_v1", "mode": "llm"}

    metrics = evaluate_plan(expected, plan)
    if error:
        metrics["errors"].append(f"planning failed: {error}")
        metrics["passed"] = {**metrics.get("passed", {}), "schema_valid": False}
    record = {
        "run_id": run_id,
        "golden": args.golden,
        "started_at": started.isoformat(),
        "git_commit": _git_commit(),
        "model": settings.model_llm if args.mode == "llm" else None,
        "prompt_version": meta.get("prompt_version"),
        "mode": meta.get("mode"),
        "story_coverage": metrics["story_coverage"],
        "story_recall": metrics["story_recall"],
        "task_recall": metrics["task_recall"],
        "link_integrity": metrics["link_integrity"],
        "cycle_free": metrics["cycle_free"],
        "estimate_sanity": metrics["estimate_sanity"],
        "acceptance_criteria": metrics["acceptance_criteria"],
        "task_granularity": metrics["task_granularity"],
        "total_hours": metrics["total_hours"],
        "total_hours_ratio": metrics["total_hours_ratio"],
        "schema_valid": metrics["schema_valid"],
        "errors": metrics["errors"],
        "details": metrics["details"],
        "passed": metrics["passed"],
        "usage": {
            k: meta.get(k)
            for k in ("attempts", "input_tokens", "output_tokens", "cost_usd", "pricing_configured")
        },
    }
    print(json.dumps(record, indent=2, ensure_ascii=False))
    if args.mode == "oracle":
        print("\nSELF-CHECK ONLY (oracle mode): not saved, not a quality measurement.")
    else:
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"agent2-{args.golden}-{started.strftime('%Y%m%dT%H%M%SZ')}-{run_id[:8]}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nsaved: {out.relative_to(ROOT)}")
    return 1 if (args.strict and not all(metrics["passed"].values())) else 0


if __name__ == "__main__":
    raise SystemExit(main())
