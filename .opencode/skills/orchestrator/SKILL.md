---
name: orchestrator
description: Multi-agent worktree + OpenCode runtime orchestration CLI (orch). Use for merge queue, worktrees, runtime Server, agent lifecycle, takeover, topics, remote promotion/release, and cleanup in an orch-managed project.
---

# Orchestrator (v1.3)

Use `orch` for every managed Git write that affects the merge queue or `develop`, OpenCode Agent runtime control, and **remote branch promotion / release** on this host. Target integration branch is always `develop`. Python stdlib only.

**Version:** `1.3.0`（`docs/v1.3-ready-checklist.md` D 门已签）。

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

## Remote promotion / release (v1.3)

Fixed chain: `feature → local develop → origin/develop → develop→master PR → origin/master → release-sync → tag`.

```text
orch <project> remote-config --repository owner/name --provider github --json
orch <project> remote-probe [--no-fetch] --json
orch <project> remote-status [--no-fetch] --json
orch <project> promote-develop --verification <id> [--execute] --json
orch <project> promotion-list|show|reconcile|cancel ...
orch <project> release-create --verification <id> [--execute] [--title ...] --json
orch <project> release-status <promotion_id> --json
orch <project> release-sync <promotion_id> [--execute] --json
```

Rules:

1. Default dry-run; `--execute` writes remote.
2. CAS push only (`expected_old_sha`/`new_sha`); never `--force`.
3. Active `master_release` freezes `promote-develop --execute` and local `merge` (enqueue still allowed).
4. Platform merge ≠ released; must `release-sync` until develop tip == merge commit.
5. Credentials via env (`ORCH_GITHUB_TOKEN` or App PEM env); never in config/DB/argv.
6. Hotfix/revert use the same promotion chain; break-glass is procedural only (not automated).

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
| `remote-config\|probe\|status` | remote/provider config + read-only probe |
| `promote-develop` / `promotion-*` | develop publish (CAS FF) |
| `release-create\|status\|sync` | master Promotion PR + release-sync |

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
