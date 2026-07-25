# v1.1 Ready 门禁（T-0703 / §19）

未全部勾选前，仓库与文档须保持 **实现候选**，禁止「已测试 / 已验证 / 可交付」等定论措辞。

## A. 实现完整性

- [x] CLI 命令面与设计 §7 / Skill CLI 表一致
- [x] 仅标准库；`develop` 硬编码；无 `--target`
- [x] 项目锁 / 配置锁 / JSON 信封 / 退出码（含 130）
- [x] merge 三段协议 + retry / skip / reset-stuck / cleanup
- [x] Skill：`skills/orchestrator/SKILL.md` ≡ 设计 §16.2
- [x] 安装与边界：`README.md`（§17.20）

## B. 自动化验收

- [x] `python -m unittest discover -s tests -q` 全绿
- [x] §17.1–§17.20 映射记录于 `docs/acceptance-results.md`
- [x] Skill 一致性测试通过

**最近全绿**：2026-07-25 · 43 tests

## C. 崩溃演练

- [x] 演练文档：`docs/crash-drills.md`
- [x] 双进程 merge / Claim 后 / Do 中(协议) / Finalize 前 / SIGINT(mock) 有自动化证据
- [ ] （可选）真实 SIGKILL during Do 手工一次
- [ ] （可选）真实 Ctrl+C 手工一次

## D. 发布前人工确认

- [ ] 操作者已阅读 `docs/acceptance-results.md` 与 `docs/crash-drills.md`
- [ ] 同意将版本从「实现候选」改为「v1.1 ready」
- [ ] 同步更新：设计文档标题/§19、`README` 状态行、`orch.__version__`

| 确认人 | 日期 | 签名/备注 |
|--------|------|-----------|
| | | |

## 当前状态（维护者）

| 字段 | 值 |
|------|-----|
| 版本字符串 | `1.1.0-candidate`（`orch/__init__.py`） |
| ready | **否**（待 D 节人工确认） |
| 阻塞项 | 仅 D 节签字；C 可选手工不阻塞候选版 |
