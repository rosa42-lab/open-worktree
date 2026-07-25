---
name: orchestrator
description: Multi-agent worktree orchestration CLI (orch). Use when inspecting pending merge tasks, reading queued code, creating managed worktrees, enqueueing completed work, resolving a blocked task, or triggering sequential merges in an orch-managed project.
---

# Orchestrator

Use `orch` for every managed Git write that affects the merge queue or `develop`. This skill applies to a single host with multiple local processes. The v1.1 target branch is always `develop`.

## Required Workflow

1. Start by running `orch <project> pending --json`.
2. Inspect relevant queued work with `diff`, `changes`, and `log` before editing related files.
3. Create your worktree with `orch <project> worktree-add <agent> <branch>`.
4. Develop and commit only in your own worktree.
5. Confirm your worktree is clean, then enqueue it with `orch <project> enqueue <agent> <branch> <worktree_path> --priority <N>`.
6. Use `orch <project> merge` or `merge --once` only when acting as the coordinator.

## Conflict Workflow

When a task becomes `conflict`, orch has already aborted the merge in `main/` and blocked the queue. In the task's source worktree, merge or rebase the current local `develop`, resolve conflicts, commit, and leave the worktree clean. Then run `orch <project> retry <task_id>`. `retry` validates the new commit and updates the queued SHA; it never edits Git state. To abandon a conflict task after `main/` is verified clean, run `orch <project> skip <task_id> --reason <text>`.

Never resolve conflicts or commit in `main/`. A `recovery_required` task is not a normal conflict; stop and run `lock-status` plus `reset-stuck`, then follow the evidence printed by orch.

## CLI Reference

| Command | Mode | Project lock |
|---|---|---|
| `orch project list [--json]` | read | no |
| `orch project add <name> <path>` | write | config lock |
| `orch project remove <name>` | write | config lock |
| `orch <project> init` | write | yes |
| `orch <project> worktree-add <agent> <branch> [--base develop]` | write | yes |
| `orch <project> enqueue <agent> <branch> <path> [--priority N]` | write | yes |
| `orch <project> list [--all] [--json]` | read | no |
| `orch <project> pending [--json]` | read | no |
| `orch <project> diff <task_id-or-branch>` | read | no |
| `orch <project> changes <task_id-or-branch>` | read | no |
| `orch <project> log <task_id-or-branch>` | read | no |
| `orch <project> merge [--once]` | write | yes |
| `orch <project> retry <task_id>` | DB write; Git validation only | yes |
| `orch <project> skip <task_id> [--reason text]` | write | yes |
| `orch <project> reset-stuck` | recovery | yes |
| `orch <project> cleanup [--prune]` | read/write | write mode: yes |
| `orch <project> lock-status [--json]` | read | no |
| `orch <project> lock-break --force` | exceptional recovery | special lock protocol |

Use `--json` whenever a command supports it and another program or Agent consumes the result. JSON responses contain `schema_version`, `ok`, `command`, `data`, and `error`. Do not parse human-readable output in automation.

## Project Layout

The project root contains `.bare.git/`, the merge-only `main/` worktree, and Agent worktrees under `worktrees/`. The user directory `~/.orchestrator/` contains the project registry, per-project SQLite databases, and lock files.

## Exit Codes

`0` success; `1` general failure; `2` usage error; `3` unregistered project; `4` merge precheck failure; `5` blocked queue; `6` lock error; `7` enqueue/retry validation failure; `8` Git or recovery failure; `9` database failure; `130` interrupted merge.

## Prohibited Actions

- Do not run `git add`, `git commit`, `git merge`, `git reset`, or `git checkout` in `main/`.
- Do not directly update, merge, or push `develop` outside orch.
- Do not edit the orchestrator SQLite database or lock files.
- Do not delete a lock file manually. Use `lock-status` and the guarded `lock-break --force` flow.
- Do not assume orch is a security sandbox. Processes with the same filesystem permissions can bypass it; obey the project permission boundary.
