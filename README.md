# orch — Multi-Agent Worktree Orchestrator (v1.1 candidate)

Global CLI for coordinating multiple agents on one machine against a shared Git bare repository. Merges into **`develop` only**, via a deterministic queue (`priority` → `submitted_at` → `queue_seq`).

**Status:** `1.1.0-candidate` — implementation + automated §17 suite green.  
**Ready gate:** see `docs/ready-checklist.md` (section D must be signed before dropping `-candidate`).

## Requirements

- Python 3.10+
- Git
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

## Skill file

- Repo copy: `skills/orchestrator/SKILL.md`
- Install path (design §16): `~/.orchestrator/skills/orchestrator/SKILL.md`

```powershell
python scripts/install_skill.py
# or:
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.orchestrator\skills\orchestrator"
Copy-Item skills\orchestrator\SKILL.md "$env:USERPROFILE\.orchestrator\skills\orchestrator\SKILL.md"
```

## Docs

| Doc | Purpose |
|-----|---------|
| `docs/acceptance-results.md` | §17 automation matrix |
| `docs/crash-drills.md` | Concurrent/crash drills (T-0702) |
| `docs/ready-checklist.md` | v1.1 ready gate (T-0703) |
| `task.md` | Implementation task list |

## Security boundary (not a sandbox)

`orch` enforces queue, locks, and state for **operations that go through orch**. A process with write access to the project tree can still bypass it (`git update-ref`, edit SQLite, delete lock files). Deploy with least privilege / separate accounts if that matters. See design §1.3 and §17.20.

## Tests

```bash
cd /path/to/open-worktree
python -m unittest discover -s tests -v
```

## Layout

| Path | Role |
|------|------|
| `orch/` | Package |
| `task.md` | Implementation task list |
| `worktree开发设计方案.md` | Design v1.1 |
| `skills/orchestrator/SKILL.md` | Agent skill |
| `~/.orchestrator/` | Registry, DBs, locks |

## License

Project-local tooling; no license file yet.
