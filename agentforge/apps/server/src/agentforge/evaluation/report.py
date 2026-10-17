"""The evaluation report: JSON (everything) and Markdown (for people), with a manifest that
says exactly what was measured: dataset version, code version, models and their digests,
settings, and the hardware it ran on."""

import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2


def _proc_value(path: str, key: str) -> str | None:
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith(key):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def hardware() -> dict[str, Any]:
    """What the measuring process sees (inside WSL2 or a container: the VM's limits)."""
    memory_kb = _proc_value("/proc/meminfo", "MemTotal")
    return {
        "cpu": _proc_value("/proc/cpuinfo", "model name") or platform.processor() or None,
        "logical_cpus": os.cpu_count(),
        "memory_gb": round(int(memory_kb.split()[0]) / 1024 / 1024, 1) if memory_kb else None,
        "platform": platform.platform(),
        "gpu": None,  # the reference machine is CPU-only; models run in Ollama on the host
    }


def model_digests(ollama_base_url: str, models: list[str]) -> dict[str, str | None]:
    """Ollama digests (first 12 characters) of the models used, so results are reproducible."""
    names = {m.split(":", 1)[1] for m in models if m.startswith("ollama:")}
    digests: dict[str, str | None] = dict.fromkeys(models)
    if not names:
        return digests
    try:
        tags = httpx2.get(f"{ollama_base_url}/api/tags", timeout=5).json().get("models", [])
    except (httpx2.HTTPError, ValueError):
        return digests
    found = {t.get("name"): str(t.get("digest", ""))[:12] for t in tags}
    for model in models:
        if model.startswith("ollama:"):
            digests[model] = found.get(model.split(":", 1)[1])
    return digests


def manifest(
    *,
    dataset: str,
    dataset_sha256: str,
    git_sha: str | None,
    settings: dict[str, Any],
    digests: dict[str, str | None],
    started: datetime,
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "dataset_sha256": dataset_sha256,
        "git_sha": git_sha,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "settings": settings,
        "model_digests": digests,
        "hardware": hardware(),
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def markdown(report: dict[str, Any]) -> str:
    m = report["manifest"]
    r = report["retrieval"]
    lines = [
        f"# Evaluation: {m['dataset']} ({report['questions']} questions)",
        "",
        f"Measured {m['finished_at']} at commit `{m['git_sha'] or 'unknown'}` on "
        f"{m['hardware']['cpu'] or 'unknown CPU'} ({m['hardware']['logical_cpus']} logical CPUs, "
        f"{m['hardware']['memory_gb']} GB visible to the measuring process, no GPU). "
        f"Dataset `{m['dataset_sha256'][:12]}`.",
        "",
        "| Setting | Value |",
        "| --- | --- |",
        *[f"| {k} | `{v}` |" for k, v in m["settings"].items()],
        *[f"| digest of {k} | `{v}` |" for k, v in m["model_digests"].items()],
        "",
        "## Retrieval",
        "",
        f"{r['answerable']} answerable questions; a hit is a passage from the right document "
        "that contains the gold quote. Hybrid search (vector + full text, fused).",
        "",
        "| Metric | @5 | @10 | @20 |",
        "| --- | --- | --- | --- |",
        "| Recall (share of gold quotes found) | "
        + " | ".join(_pct(r["recall_at"][k]) for k in ("5", "10", "20"))
        + " |",
        "| All quotes of the question found | "
        + " | ".join(_pct(r["all_quotes_at"][k]) for k in ("5", "10", "20"))
        + " |",
        "",
        f"MRR (first hit): **{r['mrr']}**. Approximate (HNSW) vs exact vector search, "
        f"top 10 overlap: **{_pct(r['ann_vs_exact_overlap_at_10'])}**.",
        "",
        "## Abstention gate",
        "",
        "The pipeline abstains without calling the model when no retrieved passage is within "
        f"the distance threshold. Current: **{r['gate']['current']}**; fewest mistakes on this "
        f"dataset: **{r['gate']['recommended']}**.",
        "",
        "| Threshold | Precision | Recall | Refused answerable | Answered unanswerable |",
        "| --- | --- | --- | --- | --- |",
        *[
            f"| {row['threshold']} | {_pct(row['precision'])} | {_pct(row['recall'])} | "
            f"{row['false_abstentions']} | {row['missed']} |"
            for row in r["gate"]["sweep"]
        ],
    ]
    for model, result in report["answers"].items():
        s = result["summary"]
        a = s["abstention"]
        so = s["structured_output"]
        lines += [
            "",
            f"## Answers: `{model}`",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Answered / abstained / failed | {s['answered']} / {s['abstained']} / "
            f"{s['failed']} |",
            f"| Abstention precision / recall | {_pct(a['precision'])} / {_pct(a['recall'])} |",
            f"| Refused answerable / answered unanswerable | {a['false_abstentions']} / "
            f"{a['missed']} |",
            f"| Answers citing a gold passage | {_pct(s['grounded_in_gold'])} |",
            f"| Citation validity (kept sentences proven by a verbatim quote) | "
            f"{_pct(s['citation_validity'])} of {s['claims_kept']} |",
            f"| Unsupported-claim rate | {_pct(s['unsupported_claim_rate'])} |",
            f"| Injection targets resisted | {s['injection_resisted']} of "
            f"{s['injection_targets']} |",
            f"| Structured output valid (first try) | {_pct(so['validity'])} "
            f"({_pct(so['first_try_valid'])}) of {so['calls']} calls |",
            f"| Seconds per question p50 / p95 | {s['seconds_p50']} / {s['seconds_p95']} |",
            f"| Model calls / tokens | {s['model_calls']} / {s['tokens']} |",
            "",
            "<details><summary>Per question</summary>",
            "",
            "| ID | Expected | Outcome | Gold cited | Answer |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in result["per_question"]:
            expected = "answer" if row["answerable"] else "abstain"
            gold = {True: "yes", False: "no", None: ""}[row.get("cites_gold")]
            answer = (row.get("answer") or row.get("error") or "").replace("|", "\\|")
            lines.append(
                f"| {row['id']} | {expected} | {row['status']} | {gold} | {answer[:160]} |"
            )
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def write(report: dict[str, Any], out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "report.json"
    md_path = out / "report.md"
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return json_path, md_path
