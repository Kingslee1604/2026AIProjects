"""Sandboxed parsing (security baseline S5): a separate process with resource limits.

Limits: wall-clock timeout, CPU seconds, address space, no file writes, few open files, no
core dumps, a minimal environment, and isolated Python mode. This contains crashes, hangs, and
memory bombs in parser libraries. It is a mitigation, not a full sandbox (no network or
syscall isolation): the hosted product needs gVisor/nsjail-style isolation (R3).
"""

import json
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PARSE_TIMEOUT_SECONDS = 120
CPU_SECONDS = 90
MEMORY_BYTES = 1536 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


class ParseError(Exception):
    """The document could not be parsed; the message is safe to show to the user."""


@dataclass(frozen=True, slots=True)
class Block:
    page: int
    text: str
    heading: int | None  # 1-6 for headings, None for body text


def _limit_resources() -> None:  # runs in the child before exec
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # the parser writes no files
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def parse_file(path: Path, media_type: str) -> list[Block]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and module, no shell
            [
                sys.executable,
                "-I",
                "-m",
                "agentforge.knowledge.parse_worker",
                str(path),
                media_type,
            ],
            capture_output=True,
            timeout=PARSE_TIMEOUT_SECONDS,
            preexec_fn=_limit_resources,
            check=False,
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"},
        )
    except subprocess.TimeoutExpired as error:
        raise ParseError(f"Parsing took longer than {PARSE_TIMEOUT_SECONDS} seconds.") from error
    if completed.returncode != 0 or not completed.stdout:
        raise ParseError(
            "The parser stopped unexpectedly (the file may be damaged or too complex)."
        )
    if len(completed.stdout) > MAX_OUTPUT_BYTES:
        raise ParseError("The document contains too much text.")
    result: dict[str, Any] = json.loads(completed.stdout)
    if "error" in result:
        raise ParseError(f"Could not read the document ({result['error']}).")
    blocks = [Block(int(b["page"]), str(b["text"]), b.get("heading")) for b in result["blocks"]]
    if not blocks:
        raise ParseError(
            "No text was found in the document (scanned PDFs need OCR, not yet supported)."
        )
    return blocks
