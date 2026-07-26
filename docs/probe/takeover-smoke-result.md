# Takeover 真实冒烟结果（2026-07-26）

**Server：** external `http://127.0.0.1:14196`（OpenCode 1.18.5，无密码）  
**Worktree：** `E:\orch-h2-probe\worktree-a`  
**Project：** `takeover-smoke`  
**Run：** `run_22d20b30fece424bad1a9e6ce392b721`  
**Session：** `ses_06162c3ebffe2TK8zP3Tkevg9z`

| 步骤 | 结果 |
|---|---|
| `runtime start --base-url` | registered_external / healthy |
| `agent-start` | `state=running`，`finalized=true`，heartbeat 到达 |
| `agent-takeover` | `human_controlled`，generation 1→2，`writable_attach=true` |
| `agent-release`（无 `--resume`） | `exited`，controller=`none`，generation→3 |

**结论：** Direct takeover 主路径在真实 OpenCode Server 上通过。  
原始 JSONL：`docs/probe/takeover-smoke.jsonl`
