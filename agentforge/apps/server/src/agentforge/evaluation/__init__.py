"""Evaluation harness (R1-40): retrieval and grounded-answer quality on a gold dataset.

`agentforge eval run` loads a corpus into a temporary knowledge base, measures retrieval
(recall@k, MRR, approximate vs exact vector search), sweeps the abstention threshold, answers
every question with each chat model under test, and writes a JSON and a Markdown report.
"""
