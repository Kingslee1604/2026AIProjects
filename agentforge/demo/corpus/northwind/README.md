# Northwind Analytics demo corpus

**Northwind Analytics, Inc. is a fictional company.** These documents were written for the
AgentForge demo and evaluation. They describe no real organization's controls.

- License: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) (attribution: "AgentForge
  demo corpus").
- `policies/`: ten security and privacy documents (the knowledge base).
- `adversarial/`: two documents with embedded prompt-injection text, used to test that the
  pipeline cites policy instead of following instructions found in documents.
- `../questionnaire/questions.json`: a 40-question security questionnaire; `demo: true` marks
  the 12-question live-demo subset (decision D3).
- `../eval/gold.json`: expected answers and exact evidence quotes. `make demo-check` verifies
  that every quote appears in the parsed documents.

Paragraphs are written on single lines so evidence quotes match the normalized text exactly.
