"""R1-49 measurements against the running stack, through the public API only.

Two parts, because they need different model settings:

  real  (AGENTFORGE_MODEL_PROVIDER=auto): index the Northwind corpus; retrieval latency for
        the 40 questions (3 rounds); the 12-question and 40-question questionnaire runs end to
        end, with tokens, model calls, per-step and per-call latency.
  fake  (AGENTFORGE_MODEL_PROVIDER=fake, run rate limit raised): a burst of runs, to measure
        the engine's enqueue -> start latency without model time.

Usage (bench/measure.sh runs both and collects the hardware manifest and memory):
  BENCH_EMAIL=... BENCH_PASSWORD=... uv run python bench/measure.py real OUT_DIR
"""

import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "demo" / "build" / "corpus"
QUESTIONNAIRE = ROOT / "demo" / "questionnaire" / "northwind-questionnaire.json"
DEMO12 = ROOT / "demo" / "build" / "northwind-questionnaire-demo12.xlsx"
FULL40 = ROOT / "demo" / "build" / "northwind-questionnaire.xlsx"
API = "http://127.0.0.1:8000"
DONE = {"waiting_review", "succeeded", "failed", "cancelled", "timed_out"}


def pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile (no interpolation), rounded for the report."""
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(p / 100 * len(ordered) + 0.5)))
    return round(ordered[rank - 1], 3)


def stats(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "p50": pct(values, 50),
        "p95": pct(values, 95),
        "max": round(max(values), 3),
        "mean": round(statistics.fmean(values), 3),
    }


def ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


class Api:
    def __init__(self, email: str, password: str) -> None:
        self.http = httpx2.Client(base_url=API, timeout=600)
        response = self.http.post("/v1/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        self.csrf = str(response.json()["csrf_token"])

    def get(self, path: str, **params: Any) -> Any:
        response = self.http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        headers = {"X-CSRF-Token": self.csrf, **kwargs.pop("headers", {})}
        response = self.http.post(path, json=body, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def upload(self, path: str, file: Path) -> Any:
        return self.post(
            path,
            content=file.read_bytes(),
            headers={"X-Filename": file.name, "Content-Type": "application/octet-stream"},
        )


def mark(marks: list[dict[str, Any]], phase: str, event: str) -> None:
    marks.append({"phase": phase, "event": event, "t": time.time()})


def index_corpus(api: Api, marks: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    mark(marks, "indexing", "start")
    kb = api.post("/v1/knowledge-bases", {"name": f"Bench {datetime.now():%Y-%m-%d %H:%M:%S}"})
    started = time.time()
    files = sorted(CORPUS.iterdir())
    for file in files:
        api.upload(f"/v1/knowledge-bases/{kb['id']}/documents", file)
    while True:
        docs = api.get(f"/v1/knowledge-bases/{kb['id']}/documents")
        if all(d["status"] in {"ready", "failed"} for d in docs) and len(docs) == len(files):
            break
        time.sleep(2)
    mark(marks, "indexing", "end")
    return kb["id"], {
        "documents": len(docs),
        "failed": [d["filename"] for d in docs if d["status"] == "failed"],
        "chunks": sum(int(d.get("chunk_count") or 0) for d in docs),
        "seconds_upload_to_all_ready": round(time.time() - started, 1),
    }


def retrieval(api: Api, kb: str, marks: list[dict[str, Any]], rounds: int = 3) -> dict[str, Any]:
    questions = [q["question"] for q in json.loads(QUESTIONNAIRE.read_text())["questions"]]
    mark(marks, "retrieval", "start")
    latencies: list[float] = []
    for _ in range(rounds):
        for question in questions:
            started = time.perf_counter()
            api.post("/v1/knowledge/search", {"query": question, "knowledge_base_ids": [kb]})
            latencies.append((time.perf_counter() - started) * 1000)
    mark(marks, "retrieval", "end")
    return {
        "what": "POST /v1/knowledge/search, k=8, client-side wall time: query embedding "
        "(Ollama) + vector and keyword search + fusion, through HTTP",
        "questions": len(questions),
        "rounds": rounds,
        "ms": stats(latencies),
    }


def wait_for(api: Api, run_id: str, poll: float = 5.0) -> dict[str, Any]:
    while True:
        run = api.get(f"/v1/runs/{run_id}")
        if run["status"] in DONE:
            return run
        time.sleep(poll)


def questionnaire_run(
    api: Api, kb: str, file: Path, phase: str, marks: list[dict[str, Any]]
) -> dict[str, Any]:
    questionnaire = api.upload("/v1/questionnaires", file)
    mark(marks, phase, "start")
    run = api.post(
        "/v1/runs",
        {"questionnaire_id": questionnaire["id"], "knowledge_base_ids": [kb], "title": phase},
    )
    observed = time.time()
    run = wait_for(api, run["id"])
    observed_done = time.time()
    mark(marks, phase, "end")
    trace = api.get(f"/v1/runs/{run['id']}/trace")
    steps = trace["steps"]
    finished = [ts(s["finished_at"]) for s in steps if s["finished_at"]]
    by_node: dict[str, list[float]] = {}
    for step in steps:
        if step["finished_at"]:
            seconds = ts(step["finished_at"]) - ts(step["started_at"])
            by_node.setdefault(step["node_id"], []).append(seconds)
    calls: dict[str, list[float]] = {}
    outcomes: dict[str, int] = {}
    tokens = {"prompt": 0, "completion": 0}
    for step in steps:
        for call in api.get(f"/v1/runs/{run['id']}/steps/{step['id']}/calls"):
            calls.setdefault(call["purpose"], []).append(call["latency_ms"] / 1000)
            outcome = call["error_class"] or call["status"]
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            tokens["prompt"] += call["prompt_tokens"] or 0
            tokens["completion"] += call["completion_tokens"] or 0
    items = api.get(f"/v1/runs/{run['id']}/items")
    return {
        "run_id": run["id"],
        "status": run["status"],
        "questions": run["item_count"],
        "answered": run["items_done"],
        "confidence": {
            c: sum(1 for i in items if i["confidence"] == c) for c in ("high", "medium", "low")
        },
        "insufficient_evidence": sum(1 for i in items if i["status"] == "insufficient_evidence"),
        "seconds_end_to_end": round(max(finished) - ts(run["created_at"]), 1),
        "seconds_observed_by_client": round(observed_done - observed, 1),
        "seconds_enqueue_to_start": round(ts(run["started_at"]) - ts(run["created_at"]), 3)
        if run["started_at"]
        else None,
        "llm_calls": trace["llm_calls"],
        "tokens": trace["tokens"],
        "tokens_split": tokens,
        "model_call_outcomes": outcomes,
        "step_seconds_by_node": {node: stats(v) for node, v in sorted(by_node.items())},
        "model_call_seconds_by_purpose": {p: stats(v) for p, v in sorted(calls.items())},
        "step_attempts": len(steps),
    }


def real(api: Api, out: Path) -> None:
    marks: list[dict[str, Any]] = []
    kb, indexing = index_corpus(api, marks)
    result: dict[str, Any] = {"part": "real", "indexing": indexing}
    result["retrieval"] = retrieval(api, kb, marks)
    (out / "real.json").write_text(json.dumps(result, indent=2) + "\n")  # partial, in case
    result["run_12"] = questionnaire_run(api, kb, DEMO12, "run_12", marks)
    (out / "real.json").write_text(json.dumps(result, indent=2) + "\n")
    result["run_40"] = questionnaire_run(api, kb, FULL40, "run_40", marks)
    result["marks"] = marks
    (out / "real.json").write_text(json.dumps(result, indent=2) + "\n")


def smoke(api: Api, out: Path) -> None:
    """Every code path of `real`, quickly: run it against a fake-model stack first."""
    marks: list[dict[str, Any]] = []
    kb, indexing = index_corpus(api, marks)
    result = {"part": "smoke", "indexing": indexing, "retrieval": retrieval(api, kb, marks, 1)}
    result["run_12"] = questionnaire_run(api, kb, DEMO12, "run_12", marks)
    (out / "smoke.json").write_text(json.dumps(result, indent=2) + "\n")


def run12(api: Api, out: Path) -> None:
    """Only the 12-question run on the real model: for comparing settings quickly."""
    marks: list[dict[str, Any]] = []
    kb, _ = index_corpus(api, marks)
    result = questionnaire_run(api, kb, DEMO12, "run_12", marks)
    name = f"run12-{datetime.now():%H%M%S}.json"
    (out / name).write_text(json.dumps(result, indent=2) + "\n")
    print(out / name)


def fake(api: Api, out: Path, runs: int = 30) -> None:
    """A burst of runs on the fake model: how long a run waits before the engine starts it."""
    kb, _ = index_corpus(api, [])
    questionnaire = api.upload("/v1/questionnaires", DEMO12)
    ids = [
        api.post(
            "/v1/runs",
            {"questionnaire_id": questionnaire["id"], "knowledge_base_ids": [kb], "title": "q"},
        )["id"]
        for _ in range(runs)
    ]
    finished = [wait_for(api, run_id, poll=1.0) for run_id in ids]
    to_start = [ts(r["started_at"]) - ts(r["created_at"]) for r in finished if r["started_at"]]
    to_first_step: list[float] = []
    for run in finished:
        steps = api.get(f"/v1/runs/{run['id']}/trace")["steps"]
        if steps:
            first = min(ts(s["started_at"]) for s in steps)
            to_first_step.append(first - ts(run["created_at"]))
    result = {
        "part": "fake",
        "what": f"{runs} runs of the 12-question questionnaire started back to back on the fake "
        "model; enqueue = run row created by POST /v1/runs",
        "statuses": {s: sum(1 for r in finished if r["status"] == s) for s in DONE},
        "seconds_enqueue_to_run_started": stats(to_start),
        "seconds_enqueue_to_first_step": stats(to_first_step),
    }
    (out / "fake.json").write_text(json.dumps(result, indent=2) + "\n")


def read_csv(path: Path) -> list[dict[str, float]]:
    if not path.exists():
        return []
    lines = path.read_text().strip().splitlines()
    header = lines[0].split(",")
    return [dict(zip(header, map(float, line.split(",")), strict=True)) for line in lines[1:]]


def peaks(rows: list[dict[str, float]], start: float, end: float) -> dict[str, float]:
    window = [r for r in rows if start * 1000 <= r["epoch_ms"] <= end * 1000]
    columns = [c for c in (window[0] if window else {}) if c != "epoch_ms"]
    return {c: max(r[c] for r in window) for c in columns}


def summary(out: Path) -> None:
    manifest = json.loads((out / "manifest.json").read_text())
    real_part: dict[str, Any] = json.loads((out / "real.json").read_text())
    fake_part: dict[str, Any] = (
        json.loads((out / "fake.json").read_text()) if (out / "fake.json").exists() else {}
    )
    memory = read_csv(out / "memory_windows.csv")
    wsl = read_csv(out / "memory_wsl.csv")
    phases: dict[str, dict[str, float]] = {}
    for m in real_part.get("marks", []):
        phases.setdefault(m["phase"], {})[m["event"]] = m["t"]
    peak = {
        phase: {**peaks(memory, t["start"], t["end"]), **peaks(wsl, t["start"], t["end"])}
        for phase, t in phases.items()
        if "start" in t and "end" in t
    }
    real_part["memory_peak_mb"] = peak
    (out / "real.json").write_text(json.dumps(real_part, indent=2) + "\n")

    win = manifest.get("windows", {})
    lines = [
        f"# Measurements {out.name}",
        "",
        f"**Machine:** {win.get('model', '?')}, {win.get('cpu', '?')} "
        f"({win.get('cores', '?')} cores / {win.get('threads', '?')} threads), "
        f"{win.get('ram_gb', '?')} GB RAM, no GPU; {win.get('os', '?')}; "
        f"mains power: {win.get('on_mains_power', '?')}. WSL2 memory "
        f"{manifest['wsl']['memory_total_mb']} MB. "
        f"Ollama {manifest['ollama'].get('version', '?')}, "
        f"models {manifest['ollama'].get('models', {})}. Commit `{manifest['commit']}`, "
        f"{manifest['date_utc']}. Raw data: `real.json`, `fake.json`, `memory_*.csv`, "
        "`manifest.json`. All values are **Measured** on this machine only.",
        "",
        "| Measure | Result |",
        "|---|---|",
    ]
    idx = real_part["indexing"]
    lines.append(
        f"| Index the Northwind corpus ({idx['documents']} documents, {idx['chunks']} chunks) "
        f"| {idx['seconds_upload_to_all_ready']} s from upload to all ready |"
    )
    r = real_part["retrieval"]["ms"]
    lines.append(
        f"| Retrieval latency ({r['n']} searches, HTTP + query embedding + hybrid search) "
        f"| p50 **{r['p50']:.0f} ms**, p95 **{r['p95']:.0f} ms** |"
    )
    for key, label in (("run_12", "12-question run"), ("run_40", "40-question run")):
        run = real_part.get(key)
        if not run:
            continue
        lines.append(
            f"| {label}, end to end (queued → every answer ready for review) "
            f"| **{run['seconds_end_to_end'] / 60:.1f} min** "
            f"({run['seconds_end_to_end'] / run['questions']:.0f} s per question); "
            f"status {run['status']}, {run['answered']}/{run['questions']} answered |"
        )
        lines.append(
            f"| {label}: model calls / tokens | {run['llm_calls']} calls, {run['tokens']} tokens "
            f"({run['tokens_split']['prompt']} prompt, {run['tokens_split']['completion']} "
            "completion) |"
        )
        for node, s in run["step_seconds_by_node"].items():
            lines.append(
                f"| {label}: step `{node}` | p50 {s['p50']:.1f} s, p95 {s['p95']:.1f} s "
                f"(n={s['n']}) |"
            )
        for purpose, s in run["model_call_seconds_by_purpose"].items():
            lines.append(
                f"| {label}: model call `{purpose}` | p50 {s['p50']:.1f} s, "
                f"p95 {s['p95']:.1f} s (n={s['n']}) |"
            )
        if key in peak:
            p = peak[key]
            lines.append(
                f"| {label}: peak memory | WSL2 VM {p.get('vmmem_mb', 0):.0f} MB "
                f"(Linux used {p.get('wsl_used_mb', 0):.0f} MB), "
                f"Ollama {p.get('ollama_mb', 0):.0f} MB |"
            )
    if fake_part:
        s = fake_part["seconds_enqueue_to_run_started"]
        f = fake_part["seconds_enqueue_to_first_step"]
        lines.append(
            f"| Enqueue → start, fake model, {s.get('n', 0)} runs started at once "
            f"| run started p50 {s.get('p50', 0):.2f} s, **p95 {s.get('p95', 0):.2f} s**; "
            f"first step p95 {f.get('p95', 0):.2f} s |"
        )
    (out / "summary.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    import os

    part, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if part == "summary":
        summary(out)
        return
    api = Api(os.environ["BENCH_EMAIL"], os.environ["BENCH_PASSWORD"])
    {"real": real, "fake": fake, "smoke": smoke, "run12": run12}[part](api, out)


if __name__ == "__main__":
    main()
