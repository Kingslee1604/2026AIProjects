"""Mechanical citation checks: no model involved, so a model cannot talk its way past them."""

import re
import unicodedata

MIN_QUOTE_CHARS = 12  # shorter quotes ("the", "yes") prove nothing

# Curly quotes, en and em dashes, and no-break spaces, by code point so the source stays ASCII.
_TRANSLATE = {
    0x2018: "'",
    0x2019: "'",
    0x201C: '"',
    0x201D: '"',
    0x2013: "-",
    0x2014: "-",
    0x00A0: " ",
}
_SPACE = re.compile(r"\s+")
_EDGE = re.compile(r"^[\s\"'.,;:]+|[\s\"'.,;:]+$")


def normalize(text: str) -> str:
    """Case-, whitespace-, and typography-insensitive form used for quote matching."""
    folded = unicodedata.normalize("NFKC", text).translate(_TRANSLATE).casefold()
    folded = folded.replace("**", "").replace("__", "")  # Markdown emphasis in parsed text
    return _SPACE.sub(" ", folded).strip()


MIN_CLAIM_OVERLAP = 0.5
_TERM = re.compile(r"[a-z0-9][a-z0-9-]*")
_STOP = frozenset(
    [
        "the",
        "and",
        "are",
        "for",
        "with",
        "that",
        "this",
        "from",
        "has",
        "have",
        "was",
        "were",
        "its",
        "our",
        "all",
        "any",
        "not",
        "but",
        "can",
        "may",
        "will",
        "which",
        "who",
        "per",
        "into",
        "than",
        "then",
        "they",
        "them",
        "their",
        "there",
        "these",
        "those",
        "such",
        "been",
        "also",
    ]
)
# A drafted "claim" that only says the evidence lacks the answer is an abstention in disguise.
# Patterns talk about the evidence itself, so real negative facts ("Contractors are not
# covered by the policy") are not mistaken for abstentions.
_SOURCE = (
    r"(?:provided |given |available )?"
    r"(?:evidence|passages?|documents?|context|sources?|knowledge base)"
)
_NON_ANSWER = re.compile(
    r"\bnot (?:explicitly )?(?:mentioned|stated|specified|provided|described|addressed|"
    rf"documented|found|covered|included) (?:in|by) (?:the |any )?{_SOURCE}\b"
    rf"|\b(?:the )?{_SOURCE} (?:does|do) not (?:mention|state|specify|say|describe|address|"
    r"contain|include|provide|cover)\b"
    r"|\bno (?:information|mention|details?|evidence) (?:about|on|regarding|of|for)\b"
    r"|\b(?:does|do) not (?:specify|provide|contain|include|give) any (?:information|details?)\b"
    r"|\binsufficient evidence\b",
    re.IGNORECASE,
)


def _terms(text: str) -> set[str]:
    return {t for t in _TERM.findall(normalize(text)) if len(t) > 2 and t not in _STOP}


def claim_overlap(claim: str, quote: str) -> float:
    """Share of the claim's content words that also appear in the quote (0..1).

    A real quote that is unrelated to the claim (a heading, a neighbouring sentence) proves
    nothing; such claims go to the LLM verifier instead of passing mechanically.
    """
    claim_terms = _terms(claim)
    if not claim_terms:
        return 0.0
    return len(claim_terms & _terms(quote)) / len(claim_terms)


def is_non_answer(text: str) -> bool:
    return bool(_NON_ANSWER.search(text))


_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")


def numbers_supported(claim: str, passages: list[str]) -> bool:
    """Every number in the claim (256, 2.56, 99.9, 24:00) appears in a cited passage.

    Small models garble numbers ("AES-2.56" for "AES-256"); a wrong number is the most
    damaging error a questionnaire answer can contain.
    """
    haystack = " ".join(normalize(p) for p in passages)
    found = set(_NUMBER.findall(haystack))
    return all(n in found for n in _NUMBER.findall(normalize(claim)))


_ELLIPSIS = chr(0x2026)


def looks_truncated(text: str) -> bool:
    """A claim with an ellipsis ("encrypted with AES-...") is unfinished, not a fact."""
    return "..." in text or _ELLIPSIS in text


def quote_found(quote: str, passages: list[str]) -> bool:
    """True when the quote appears verbatim (after normalization) in any passage."""
    needle = _EDGE.sub("", normalize(quote))
    if len(needle) < MIN_QUOTE_CHARS:
        return False
    return any(needle in normalize(p) for p in passages)
