# 崩溃与并发演练清单（T-0702）

设计锚点：§5.9、§11.1、§17.1、§17.6–§17.8、§17.15。

## 自动化等价（已实现）

下列场景已有可重复自动化，结果见 `docs/acceptance-results.md` 与 `python -m unittest discover -s tests -q`。

| 演练 | 自动化 | 测试名 |
|------|--------|--------|
| 双进程 merge 串行 | 是 | `test_two_processes_merge_once_serial_order` |
| Claim 后、Do 前 | 是 | `test_merging_with_clean_main_resets_to_pending` |
| Do 中（MERGE_HEAD） | 是 | `test_reset_stuck_with_merge_head` |
| Finalize 前（Git 已合、DB merging） | 是 | `test_reset_stuck_marks_merged_when_develop_has_source` |
| KeyboardInterrupt / 码 130 | 是（mock Do） | `test_keyboard_interrupt_during_merge_returns_130` |

**自动化实录日期**：2026-07-25 · **43 tests OK**（Windows）。

---

## 手工演练（可选加强，真实 SIGKILL / Ctrl+C）

> 下列步骤用于操作系统级杀进程。完成后在「结果」栏勾选并填日期。

### 公共准备

```powershell
cd E:\open-worktree
$env:PYTHONPATH = (Get-Location).Path
# 准备独立演练目录
$drill = "$env:TEMP\orch-drill"
Remove-Item -Recurse -Force $drill -ErrorAction SilentlyContinue
New-Item -ItemType Directory $drill | Out-Null
# 创建带 develop 的 bare（或复用测试 fixture 思路）
# ... 建立 $drill\proj\.bare.git + develop 后：
python -m orch project add drill $drill\proj
python -m orch drill init
python -m orch drill worktree-add agentA feat/drill
# 在 worktree 提交后 enqueue
```

查询状态：

```powershell
python -m orch drill list --json
python -m orch drill lock-status --json
```

### D1 — 双 shell 并发 `merge --once`

| 项 | 内容 |
|----|------|
| 步骤 | 两任务入队后，两窗口同时 `python -m orch drill merge --once --json` |
| 期望 | 各处理 1 个任务；顺序 `(priority, submitted_at, queue_seq)`；均 ok |
| 自动化 | 已覆盖 |
| 手工结果 | [x] 以自动化为准（2026-07-25）/ [ ] 另做手工：____ |

### D2 — Claim 后、Do 前杀进程

| 项 | 内容 |
|----|------|
| 步骤 | 将 DB 中任务置 `merging` 且 `target_commit_at_claim=develop`，`main/` 干净；`reset-stuck` |
| 期望 | → `pending`，审计 `reset_stuck{recovered_as:pending}` |
| 自动化 | 已覆盖 |
| 手工结果 | [x] 以自动化为准 / [ ] 手工：____ |

### D3 — Do 中杀进程（真实 SIGKILL，可选）

| 项 | 内容 |
|----|------|
| 步骤 | 制造慢合并或断点；在 `git merge` 进行中结束 orch 进程（任务管理器 End Task / `Stop-Process -Force`） |
| 恢复 | `python -m orch drill reset-stuck --json` |
| 期望 | 有 `MERGE_HEAD` 则 abort 后 `conflict`/`pending`；否则按 HEAD 证据 |
| 自动化 | 协议级（MERGE_HEAD）已覆盖；OS 杀进程可选 |
| 手工结果 | [ ] 日期：____ 结果：____ |

### D4 — Finalize 前杀进程

| 项 | 内容 |
|----|------|
| 步骤 | merge 已成功写入 develop，DB 仍为 `merging`；`reset-stuck` |
| 期望 | → `merged`，写入 `merged_commit` |
| 自动化 | 已覆盖 |
| 手工结果 | [x] 以自动化为准 / [ ] 手工：____ |

### D5 — SIGINT / Ctrl+C

| 项 | 内容 |
|----|------|
| 步骤 | `merge` 运行中 Ctrl+C |
| 期望 | 退出码 130；锁释放；任务 `pending`/`merged`/`recovery_required` 可解释 |
| 自动化 | mock KeyboardInterrupt 已覆盖 |
| 手工结果 | [ ] 日期：____ 结果：____ |

---

## 签字

| 角色 | 姓名 | 日期 | 备注 |
|------|------|------|------|
| 执行人 | | | |
| 复核 | | | 可选 |

**说明**：设计 §19 要求 ready 前跑通第 17 章。自动化已覆盖可重复 GWT；D3/D5 真实 OS 信号为增强项，不勾选不阻塞「实现候选」标记，但若宣称生产 ready 建议至少各做一次。
