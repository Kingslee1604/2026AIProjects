# Install on Windows 11 with WSL2

AgentForge was developed and measured on a Windows 11 laptop with no GPU (Dell Inspiron 15 3520,
16 GB of RAM). On Linux, skip steps 1 and 2 and install Docker Engine and Ollama natively.

## What you need

| | Minimum | Notes |
|---|---|---|
| RAM | 16 GB | About 4 GB for the containers and 4–5 GB for Ollama with the default models |
| Disk | 15 GB free | Images, database, and models |
| Windows | 11, with virtualisation enabled | WSL2 |
| Docker Desktop | Current | WSL2 backend, integration enabled for your distro |
| Ollama for Windows | Current | Runs on Windows; the containers reach it |

## 1. WSL2 and Ubuntu

In PowerShell:

```powershell
wsl --install -d Ubuntu
```

Restart if asked, open **Ubuntu** from the Start menu, and create your Linux user.

**Cap WSL's memory** so it does not take memory Ollama needs. Create
`%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=6GB
swap=4GB
```

Then run `wsl --shutdown` in PowerShell and reopen Ubuntu. With less than 6 GB, builds and runs
still work, but slowly. `make doctor` reports the memory WSL can see.

## 2. Docker Desktop

Install Docker Desktop, and in **Settings → Resources → WSL Integration** turn on your Ubuntu
distro. In Ubuntu, `docker info` should now work.

## 3. Ollama and the models

Install Ollama for Windows and start it. In PowerShell or Ubuntu:

```bash
ollama pull qwen3:4b
ollama pull nomic-embed-text:v1.5
```

That is about 3 GB of downloads. With Docker Desktop's default network settings the containers
reach Ollama at `host.docker.internal:11434`. Leave Ollama listening on localhost (do not set
`OLLAMA_HOST=0.0.0.0`); that would expose it to your network.

No Ollama yet? Start with the fake models instead (`AGENTFORGE_MODEL_PROVIDER=fake make up`).
Answers then quote the top passage without real language understanding; this is useful to try
the interface.

## 4. AgentForge

In Ubuntu, clone into the **Linux file system** (`~/…`), not `/mnt/c/…` or a OneDrive folder.
Docker builds and file watching across the Windows boundary are slow and unreliable.

```bash
sudo apt-get update && sudo apt-get install -y make git python3
git clone https://github.com/Kingslee1604/2026AIProjects.git ~/src/2026AIProjects   # add -b feature/dev until R1 is merged
cd ~/src/2026AIProjects/agentforge
make doctor                                          # checks Docker, memory, Ollama, models, ports
make up                                              # builds and starts everything (see below)
make user-create EMAIL=you@example.com ROLE=admin    # asks for a password; there is no sign-up
```

**Measured** on the reference laptop (2026-10-19, fresh clone, Docker's build cache warm):
clone 2 s, `make up` 164 s, `user-create` 11 s; the Ask page loaded about 3 minutes after the
clone started. The first build on a clean machine also downloads the base images and
dependencies, so expect it to take longer.

Open <http://127.0.0.1:3000> and sign in. Then:

1. **Knowledge** → create a knowledge base → upload PDFs, Word files, or Markdown. Wait for
   "Ready".
2. **Ask** → ask a question. Every sentence shows the quote that proves it.
3. **Questionnaires** → upload an Excel or CSV questionnaire → start a run → review → download.

Demo documents and a 40-question questionnaire are in `demo/` (build them with `make demo-build`,
which needs the developer tools below).

## Everyday commands

| Command | What it does |
|---|---|
| `make up` | Start, or rebuild and restart after an update; data is kept |
| `make down` | Stop; data is kept in Docker volumes |
| `make ps` / `make logs` | Status / follow the logs |
| `make user-create EMAIL=… [ROLE=admin]` | Add a person |
| `make doctor` | Diagnose the machine |

## For developers

```bash
make tools   # pinned uv, Node.js, pnpm, gitleaks, pre-commit into ~/.local
make setup   # dependencies from lockfiles and git hooks
make check   # everything CI runs: lint, types, tests (real Postgres in Docker), docs, secrets, licenses
make e2e     # browser tests against the running stack
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker: command not found` or permission denied in Ubuntu | Turn on WSL Integration for the distro in Docker Desktop; reopen the terminal |
| `make up` stops, or Docker Desktop stops by itself | Restart Docker Desktop; check the WSL memory cap and free disk |
| "Ollama not reachable" in `make doctor` | Start the Ollama app on Windows. If you changed `AGENTFORGE_OLLAMA_BASE_URL` in `.env`, check it |
| Answers take minutes | Normal for a first question while the model loads, and slow on CPU (30–70 s per question on the reference laptop). Close heavy apps and keep the laptop on mains power: on battery, Windows throttles the CPU |
| Port 3000, 8000, or 5432 in use | Set `WEB_PORT`, `API_PORT`, or `POSTGRES_PORT` in `.env` and run `make up` |
