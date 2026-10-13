# 2026 AI Projects

AI engineering projects built in 2026. Each project lives in its own folder with its own
README, license, and tooling.

| Project | Description | Status |
|---|---|---|
| [agentforge](agentforge/) | Self-hostable platform for evidence-grounded, human-supervised AI workflows over private documents | Early development (Release 1, milestone M1) |
| [production-rag](production-rag/) | "Ask My Cardiology Docs": CPU-only RAG with hybrid retrieval, reranking, verified citations, and a CI eval gate | Working prototype with measured fixture-set results |

## Repository conventions

- Repository-wide hygiene lives at the root: `.gitignore`, `.gitattributes`, `.editorconfig`,
  `.gitleaks.toml` (secret scanning), and `.pre-commit-config.yaml`.
- Project tooling (Makefile, lockfiles, dependencies) lives inside each project folder.
- GitHub Actions only reads workflows at the root, so each project's workflows live in
  `.github/workflows/<project>-*.yml` and run with that project folder as the working directory.
- Clone into the Linux filesystem when using WSL2 (for example `~/src/2026AIProjects`), not under
  `/mnt/c` or a OneDrive folder.
