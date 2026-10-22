"""Screening at ingestion: does a document try to steer an AI? (R1-40 follow-up, baseline S10)

Prompts already fence document text as untrusted, and the evaluation shows injected
instructions are not followed. But a planted document's *statements* ("we hold FedRAMP High")
are quoted and verified like any other. So a document whose text addresses or instructs an AI
is flagged: answers that cite it get low confidence and a "check this source" warning, and a
person decides. Nothing is hidden or blocked.

The rules are deliberately plain and explainable; each flag carries the excerpt that matched.
A false alarm costs a reviewer one look.
"""

import re
from dataclasses import asdict, dataclass

EXCERPT = 200


@dataclass(frozen=True, slots=True)
class Flag:
    kind: str
    reason: str
    excerpt: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


_AI = (
    r"(?:ai|a\.i\.|llms?|language models?|chatbots?|automated assistants?|ai assistants?"
    r"|assistants?)"
)

RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "override",
        "tells an AI to ignore its instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}"
            r"\b(?:instructions?|rules|prompts?|directions|guidelines)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "addressed_to_ai",
        "addresses AI assistants directly",
        re.compile(
            rf"\b(?:note|notice|message|instructions?|attention|reminder)\b[^.\n]{{0,25}}"
            rf"\b(?:to|for)\b[^.\n]{{0,12}}\b{_AI}\b"
            rf"|^\s*(?:dear\s+)?(?:assistant|ai|chatgpt|claude|copilot)\s*[,:]"
            rf"|\b(?:you are|act as)\b[^.\n]{{0,20}}\b{_AI}\b",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "scripted_answer",
        "tells an AI what to answer",
        re.compile(
            r"\b(?:when|if|whenever)\b[^.\n]{0,40}\b(?:asked|asks|questioned)\b[^.\n]{0,120}"
            r"\b(?:state|answer|say|respond|reply|claim|write)\b[^.\n]{0,15}\bthat\b",
            re.IGNORECASE,
        ),
    ),
    (
        "conceal_source",
        "tells an AI to hide its sources",
        re.compile(
            r"\b(?:do not|don't|never)\b[^.\n]{0,10}\b(?:cite|mention|reveal|disclose)\b"
            r"[^.\n]{0,20}\b(?:sources?|this (?:document|note|notice))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "exfiltration",
        "asks for content to be sent elsewhere",
        re.compile(
            r"\b(?:send|e-?mail|forward|post|upload)\b[^.\n]{0,80}\bto\b\s+\S+@\S+"
            r"|\b(?:send|forward|post|upload)\b[^.\n]{0,60}https?://",
            re.IGNORECASE,
        ),
    ),
)


def _excerpt(text: str, start: int, end: int) -> str:
    """The sentence (or line) around a match, shortened to EXCERPT characters."""
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    stops = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    right = min(stops) + 1 if stops else len(text)
    sentence = " ".join(text[left:right].split())
    return sentence if len(sentence) <= EXCERPT else sentence[: EXCERPT - 1] + "…"


def screen(text: str) -> list[Flag]:
    """At most one flag per rule, in rule order."""
    flags: list[Flag] = []
    for kind, reason, pattern in RULES:
        match = pattern.search(text)
        if match:
            flags.append(Flag(kind, reason, _excerpt(text, match.start(), match.end())))
    return flags
