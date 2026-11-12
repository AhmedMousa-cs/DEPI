"""Agent 1: extract DRAFT requirements from project sources. It never writes to the database."""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.ingest.chunking import chunk_text
from app.llm.runner import StructuredRun, run_structured
from app.schemas.extraction import ExtractedRequirement, ExtractionOutput
from app.services.injection_guard import detect_injection, wrap_untrusted
from app.services.quotes import VerifiedRequirement, normalize, verify_requirements

log = logging.getLogger("nexus.agent1")

PROMPT_VERSION = "agent1_extract_v2"
_PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"

# Characters of source text per LLM call, and how much neighbouring chunks overlap.
MAX_CHARS_PER_CALL = 900
CHUNK_OVERLAP = 120


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


# ---- long sources: batching, merge, de-duplication -------------------------------------------------

@dataclass(frozen=True)
class ChunkedExtraction:
    requirements: list[ExtractedRequirement]
    runs: list[StructuredRun]
    batches: int


def build_batches(
    sources: list[dict], *, max_chars: int = MAX_CHARS_PER_CALL, overlap: int = CHUNK_OVERLAP
) -> list[list[dict]]:
    """Split sources into chunks and pack them into batches of at most max_chars (one LLM call each).

    Each part keeps its source id/kind/title; "chunk" holds its offsets in the original text.
    """
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for source in sources:
        for chunk in chunk_text(source["content_text"] or "", max_chars, overlap):
            part = {
                **source,
                "content_text": chunk.text,
                "chunk": {"index": chunk.index, "start": chunk.start, "end": chunk.end},
            }
            if current and size + len(chunk.text) > max_chars:
                batches.append(current)
                current, size = [], 0
            current.append(part)
            size += len(chunk.text)
    if current:
        batches.append(current)
    return batches


def dedupe_requirements(items: list[ExtractedRequirement]) -> list[ExtractedRequirement]:
    """Same source + same quote (ignoring case/spacing) = same item; keep the highest confidence."""
    kept: dict[tuple[int, str], ExtractedRequirement] = {}
    for item in items:
        key = (item.source_id, normalize(item.exact_quote))
        current = kept.get(key)
        if current is None or item.confidence > current.confidence:
            kept[key] = item
    return list(kept.values())


def extract_requirements_chunked(
    *,
    project_id: int,
    sources: list[dict],
    job_id: int | None = None,
    max_chars: int = MAX_CHARS_PER_CALL,
    overlap: int = CHUNK_OVERLAP,
    on_batch: Callable[[int, int], None] | None = None,
) -> ChunkedExtraction:
    """Like extract_requirements, but safe for long sources. If one call fails the error propagates
    (calls that already finished stay recorded in agent_runs)."""
    flags = injection_flags(sources)  # on the FULL text, so a pattern split across chunks is still seen
    if flags:
        log.warning("instruction-like text in sources of project %s: %s", project_id, flags)
    batches = build_batches(sources, max_chars=max_chars, overlap=overlap)
    runs: list[StructuredRun] = []
    collected: list[ExtractedRequirement] = []
    for index, batch in enumerate(batches, start=1):
        run = run_structured(
            project_id=project_id,
            job_id=job_id,
            agent="agent1_extract",
            prompt_version=PROMPT_VERSION,
            messages=build_messages(batch),
            schema=ExtractionOutput,
        )
        runs.append(run)
        for item in run.parsed.requirements:
            if detect_injection(item.exact_quote) or detect_injection(item.text):
                log.warning("dropped instruction-like item from project %s: %r", project_id, item.text[:80])
                continue
            collected.append(item)
        if on_batch is not None:
            on_batch(index, len(batches))
    return ChunkedExtraction(dedupe_requirements(collected), runs, len(batches))


def verify_extraction(
    output: ExtractionOutput | list[ExtractedRequirement], sources: list[dict]
) -> list[VerifiedRequirement]:
    """Check each quote against its own FULL source (not a chunk). Unverified items get a capped confidence."""
    items = output.requirements if isinstance(output, ExtractionOutput) else output
    sources_by_id = {s["id"]: s["content_text"] for s in sources}
    return verify_requirements(items, sources_by_id)