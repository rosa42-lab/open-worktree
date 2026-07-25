# §17 验收覆盖记录

**日期**：2026-07-25  
**命令**：`python -m unittest discover -s tests -q`  
**结果**：全绿（本机 Windows）

| 验收 | 覆盖方式 | 测试 | 状态 |
|------|----------|------|------|
| 17.1 并发 merge | 双线程同时子进程 `merge --once --json` | `test_two_processes_merge_once_serial_order` | 通过 |
| 17.2 并发 enqueue | 双进程同分支 enqueue 仅一成功 | `test_double_enqueue_same_branch_one_wins` | 通过 |
| 17.3 冻结 SHA | enqueue 后新 commit，diff 仍用旧 SHA | `test_diff_uses_frozen_source_after_new_commit` | 通过 |
| 17.4 冲突阻塞 | 冲突后 merge 码 5 | `test_conflict_blocks_then_retry_and_merge` | 通过 |
| 17.5 retry 流程 | 冲突后合 develop、retry、merge | 同上 | 通过 |
| 17.6 Claim 后崩溃 | 人工置 merging + reset-stuck → pending | `test_merging_with_clean_main_resets_to_pending` | 通过 |
| 17.7 Do 中崩溃 | MERGE_HEAD + merging → reset-stuck | `test_reset_stuck_with_merge_head` | 通过 |
| 17.8 Finalize 前崩溃 | Git 已合入、DB merging → merged | `test_reset_stuck_marks_merged_when_develop_has_source` | 通过 |
| 17.9 cleanup 安全 | 安全 prune + locked 拒绝 + 他 worktree 引用拒绝 | `test_acceptance_cleanup` | 通过 |
| 17.10 命名/路径 | `../etc` 码 2 | `test_invalid_project_name` | 通过 |
| 17.11 JSON 契约 | schema_version/ok/command | `test_json_schema_fields` | 通过 |
| 17.12 main 手工解决 | conflict 不被 reset-stuck；merge 仍码 5 | `test_conflict_not_cleared_by_reset_stuck` | 通过 |
| 17.13 锁语义 | 活 PID 拒 break；死 PID break | `test_acceptance_locks` | 通过 |
| 17.14 无 --target | argparse 拒绝 | `test_no_target_flag_in_merge` | 通过 |
| 17.15 SIGINT | mock KeyboardInterrupt → 码 130 + 对账 | `test_keyboard_interrupt_during_merge_returns_130` | 通过 |
| 17.16 retry 不改 Git | 无新 commit 时码 7 | conflict 测试内 | 通过 |
| 17.17 空变更 | 码 7 | `test_empty_change_rejected` | 通过 |
| 17.18 skip | pending → skipped | `test_skip_pending` | 通过 |
| 17.19 Skill | 与 §16.2 一致 | `test_skill_matches_design_16_2` | 通过 |
| 17.20 非沙箱边界 | README 文档 | `README.md` | 文档完成 |

## 说明

- **17.1 / 17.2**：使用多线程各启真实 `python -m orch` 子进程，共享同一 `USERPROFILE` 下的 registry/DB。
- **17.7**：用真实 `git merge` 留下 `MERGE_HEAD`，再 `reset-stuck`，非 OS 级 SIGKILL。
- **17.15**：在 Do 入口 mock `KeyboardInterrupt`（验证协议与退出码 130）；未向 OS 进程发 SIGINT。
- **17.9「其他 worktree 引用分支」**：Git 默认禁止同分支双检出，除 `worktree lock` 路径外另用 mock list 注入第二条 worktree。

## ready 门禁

- 自动化：§17.1–§17.20 可重复路径已覆盖（本文件）。
- 演练说明：`docs/crash-drills.md`（T-0702）。
- 签字门禁：`docs/ready-checklist.md`（T-0703；**D 节人工确认前保持 candidate**）。
