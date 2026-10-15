"""DBOS workflows, registered on import (the worker imports this before launching DBOS).

The questionnaire workflow arrives with R1-33; until then no run template can execute, and
runs stay 'queued' (the start reconciler keeps offering them).
"""
