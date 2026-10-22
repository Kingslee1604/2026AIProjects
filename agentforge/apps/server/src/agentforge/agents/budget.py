"""Per-run budgets (architecture §7.4): steps, model calls, tokens, and wall-clock time.

The runtime checks the budget before each step or model call and charges it afterwards, so a
run can overshoot by at most one call. Exhaustion raises BudgetExceededError, which is final
(retrying the same run with the same budget cannot succeed).
"""

import time
from dataclasses import dataclass, field

from agentforge.models import GenerationResult


class BudgetExceededError(Exception):
    """`kind` is one of: steps, llm_calls, tokens, wall_clock. Reported as `budget_exceeded`."""

    code = "budget_exceeded"

    def __init__(self, kind: str, used: float, limit: float) -> None:
        super().__init__(f"budget_exceeded: {kind} used {used:g} of {limit:g}")
        self.kind = kind
        self.used = used
        self.limit = limit


@dataclass(slots=True)
class Budget:
    max_steps: int = 50
    max_llm_calls: int = 20
    max_tokens: int = 60_000
    max_seconds: float = 900.0
    steps: int = 0
    llm_calls: int = 0
    tokens: int = 0
    _started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def remaining_llm_calls(self) -> int:
        return max(self.max_llm_calls - self.llm_calls, 0)

    def check(self) -> None:
        """Raise if any limit is already used up."""
        for kind, used, limit in (
            ("steps", self.steps, self.max_steps),
            ("llm_calls", self.llm_calls, self.max_llm_calls),
            ("tokens", self.tokens, self.max_tokens),
            ("wall_clock", self.elapsed, self.max_seconds),
        ):
            if used >= limit:
                raise BudgetExceededError(kind, used, limit)

    def step(self) -> None:
        self.check()
        self.steps += 1

    def charge(self, result: GenerationResult) -> None:
        self.llm_calls += result.attempts
        self.tokens += (result.prompt_tokens or 0) + (result.completion_tokens or 0)

    def snapshot(self) -> dict[str, float]:
        return {
            "steps": self.steps,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "seconds": round(self.elapsed, 2),
        }
