# orch — Multi-Agent Worktree Orchestrator (v1.2 candidate)

Global CLI for coordinating multiple agents on one machine against a shared Git bare repository. Merges into **`develop` only**, via a deterministic queue (`priority` → `submitted_at` → `queue_seq`). v1.2 adds OpenCode **runtime / agent lifecycle / takeover / topics**.

**Status:** `1.2.0-candidate` — Phase 0–4 + Skill/docs packaged.  
**Ready gate:** see `docs/v1.2-ready-checklist.md` (section D must be signed before dropping `-candidate`).  
**v1.1 gate (historical):** `docs/ready-checklist.md`

## Requirements

- Python 3.10+
- Git
- OpenCode CLI (for runtime / agent features)
- **No third-party Python packages** (stdlib only)

## Install (Windows PowerShell)

```powershell
# From this repo
$repo = "E:\open-worktree"   # adjust
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

# Wrapper
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.local\bin" | Out-Null
@"
@echo off
python -m orch %*
"@ | Set-Content -Encoding ASCII "$env:USERPROFILE\.local\bin\orch.cmd"

# Ensure repo is on PYTHONPATH when invoking
# Recommended: run from repo, or:
# setx PYTHONPATH "$repo"
```

Or always:

```powershell
cd E:\open-worktree
python -m orch --help
```

Preferred:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_orch.ps1
python scripts\install_skill.py
```

### Long paths

Deep worktree trees may hit `MAX_PATH` (260). Enable Windows long path support or keep project roots short.

## Install (POSIX)

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/orch <<'EOF'
#!/usr/bin/env bash
exec python3 -m orch "$@"
EOF
chmod 700 ~/.local/bin/orch
export PYTHONPATH="/path/to/open-worktree:$PYTHONPATH"
export PATH="$HOME/.local/bin:$PATH"
```

## Bootstrap a project

`orch` does **not** create the first commit or bare repo for you.

```bash
# 1) Seed develop, then bare-clone
git clone --bare /path/to/existing/source /path/to/project/.bare.git
git --git-dir=/path/to/project/.bare.git show-ref --verify refs/heads/develop

# 2) Register + init
python -m orch project add alpha /path/to/project
python -m orch alpha init

# 3) Agent worktree
python -m orch alpha worktree-add agentA feat/foo
# develop in worktrees/agentA-feat__foo, commit, then:
python -m orch alpha enqueue agentA feat/foo /path/to/project/worktrees/agentA-feat__foo
python -m orch alpha merge --once --json
```

Or:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_project.ps1 `
  -ProjectName myapp -SourceRepo D:\work\my-app -ProjectRoot D:\work\my-app-orch
```

## Runtime + Agent (v1.2)

```powershell
orch runtime probe --json
orch runtime start --base-url http://127.0.0.1:14196 --json   # or --port for managed
orch <project> agent-start <agent> <branch> <worktree> --json
orch <project> agent-list --json
orch <project> agent-takeover <run_id> --json
orch <project> agent-release <run_id> --token <human_lease> --json
orch runtime stop --json
```

Desktop: bind OpenCode Desktop to the **external** Server URL (see H2 notes in probe docs). Do not use Desktop's nested Local Server as the orch target for multi-worktree H2.

## Skill file

- Repo copy: `skills/orchestrator/SKILL.md` (v1.2 surface)
- Install: `python scripts/install_skill.py` → `~/.orchestrator/skills/...` and OpenCode/Claude discovery paths

## OpenCode multi-session

```powershell
cd E:\open-worktree
powershell -ExecutionPolicy Bypass -File scripts\install_orch.ps1
python scripts\install_skill.py
# then open a NEW terminal / OpenCode session
```

Full guide: [`docs/opencode-multi-session.md`](docs/opencode-multi-session.md)

## Docs

| Doc | Purpose |
|-----|---------|
| `docs/v1.2-upgrade-plan.md` | v1.2 design / phases |
| `docs/tasks.md` | v1.2 task tracker |
| `docs/v1.2-acceptance-results.md` | v1.2 acceptance matrix |
| `docs/v1.2-crash-drills.md` | Crash / concurrency drills |
| `docs/v1.2-ready-checklist.md` | v1.2 ready gate |
| `docs/opencode-multi-session.md` | OpenCode + orch checklist |
| `docs/prompts/bootstrap-worktree-agent.md` | Agent bootstrap prompt |
| `docs/acceptance-results.md` | v1.1 §17 automation matrix |
| `docs/ready-checklist.md` | v1.1 ready gate |
| `task.md` | v1.1 implementation task list |

## Security boundary (not a sandbox)

`orch` enforces queue, locks, and state for **operations that go through orch**. A process with write access to the project tree can still bypass it (`git update-ref`, edit SQLite, delete lock files). Credentials files are same-user readable — not a cross-account sandbox. See design §1.3 / v1.2 plan H10.

## Tests

```bash
cd /path/to/open-worktree
python -m unittest discover -s tests -v
```

## Layout

| Path | Role |
|------|------|
| `orch/` | Package |
| `orch/runtime/` | OpenCode runtime adapter / worker / takeover |
| `docs/v1.2-*.md` | v1.2 docs |
| `skills/orchestrator/SKILL.md` | Agent skill |
| `~/.orchestrator/` | Registry, DBs, locks, runtime |

## License

Project-local tooling; no license file yet.
