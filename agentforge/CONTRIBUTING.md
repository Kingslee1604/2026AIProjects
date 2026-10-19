# Contributing to AgentForge

Thank you for helping. Issues and pull requests are welcome. For anything larger than a small
fix, please open an issue first so we can agree on the approach.

## Ground rules

- **Security problems:** never in a public issue. See [SECURITY.md](SECURITY.md).
- **Be kind:** everyone here follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- **No secrets, ever:** no `.env` files, keys, or real customer documents in commits.
  gitleaks runs in the pre-commit hook and in CI.

## Set up

Follow the [Windows/WSL2 guide](docs/guides/windows-wsl2.md) (or the same steps on Linux), then:

```bash
make tools && make setup   # pinned tools, dependencies from lockfiles, git hooks
make check                 # everything CI runs; must pass before you open a pull request
```

`make up` and `make e2e` run the stack and the browser tests. With `AGENTFORGE_MODEL_PROVIDER=fake`
you need no model.

## What a good change looks like

- **Tests with the change.** Integration tests run on real Postgres (testcontainers); browser
  tests run with the fake model. A bug fix comes with a test that failed before the fix.
- **Boundaries hold.** `make lint` enforces the module layers (import-linter): domain code does
  not import FastAPI or the API layer.
- **Types are strict.** pyright runs in strict mode on the `agentforge` package, and `tsc` on the
  web app.
- **Security baseline.** New routes require authentication (a test walks every route), new
  tables have forced RLS (schema lint), and new uploads go through the parser limits. See
  [docs/security/r1-baseline.md](docs/security/r1-baseline.md).
- **Dependencies:** only from lockfiles, licenses on the allowlist (`make licenses`), and old
  enough for the `exclude-newer` age gate in `pyproject.toml`.
- **Decisions:** a change to an architectural decision needs a new ADR in `docs/adr/`.
- **Numbers:** label every number as *Measured* (with the raw data and the machine) or
  *Estimate*. Do not claim performance without a report.

## Developer Certificate of Origin (DCO)

Every commit must be signed off. This certifies that you wrote the change, or otherwise have the
right to submit it under the project's license (Apache-2.0), as stated in the
[Developer Certificate of Origin 1.1](https://developercertificate.org/):

```bash
git commit -s -m "Explain what and why"
```

This adds a line with your name and email:

```text
Signed-off-by: Your Name <you@example.com>
```

You can use the GitHub `noreply` address to keep your email private. Pull requests whose commits
are not signed off cannot be merged. To sign off earlier commits, run
`git rebase --signoff main`.

## Pull requests

- One topic per pull request, with a description of what changed and why, and how you tested it.
- CI must be green: lint, types, tests, documentation diagrams, secret scan, license gate,
  dependency and image scans, and browser tests.
- User-facing text is plain English, short, and says what to do next.
