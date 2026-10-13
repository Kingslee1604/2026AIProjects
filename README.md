# 2026 AI Projects

AI engineering projects built in 2026. Each project lives in its own folder with its own
README, license, and tooling.

| Project | Description | Status |
|---|---|---|
| [agentforge](agentforge/) | Self-hostable platform for evidence-grounded, human-supervised AI workflows over private documents | Early development (Release 1, milestone M1) |

## Repository conventions

- Repository-wide hygiene lives at the root: `.gitignore`, `.gitattributes`, `.editorconfig`,
  `.gitleaks.toml` (secret scanning), and `.pre-commit-config.yaml`.
- Project tooling (Makefile, lockfiles, CI checks) lives inside each project folder.
- Clone into the Linux filesystem when using WSL2 (for example `~/src/2026AIProjects`), not under
  `/mnt/c` or a OneDrive folder.
