"""The gold dataset: questions, expected answers, and the exact quotes that prove them."""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GoldEvidence:
    document: str  # source file name; built copies may have another extension (.docx, .pdf)
    quote: str


@dataclass(frozen=True, slots=True)
class EvalQuestion:
    id: str
    question: str
    section: str
    demo: bool
    answerable: bool
    expected: str
    evidence: tuple[GoldEvidence, ...]
    injection_target: bool


@dataclass(frozen=True, slots=True)
class Dataset:
    name: str
    questions: list[EvalQuestion]
    corpus: list[Path]
    adversarial: frozenset[str]  # document names (no extension) written to mislead the model
    sha256: str  # of the questionnaire and gold files: which dataset version was measured

    def is_adversarial(self, filename: str) -> bool:
        return Path(filename).stem in self.adversarial


def load_dataset(root: Path, name: str = "northwind") -> Dataset:
    """`root` is the repository's demo/ folder: questionnaire/, eval/gold.json, the built
    corpus (make demo-build) under build/corpus/, and the adversarial sources under
    corpus/<name>/adversarial/."""
    questionnaire_path = root / "questionnaire" / f"{name}-questionnaire.json"
    gold_path = root / "eval" / "gold.json"
    questionnaire = json.loads(questionnaire_path.read_text(encoding="utf-8"))
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    answers = {a["id"]: a for a in gold["answers"]}
    questions = [
        EvalQuestion(
            id=q["id"],
            question=q["question"],
            section=q["section"],
            demo=bool(q.get("demo")),
            answerable=bool(answers[q["id"]]["answerable"]),
            expected=answers[q["id"]]["expected"],
            evidence=tuple(
                GoldEvidence(e["document"], e["quote"]) for e in answers[q["id"]]["evidence"]
            ),
            injection_target=bool(answers[q["id"]].get("injection_target")),
        )
        for q in questionnaire["questions"]
    ]
    corpus = sorted(p for p in (root / "build" / "corpus").iterdir() if p.is_file())
    if not corpus:
        raise FileNotFoundError(f"no corpus files in {root / 'build' / 'corpus'} (make demo-build)")
    adversarial = frozenset(p.stem for p in (root / "corpus" / name / "adversarial").glob("*"))
    digest = hashlib.sha256(
        questionnaire_path.read_bytes() + b"\0" + gold_path.read_bytes()
    ).hexdigest()
    return Dataset(
        name=name, questions=questions, corpus=corpus, adversarial=adversarial, sha256=digest
    )


def _canonical(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).lower()


def same_document(filename: str, document: str) -> bool:
    """A built file matches its source by name without extension (03-encryption-standard.docx
    is the build of 03-encryption-standard.md)."""
    return Path(filename).stem == Path(document).stem


def contains_quote(passage: str, quote: str) -> bool:
    """Containment ignoring case, runs of whitespace, and Unicode compatibility forms."""
    return _canonical(quote) in _canonical(passage)


def is_relevant(filename: str, content: str, evidence: GoldEvidence) -> bool:
    return same_document(filename, evidence.document) and contains_quote(content, evidence.quote)
