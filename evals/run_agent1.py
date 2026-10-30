"""Manual evaluation: python -m evals.run_agent1 --golden project-a   (real LLM calls, real cost)."""
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

from evals.metrics import evaluate  # noqa: E402

GOLDEN = ROOT / "evals" / "golden"
RESULTS = ROOT / "evals" / "results"


def load_golden(name: str):
    expected = json.loads((GOLDEN / f"{name}-expected.json").read_text(encoding="utf-8"))
    source_list = [
        {"source_id": s["source_id"], "kind": s["kind"], "text": (GOLDEN / s["file"]).read_text(encoding="utf-8")}
        for s in expected["sources"]
    ]
    return expected, {s["source_id"]: s["text"] for s in source_list}, source_list


def oracle_items(expected: dict) -> list[dict]:
    """The reference answer itself. Pipeline self-check only; says nothing about model quality."""
    return [
        {"type": e["type"], "text": e["text"], "source_id": e["source_id"], "exact_quote": e["exact_quote"],
         "confidence": round(sum(e["confidence_range"]) / 2, 3)}
        for e in expected["requirements"]
    ]


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or None
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

    expected, sources, source_list = load_golden(args.golden)
    run_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc)
    meta: dict = {"prompt_version": "oracle", "mode": "oracle"}
    error = None
    items: list = []
    if args.mode == "oracle":
        items = oracle_items(expected)
    else:
        try:
            from evals.baseline_extractor import extract

            items, meta = extract(source_list, args.max_attempts)
            meta["mode"] = "llm"
        except Exception as exc:  # noqa: BLE001
            from app.observability.redact import redact

            error = redact(f"{type(exc).__name__}: {exc}")[:500]
            meta = {"prompt_version": "agent1_extract_v0", "mode": "llm"}

    metrics = evaluate(expected, sources, items)
    if error:
        metrics["errors"].append(f"extraction failed: {error}")
        metrics["schema_valid"] = False
    record = {
        "run_id": run_id, "golden": args.golden, "started_at": started.isoformat(), "git_commit": _git_commit(),
        "model": settings.model_llm if args.mode == "llm" else None,
        "judge_model": settings.model_judge_eval, "judge_used": False,
        "prompt_version": meta.get("prompt_version"), "mode": meta.get("mode"),
        "recall": metrics["recall"], "precision": metrics["precision"],
        "quote_faithfulness": metrics["quote_faithfulness"], "source_verification_rate": metrics["source_verification_rate"],
        "injection_resistant": metrics["injection_resistant"], "duplicate_detection": metrics["duplicate_detection"],
        "schema_valid": metrics["schema_valid"], "errors": metrics["errors"],
        "extra": {k: metrics[k] for k in ("n_items", "confidence_in_range_rate", "needs_review_flagged", "details", "passed")},
        "usage": {k: meta.get(k) for k in ("attempts", "input_tokens", "output_tokens", "cost_usd", "pricing_configured", "injection_flags")},
    }
    print(json.dumps(record, indent=2, ensure_ascii=False))
    if args.mode == "oracle":
        print("\nSELF-CHECK ONLY (oracle mode): not saved, not a quality measurement.")
    else:
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"agent1-{args.golden}-{started.strftime('%Y%m%dT%H%M%SZ')}-{run_id[:8]}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nsaved: {out.relative_to(ROOT)}")
    return 1 if (args.strict and not all(metrics["passed"].values())) else 0


if __name__ == "__main__":
    raise SystemExit(main())
