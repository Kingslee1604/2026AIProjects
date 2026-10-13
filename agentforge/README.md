# AgentForge AI

**Self-hostable platform for evidence-grounded, human-supervised AI workflows over private documents.**

> Status: **early development** (Release 1, milestone M1 "Foundations"). Nothing is usable yet.
> "AgentForge" is a working name, pending a trademark check before any public launch.

## What Release 1 will do

1. **Ask your documents.** Upload PDF, Word, or text documents and ask questions. Every answer
   links to the exact source passage, each claim is checked against its source, and the system
   says "insufficient evidence" instead of guessing.
2. **Fill a questionnaire.** Upload an Excel questionnaire with many questions. AgentForge drafts
   every answer from your documents, a person reviews the uncertain ones, and you download the
   completed spreadsheet.

Everything runs locally with open-weight models through [Ollama](https://ollama.com). No paid
service is required.

## Developer setup (WSL2 Ubuntu or Linux)

AgentForge lives in the `agentforge/` folder of the
[2026AIProjects](https://github.com/Kingslee1604/2026AIProjects) repository. Clone it into the
Linux filesystem, not under `/mnt/c` or a OneDrive folder.

```bash
git clone https://github.com/Kingslee1604/2026AIProjects.git ~/src/2026AIProjects
cd ~/src/2026AIProjects/agentforge
make tools   # pinned uv, Node.js, pnpm, gitleaks, pre-commit into ~/.local (checksums verified)
make setup   # dependencies from lockfiles + git hooks
make check   # lint, type-check, tests, diagram validation, secret scan
```

`make help` lists every target.

## Documentation

- [Architecture & product design (v0.2, approved)](docs/design/ARCHITECTURE_DESIGN.md)
- [Release 1 implementation plan](docs/design/RELEASE_1_PLAN.md)

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE). Security reports: see [SECURITY.md](SECURITY.md).
