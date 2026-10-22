"""Normalization and structure-aware chunking with exact page and character spans.

The normalized text is the document's canonical text: chunk spans, citations, and eval gold
labels all point into it, so labels survive re-chunking (architecture §10.3).
"""

import itertools
import re
from dataclasses import dataclass, field

from agentforge.knowledge.parsing import Block

TARGET_TOKENS = 450  # starting points to tune with the eval harness (architecture §10.1)
MAX_TOKENS = 800
OVERLAP_TOKENS = 60
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def embedding_text(section_path: list[str], content: str) -> str:
    """What gets embedded: the section path as a header, then the chunk's exact text.

    Short policy sections ("Encryption: AES-256 at rest.") are far easier to match with their
    headings attached. Citations still use the exact content span, not this text.
    """
    header = " > ".join(section_path)
    return f"{header}\n\n{content}" if header else content


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text.split()) * 4 / 3))


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    text: str
    page_map: list[tuple[int, int]]  # (page, first char offset), ascending
    page_count: int
    segments: list["Segment"]


@dataclass(frozen=True, slots=True)
class Segment:
    """A sentence (or a heading line), the unit chunks are packed from."""

    start: int
    end: int
    page: int
    section: tuple[str, ...]
    tokens: int
    is_heading: bool = False


@dataclass(slots=True)
class Chunk:
    ordinal: int
    content: str
    char_start: int
    char_end: int
    page_start: int
    page_end: int
    section_path: list[str] = field(default_factory=lambda: [])
    token_estimate: int = 0


def normalize(blocks: list[Block]) -> NormalizedDocument:
    parts: list[str] = []
    page_map: list[tuple[int, int]] = []
    segments: list[Segment] = []
    section: list[tuple[int, str]] = []  # (level, title)
    offset = 0
    for block in blocks:
        text = re.sub(r"[ \t]+", " ", block.text).strip()
        if not text:
            continue
        if parts:
            parts.append("\n\n")
            offset += 2
        if not page_map or page_map[-1][0] != block.page:
            page_map.append((block.page, offset))
        if block.heading is not None:
            section = [s for s in section if s[0] < block.heading] + [(block.heading, text)]
        path = tuple(title for _, title in section)
        if block.heading is not None:
            segments.append(
                Segment(offset, offset + len(text), block.page, path, estimate_tokens(text), True)
            )
        else:
            cursor = 0
            for sentence in SENTENCE_END.split(text):
                start = text.index(sentence, cursor)
                cursor = start + len(sentence)
                segments.append(
                    Segment(
                        offset + start,
                        offset + cursor,
                        block.page,
                        path,
                        estimate_tokens(sentence),
                    )
                )
        parts.append(text)
        offset += len(text)
    pages = max((block.page for block in blocks), default=0)
    return NormalizedDocument("".join(parts), page_map, pages, segments)


def _split_long(segment: Segment, text: str) -> list[Segment]:
    """Very long sentences (tables, lists without punctuation) are split at word boundaries."""
    if segment.tokens <= MAX_TOKENS:
        return [segment]
    pieces = max(2, -(-segment.tokens // TARGET_TOKENS))
    step = (segment.end - segment.start) // pieces
    bounds = [segment.start]
    for i in range(1, pieces):
        cut = segment.start + i * step
        space = text.find(" ", cut, segment.end)
        bounds.append(space if space != -1 else cut)
    bounds.append(segment.end)
    return [
        Segment(a, b, segment.page, segment.section, estimate_tokens(text[a:b]))
        for a, b in itertools.pairwise(bounds)
        if b > a
    ]


def chunk(document: NormalizedDocument) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[Segment] = []

    def emit() -> None:
        body = [s for s in current if not s.is_heading] or current
        start, end = current[0].start, current[-1].end
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                content=document.text[start:end],
                char_start=start,
                char_end=end,
                page_start=min(s.page for s in current),
                page_end=max(s.page for s in current),
                section_path=list(body[-1].section),
                token_estimate=sum(s.tokens for s in current),
            )
        )

    for segment in (piece for s in document.segments for piece in _split_long(s, document.text)):
        size = sum(s.tokens for s in current)
        # Every heading starts a new chunk once the current one has body text: policy sections
        # are short, and one chunk per section gives precise citations. Consecutive headings
        # (a title followed by a section heading) stay together.
        new_section = segment.is_heading and any(not s.is_heading for s in current)
        if current and (size + segment.tokens > TARGET_TOKENS or new_section):
            emit()
            # Overlap: carry the last sentences of the same section into the next chunk.
            carry: list[Segment] = []
            if not segment.is_heading:
                for previous in reversed(current):
                    if previous.is_heading or previous.section != segment.section:
                        break
                    if sum(s.tokens for s in carry) + previous.tokens > OVERLAP_TOKENS:
                        break
                    carry.insert(0, previous)
            current = carry
        current.append(segment)
    if current:
        emit()
    return chunks
