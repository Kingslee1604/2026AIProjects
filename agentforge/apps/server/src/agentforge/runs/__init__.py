"""Runs: long workflows (questionnaires) on the durable engine (ADR-0002: DBOS).

Content lives in RLS-protected app tables (runs, run_items, step_records, run_events,
artifacts); the engine holds IDs and status only.
"""
