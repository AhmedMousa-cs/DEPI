"""Agent 1: extract DRAFT requirements from project sources. It never writes to the database."""
import logging
from pathlib import Path

from app.llm.runner import StructuredRun, run_structured
from app.schemas.extraction import ExtractionOutput
from app.services.injection_guard import detect_injection, wrap_untrusted
from app.services.quotes import VerifiedRequirement, verify_requirements
log = logging.getLogger("nexus.agent1")

PROMPT_VERSION = "agent1_extract_v1"
_PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"


def injection_flags(sources: list[dict]) -> dict[int, list[str]]:
    """source id -> names of instruction-like patterns found in its text (empty dict if clean)."""
    found = {s["id"]: detect_injection(s["content_text"]) for s in sources}
    return {sid: flags for sid, flags in found.items() if flags}


def build_messages(sources: list[dict]) -> list[dict]:
    """sources: dicts with id, kind, title, content_text."""
    blocks = [
        wrap_untrusted(source_id=s["id"], kind=s["kind"], text=s["content_text"])
        for s in sources
    ]
    return [
        {"role": "system", "content": _PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def extract_requirements(*, project_id: int, sources: list[dict], job_id: int | None = None) -> StructuredRun:
    flags = injection_flags(sources)
    if flags:
        # Flagged text is still extracted as data; the PM sees it at review time.
        log.warning("instruction-like text in sources of project %s: %s", project_id, flags)
    return run_structured(
        project_id=project_id,
        job_id=job_id,
        agent="agent1_extract",
        prompt_version=PROMPT_VERSION,
        messages=build_messages(sources),
        schema=ExtractionOutput,
    )


def verify_extraction(output: ExtractionOutput, sources: list[dict]) -> list[VerifiedRequirement]:
    """Check each quote against its own source; unverified items get a capped confidence. Pure, no DB."""
    sources_by_id = {s["id"]: s["content_text"] for s in sources}
    return verify_requirements(output.requirements, sources_by_id)