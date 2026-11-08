"""Unit tests for text chunking: no database, no LLM."""
import pytest

from app.ingest.chunking import Chunk, chunk_text

PARAGRAPHS = "\n\n".join(f"Paragraph {i}: the clinic needs feature number {i} to work well." for i in range(120))
ARABIC = " ".join("الهدف إن المريض يحجز أونلاين من البيت." for _ in range(150))


def test_empty_or_blank_text_gives_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\t ") == []


def test_short_text_is_one_chunk():
    text = "The clinic needs online booking."
    assert chunk_text(text, max_chars=100, overlap=10) == [Chunk(0, 0, len(text), text)]


def test_invalid_parameters_raise():
    with pytest.raises(ValueError):
        chunk_text("abc", max_chars=0, overlap=0)
    with pytest.raises(ValueError):
        chunk_text("abc", max_chars=100, overlap=-1)
    with pytest.raises(ValueError):
        chunk_text("abc", max_chars=100, overlap=51)


def test_offsets_match_the_original_text():
    for text in (PARAGRAPHS, ARABIC):
        chunks = chunk_text(text, max_chars=500, overlap=80)
        assert len(chunks) > 1
        assert all(text[c.start:c.end] == c.text for c in chunks)


def test_indexes_are_sequential():
    chunks = chunk_text(PARAGRAPHS, max_chars=500, overlap=80)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_cover_the_text_with_overlap_and_make_progress():
    chunks = chunk_text(PARAGRAPHS, max_chars=500, overlap=80)
    assert chunks[0].start == 0
    assert chunks[-1].end == len(PARAGRAPHS)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start > prev.start  # always moves forward
        assert nxt.start < prev.end    # overlaps, so no gap and no cut sentence is lost


def test_no_chunk_exceeds_max_chars():
    assert all(len(c.text) <= 500 for c in chunk_text(PARAGRAPHS, max_chars=500, overlap=80))


def test_prefers_paragraph_break():
    text = "A" * 50 + "\n\n" + "B" * 50
    first = chunk_text(text, max_chars=80, overlap=10)[0]
    assert first.text == "A" * 50 + "\n\n"


def test_arabic_text_chunks_start_on_word_boundaries():
    chunks = chunk_text(ARABIC, max_chars=300, overlap=60)
    assert len(chunks) > 1
    assert all(ARABIC[c.start - 1].isspace() for c in chunks[1:])


def test_text_without_any_whitespace_still_terminates():
    text = "x" * 1000
    chunks = chunk_text(text, max_chars=300, overlap=50)
    assert chunks[0].start == 0 and chunks[-1].end == 1000
    assert all(text[c.start:c.end] == c.text for c in chunks)
    assert all(len(c.text) <= 300 for c in chunks)