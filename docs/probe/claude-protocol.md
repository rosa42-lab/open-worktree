# Claude Code protocol probe (V20-P0a)

**Date:** 2026-08-31  
**Host:** Windows, `claude` 2.1.214 (Claude Code)  
**Paid `--resume` round-trip:** **not run** (encoding-gate; flags verified from `--help`).

## Identity

| | |
|---|---|
| Binary | PATH `claude` → npm wrapper (`claude.ps1` / `claude.cmd`) |
| Not | PATH `agent` (this host: Grok 0.2.112) |
| Class | **B** (headless exit + resume id). Not Class A (no attach-equivalent HTTP). |
| Reject | `--bg` / Agent Teams as orch session; perpetual CLI shell |

## Headless

- Default entry is **interactive TUI**.
- `-p` / `--print`: print response and **exit**. This is the only orch Class B entry.
- `--output-format` only with `--print`.
- `--session-id <uuid>`: pin id for a **new** session.
- `-r` / `--resume [value]`: resume by session id (optional value = picker in TUI; orch must pass an explicit id).
- `-c` / `--continue`: most recent conversation **in cwd** — too implicit for orch; do not use as the durable ref.
- `--fork-session`: new id when used with `--resume` / `--continue`. orch should **not** fork unless inspect.

## orch argv shape (intended)

First slice:

```text
claude -p --output-format text --session-id <uuid> -- <prompt>
```

Continue:

```text
claude -p --output-format text --resume <uuid> -- <prompt>
```

Fail-closed if exit ≠ 0 or session id missing. Do not mark `running` after the process has exited.

## Env

Allowlist (tentative): `ANTHROPIC_API_KEY`, `CLAUDE_CONFIG_DIR`, locale/`TERM` as needed.  
Do **not** inherit arbitrary parent env. `NO_COLOR` / `OPENCODE_*` must not leak into Claude.

## Gate

This file is **flag-complete, resume-unproven**. Class B implementation may use a **fake binary fixture** for argv/exit tests. A live paid `--resume` round-trip remains an encoding check before production use of a real `claude` binary.
