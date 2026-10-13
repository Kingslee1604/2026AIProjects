# Security Policy

## Supported versions

AgentForge is pre-release. Only the latest commit on `main` receives fixes.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private vulnerability
reporting on this repository (Security tab → "Report a vulnerability").

Include what you found, steps to reproduce, and the impact you expect. You will get an
acknowledgement within 7 days. Fixes are coordinated with you before public disclosure.

## Scope notes

AgentForge treats every uploaded document and tool output as untrusted. Prompt injection has no
complete defense; the design relies on permissions enforced outside the model and on human
approval for sensitive actions. Reports that bypass those controls are in scope.
