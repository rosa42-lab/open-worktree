# oh-my-openagent `/goal` 快速上手（配 orch worktree）

面向 **OpenCode Ultimate + oh-my-openagent 4.19+**。  
本机斜杠命令是 **`/goal`**，不是 `/ulw-loop`（旧文档名 / Codex Light CLI），也不是第三方 `opencode-plugin-loop` 的 `/loop`。

相关文件：

| 文件 | 内容 |
|---|---|
| [prompts/worktree-goal-quality-dev.md](prompts/worktree-goal-quality-dev.md) | Conductor 粘贴 Prompt（高质量开发） |
| [omo-goal-models.example.json](omo-goal-models.example.json) | `oh-my-openagent.json` 模型 + goal 示例（无密钥） |

---

## 1. 安装与健康

- [ ] OpenCode Desktop / CLI 可用
- [ ] `~/.config/opencode/opencode.jsonc` 的 `plugin` 含 `"oh-my-openagent@…"`（**字符串**，不要 `{package,options}` 对象）
- [ ] `oh-my-openagent.json` 中 `"goal": { "enabled": true, ... }`
- [ ] 改配置后**完全退出并重启** OpenCode
- [ ] `omo doctor` / `oh-my-openagent doctor` 无阻塞性错误

---

## 2. 日常开一场

1. Desktop 打开 **coord worktree**（不要开 orch 的 `main/`）
2. 主 Agent 选 **Sisyphus**
3. 粘贴 [worktree-goal-quality-dev](prompts/worktree-goal-quality-dev.md) 并填好占位符
4. 挂 **`/goal …`**（唯一续跑入口）

---

## 3. `/goal` 命令

| 命令 | 作用 |
|---|---|
| `/goal <目标>` | 设定目标，空闲自动续跑 |
| `/goal` | 查看当前目标 |
| `/goal pause` / `resume` / `clear` | 暂停 / 恢复 / 清除 |
| `/stop-continuation` | 停掉 goal / ralph 残留 / todo 续跑 |

完成：Agent `update_goal({ status: "complete" })`，或用户 `/goal clear`。

### 最小示例

```text
/goal 完成 XXX：列出可观察验收标准；未全部满足不得标记 complete。
```

### 配 orch 示例

```text
/goal 按 Conductor 规则，用 orch 多 worktree 高质量交付用户目标与全部可观察验收标准；未满足不得 complete；禁止 main/ 开发。
```

---

## 4. 与 orch 分工

| 层 | 负责 |
|---|---|
| `/goal`（仅 coord Desktop session） | 空闲续跑、逼验收 |
| `orch` | worktree、`agent-start`、enqueue、merge、retry |
| worker（runtime `:4096`，常 `--pure`） | 单次交付；**没有** `/goal` |

`orch --version` ≥ 1.3.0；`orch runtime start --port 4096`。

---

## 5. 模型建议（示例）

见 [omo-goal-models.example.json](omo-goal-models.example.json)。推荐：

- **编排 / Sisyphus**：主模型（如 `minimax/MiniMax-M3` 或工作机同款）
- **fallback**：便宜快模型（如本地 LiteLLM / flash）防 429/5xx
- **Oracle 审查**：可与 Sisyphus 同模型，或换更强推理模型
- `runtime_fallback.enabled: true` 提高长 Goal 稳定性

**不要**把 API Key 提交进本仓库；密钥只放本机 `opencode.jsonc` / 环境变量。

---

## 6. 常见坑

| 现象 | 处理 |
|---|---|
| `/ulw` 无匹配命令 | 用 `/goal` |
| `/goal` 不续跑 | `goal.enabled: true` + 重启 Desktop |
| 只有 `/loop` | 第三方插件；编排请用 `/goal` |
| plugin 配置导致 serve 失败 | plugin 必须是字符串数组 |
| 关 Desktop / 换 session | Goal 不会跨 session 复活 |

---

## 7. 30 秒自检

1. 重启 → `/goal` 能选中  
2. `/goal 写一个 hello.txt 到当前目录并说明路径`  
3. 确认停回合后会续跑  
4. `/goal clear` 或 `/stop-continuation`
