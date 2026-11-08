"""Split long text into overlapping chunks that keep exact offsets into the original text."""
from dataclasses import dataclass

# Preferred cut points, strongest first: paragraph, line, sentence (English/Arabic), word.
_BREAKS = ("\n\n", "\n", ". ", "؟ ", "! ", "? ", "؛ ", " ")


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int  # offset in the original text
    end: int    # exclusive
    text: str   # always equals original[start:end]


def _find_break(text: str, start: int, hard_end: int) -> int:
    """Best cut in the second half of (start, hard_end]; falls back to the hard limit."""
    floor = start + (hard_end - start) // 2
    window = text[floor:hard_end]
    for sep in _BREAKS:
        i = window.rfind(sep)
        if i != -1:
            return floor + i + len(sep)
    return hard_end


def chunk_text(text: str, max_chars: int = 6000, overlap: int = 400) -> list[Chunk]:
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    if overlap < 0 or overlap > max_chars // 2:
        raise ValueError("overlap must be between 0 and max_chars // 2")
    if not text or not text.strip():
        return []
    n = len(text)
    if n <= max_chars:
        return [Chunk(0, 0, n, text)]

    chunks: list[Chunk] = []
    pos = 0
    while pos < n:
        hard_end = min(pos + max_chars, n)
        end = n if hard_end == n else _find_break(text, pos, hard_end)
        chunks.append(Chunk(len(chunks), pos, end, text[pos:end]))
        if end >= n:
            break
        nxt = max(end - overlap, pos + 1)
        # Start the next chunk on a word boundary inside the overlap, never mid-word.
        if not text[nxt - 1].isspace():
            for k, ch in enumerate(text[nxt:end]):
                if ch.isspace():
                    nxt += k + 1
                    break
        pos = nxt
    return chunks