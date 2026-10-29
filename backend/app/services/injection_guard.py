"""Untrusted content handling: wrap sources as data and flag instruction-like text."""
import re

_PATTERNS = {
    "ignore_instructions": r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above)\s+(instructions|prompts?)",
    "disregard_rules": r"disregard\s+.{0,30}(instructions|rules)",
    "auto_approve": r"(approve|accept)\s+(this|all|every)\s+requirements?",
    "force_confidence": r"mark\s+.{0,40}(confidence|verified)",
    "role_override": r"you\s+are\s+now\b|system\s+prompt",
    "ar_ignore": r"تجاهل\s+(كل\s+)?(التعليمات|الأوامر)",
    "ar_auto_approve": r"وافق\s+(تلقائي|تلقائيا|تلقائيًا)",
}
_COMPILED = {k: re.compile(v, re.IGNORECASE | re.DOTALL) for k, v in _PATTERNS.items()}


def detect_injection(text: str) -> list[str]:
    return sorted(name for name, rx in _COMPILED.items() if rx.search(text or ""))


def wrap_untrusted(*, source_id: int, kind: str, text: str) -> str:
    safe = (text or "").replace("<<<", "< < <").replace(">>>", "> > >")
    return f"<<<UNTRUSTED_SOURCE id={source_id} kind={kind}>>>\n{safe}\n<<<END_UNTRUSTED_SOURCE id={source_id}>>>"
