"""Deterministic quote verification (the blueprint's main anti-hallucination guard)."""
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

from app.schemas.extraction import ExtractedRequirement

MIN_QUOTE_CHARS = 8
# An unverified quote can never keep a high confidence: it is capped, never raised.
UNVERIFIED_CONFIDENCE_CAP = 0.3

_WS = re.compile(r"\s+")
# Arabic diacritics (tashkeel), dagger alef and tatweel: ignored when matching.
_ARABIC_MARKS = re.compile("[\u064b-\u065f\u0670\u0640]")
_TRANS = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u200e": "", "\u200f": ""})


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_TRANS)
    t = _ARABIC_MARKS.sub("", t)
    return _WS.sub(" ", t).strip().casefold()


@dataclass(frozen=True)
class QuoteCheck:
    verified: bool
    match: str  # "exact" | "normalized" | "none"
    reason: str | None  # None | "too_short" | "not_found" | "unknown_source"


def check_quote(source_text: str, quote: str) -> QuoteCheck:
    q = normalize(quote or "").strip(" \"'`\u2026")
    if len(q) < MIN_QUOTE_CHARS:
        return QuoteCheck(False, "none", "too_short")
    source = source_text or ""
    raw = (quote or "").strip()
    if raw and raw in source:
        return QuoteCheck(True, "exact", None)
    if q in normalize(source):
        return QuoteCheck(True, "normalized", None)
    return QuoteCheck(False, "none", "not_found")


def verify_quote(source_text: str, quote: str) -> bool:
    return check_quote(source_text, quote).verified


def capped_confidence(confidence: float, verified: bool) -> float:
    return confidence if verified else min(confidence, UNVERIFIED_CONFIDENCE_CAP)


@dataclass(frozen=True)
class VerifiedRequirement:
    requirement: ExtractedRequirement
    quote_verified: bool
    quote_match: str
    quote_reason: str | None
    confidence: float  # after the unverified cap


def verify_requirements(
    items: Iterable[ExtractedRequirement], sources_by_id: Mapping[int, str]
) -> list[VerifiedRequirement]:
    """Check every item's quote against ITS OWN source. Order is preserved; inputs are not modified."""
    results: list[VerifiedRequirement] = []
    for item in items:
        source_text = sources_by_id.get(item.source_id)
        if source_text is None:
            check = QuoteCheck(False, "none", "unknown_source")
        else:
            check = check_quote(source_text, item.exact_quote)
        results.append(
            VerifiedRequirement(
                requirement=item,
                quote_verified=check.verified,
                quote_match=check.match,
                quote_reason=check.reason,
                confidence=capped_confidence(item.confidence, check.verified),
            )
        )
    return results