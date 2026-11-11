"""Local smoke test (not committed): chunked extraction on the demo transcript."""
import sys
from pathlib import Path

from app.agents.agent1_extract import extract_requirements_chunked, verify_extraction

project_id = int(sys.argv[1])
max_chars = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
text = (Path(__file__).resolve().parents[1] / "demo_data" / "project-a-transcript.txt").read_text(encoding="utf-8")
sources = [{"id": 1, "kind": "transcript", "title": "demo", "content_text": text}]
result = extract_requirements_chunked(project_id=project_id, sources=sources, max_chars=max_chars, overlap=200)
verified = verify_extraction(result.requirements, sources)
print("text chars:", len(text))
print("batches:", result.batches)
print("requirements after dedupe:", len(result.requirements))
print("quotes verified:", sum(v.quote_verified for v in verified), "of", len(verified))
print("total cost usd:", sum(r.cost_usd for r in result.runs))