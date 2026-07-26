---
name: orchestrator
description: Multi-agent worktree + OpenCode runtime orchestration CLI (orch). Use for merge queue, worktrees, runtime Server, agent lifecycle, takeover, topics, and cleanup in an orch-managed project.
---

# Orchestrator (v1.2 candidate)

Use `orch` for every managed Git write that affects the merge queue or `develop`, and for OpenCode Agent runtime control on this host. Target branch is always `develop`. Python stdlib only.

**Version:** keep `1.2.0-candidate` until `docs/v1.2-ready-checklist.md` release gates are signed.

## Merge queue workflow (v1.1 unchanged)

1. `orch <project> pending --json`
2. Inspect with `diff` / `changes` / `log`
3. `orch <project> worktree-add <agent> <branch>`
4. Develop and commit only in your worktree (never in `main/`)
5. `orch <project> enqueue <agent> <branch> <worktree_path> --priority <N>`
6. Coordinator: `orch <project> merge` or `merge --once`

### Conflict / recovery

- `conflict`: resolve in the **source worktree**, then `retry <task_id>` (DB only; no Git from orch).
- Abandon: `skip <task_id> --reason <text>` after `main/` is clean.
- `recovery_required`: `lock-status` + `reset-stuck`; do not treat as normal conflict.

## Runtime + Agent workflow (v1.2)

```text
orch runtime start [--port 4096 | --base-url URL] --json
orch runtime status --json
orch <project> agent-start <agent> <branch> <worktree> [--prompt ...] --json
orch <project> agent-list|show|watch ...
orch <project> agent-takeover <run_id> [--fork] [--launch] --json
orch <project> agent-release <run_id> --token <human_lease> [--resume] --json
orch <project> agent-stop|reconcile|archive ...
orch runtime stop --json   # refuses if active runs (unless --force)
```

### Takeover rules

1. Direct takeover: generation++ → worker exit → abort → session idle → **human lease** → writable attach.
2. `--fork`: inspection fork only; does **not** change owner/generation/worker.
3. Without human lease / before idle: **never** treat attach as writable.
4. `--launch` alone starts a client; omit it to print locator only.

### Topic / coordinator (product layer)

```text
orch <project> coordinator-bind --session <ses> --directory <path> [--replace] --json
orch <project> topic-start <name> --title ... --goal ... --branch ... --worktree ... --json
orch <project> topic-ready <topic_id> --commit <sha> [--command ...] --json
# topic-ready does NOT enqueue; use enqueue after verification
orch <project> topic-open|list|show|archive ...
```

## CLI reference

### Host / project registry

| Command | Lock |
|---|---|
| `orch project list\|add\|remove` | config lock on write |
| `orch runtime probe\|start\|status\|stop` | runtime lock on start/stop |
| `orch --version` | no |

### Project merge queue

| Command | Lock |
|---|---|
| `init`, `worktree-add`, `enqueue`, `merge`, `retry`, `skip`, `reset-stuck` | project lock |
| `list`, `pending`, `diff`, `changes`, `log`, `lock-status` | no |
| `cleanup [--prune]` | yes when pruning |
| `lock-break --force` | guarded break |

### Agent / topic

| Command | Notes |
|---|---|
| `agent-list\|show\|watch` | observe-only; no project lock; no control HTTP |
| `agent-register` | temp map worktree/session; no worker |
| `agent-start\|stop\|reconcile\|archive` | lifecycle owner |
| `agent-takeover\|release\|open` | single-writer lease |
| `coordinator-bind\|show` | one active coordinator per project |
| `topic-*` | product records; delete still via cleanup guards |

Use `--json` for automation. Envelope: `schema_version`, `ok`, `command`, `data`, `error`.  
`agent-watch --json` is **JSONL** (header/ticks/footer), not a single envelope.

## Layout

- Project: `.bare.git/`, merge-only `main/`, agent trees under `worktrees/`
- Host: `~/.orchestrator/` — project DBs/locks, `runtime/opencode.json`, credentials, skill

## Exit codes

`0` ok · `1` general · `2` usage · `3` unregistered · `4` merge precheck · `5` queue blocked · `6` lock · `7` validation · `8` git · `9` db · `130` interrupted

## Safety / secrets

- Never put Server password or lease token on argv; credentials file is same-user readable (not a sandbox).
- Do not kill unknown port owners; do not `runtime stop` external Servers.
- `cleanup --prune` is blocked by active/human/lost/manual/unarchived runs and skipped tasks (`runtime_blocked`).
- Do not edit SQLite/locks by hand; do not Git-write `develop` or `main/` outside orch.
- Hooks: argv arrays only, `shell=False`; no `cmd /c` / `powershell -Command`.

## Prohibited

- Git writes in `main/`
- Direct `develop` update outside orch
- Manual lock deletion
- Assuming orch is a security boundary across OS accounts
