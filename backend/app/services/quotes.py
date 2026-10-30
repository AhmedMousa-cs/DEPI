"""Deterministic quote verification (the blueprint's main anti-hallucination guard)."""
import re
import unicodedata

MIN_QUOTE_CHARS = 8
_WS = re.compile(r"\s+")
_TRANS = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u200e": "", "\u200f": ""})


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_TRANS)
    return _WS.sub(" ", t).strip().casefold()


def verify_quote(source_text: str, quote: str) -> bool:
    q = normalize(quote).strip(" \"'`\u2026")
    if len(q) < MIN_QUOTE_CHARS:
        return False
    return q in normalize(source_text)
