"""Engine conformance harness (R1-20, architecture §7.3).

The same mini-flow (plan -> 20 items with a fake LLM, at most 4 at a time per run -> review
wait -> idempotent export) runs on every candidate engine, and the same tests check the hard
gates H1-H8 against it. Spike code: kept for the ADR-2 record, not part of the product.
"""
