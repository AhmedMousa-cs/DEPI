"""Unit tests for quote verification: no database, no LLM."""
from app.agents.agent1_extract import verify_extraction
from app.schemas.extraction import ExtractedRequirement, ExtractionOutput
from app.services.quotes import (
    UNVERIFIED_CONFIDENCE_CAP,
    check_quote,
    normalize,
    verify_quote,
    verify_requirements,
)

SRC_EN = "Karim: The receptionist should be able to book appointments by phone."
SRC_AR = "الهدف إن المريض يحجز أونلاين."
QUOTE_EN = "The receptionist should be able to book appointments by phone."


def _req(source_id: int, quote: str, confidence: float) -> ExtractedRequirement:
    return ExtractedRequirement(
        type="functional",
        text="Receptionist books appointments",
        source_id=source_id,
        exact_quote=quote,
        confidence=confidence,
    )


def test_exact_quote_is_reported_as_exact():
    check = check_quote(SRC_EN, QUOTE_EN)
    assert (check.verified, check.match, check.reason) == (True, "exact", None)


def test_case_and_whitespace_difference_is_a_normalized_match():
    check = check_quote(SRC_EN, "the  RECEPTIONIST should be able to book appointments by phone.")
    assert (check.verified, check.match) == (True, "normalized")


def test_arabic_diacritics_and_tatweel_are_ignored():
    decorated = SRC_AR.replace("ه", "ه\u064e").replace("ل", "لـ")
    assert decorated != SRC_AR
    check = check_quote(SRC_AR, decorated)
    assert (check.verified, check.match) == (True, "normalized")


def test_normalize_keeps_arabic_letters():
    assert normalize("الهَدف") == normalize("الهدف")
    assert normalize("الهدف") != ""


def test_altered_quote_is_not_found():
    check = check_quote(SRC_EN, "The receptionist must be able to book appointments by phone.")
    assert (check.verified, check.match, check.reason) == (False, "none", "not_found")


def test_too_short_quote_is_rejected_with_reason():
    check = check_quote(SRC_EN, "the")
    assert (check.verified, check.reason) == (False, "too_short")


def test_verify_quote_still_returns_a_plain_bool():
    assert verify_quote(SRC_EN, QUOTE_EN) is True
    assert verify_quote(SRC_EN, "Patients can pay online") is False


def test_verified_item_keeps_its_confidence():
    [res] = verify_requirements([_req(1, QUOTE_EN, 0.9)], {1: SRC_EN})
    assert res.quote_verified is True
    assert res.confidence == 0.9


def test_fabricated_quote_is_flagged_and_confidence_is_capped():
    [res] = verify_requirements([_req(1, "Patients can pay online by card", 0.95)], {1: SRC_EN})
    assert res.quote_verified is False
    assert res.quote_reason == "not_found"
    assert res.confidence == UNVERIFIED_CONFIDENCE_CAP


def test_confidence_below_the_cap_is_never_raised():
    [res] = verify_requirements([_req(1, "Patients can pay online by card", 0.1)], {1: SRC_EN})
    assert res.quote_verified is False
    assert res.confidence == 0.1


def test_quote_from_an_unknown_source_is_unverified():
    [res] = verify_requirements([_req(99, QUOTE_EN, 0.9)], {1: SRC_EN})
    assert (res.quote_verified, res.quote_reason) == (False, "unknown_source")
    assert res.confidence == UNVERIFIED_CONFIDENCE_CAP


def test_order_is_preserved_and_inputs_are_not_modified():
    items = [_req(1, QUOTE_EN, 0.9), _req(1, "Patients can pay online by card", 0.95)]
    results = verify_requirements(items, {1: SRC_EN})
    assert [r.quote_verified for r in results] == [True, False]
    assert [r.requirement for r in results] == items
    assert items[1].confidence == 0.95


def test_verify_extraction_matches_each_quote_to_its_own_source():
    output = ExtractionOutput(requirements=[_req(1, QUOTE_EN, 0.9), _req(2, QUOTE_EN, 0.9)])
    sources = [
        {"id": 1, "kind": "transcript", "title": "a", "content_text": SRC_EN},
        {"id": 2, "kind": "note", "title": "b", "content_text": SRC_AR},
    ]
    results = verify_extraction(output, sources)
    assert [r.quote_verified for r in results] == [True, False]