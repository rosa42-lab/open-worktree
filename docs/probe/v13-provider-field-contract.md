# V13 Provider 领域字段契约

**任务：** V13-009-2  
**范围：** `HostingProviderAdapter` 返回值；禁止 raw GitHub/GitLab JSON 进入 `PromotionService`。

## 通用

| 字段 | 含义 |
|---|---|
| `kind` | 稳定错误/结果分类：`auth_failed` / `forbidden` / `not_found` / `validation` / `rate_limited` / `server_error` / `timeout` / `network` / `unsupported` / `misconfigured` / `merge_not_syncable` |
| `detail` | 已脱敏人类可读说明；不得含 token / Authorization |

## `probe_capabilities`

```text
{
  ok: bool,
  checks: [{name, status, detail?, evidence?}],
  identity: {type, login|account, id, ...},
  permissions_summary: {full_name?, permissions?},
  kind?, detail?
}
```

`status` ∈ `verified` | `unsupported` | `unknown` | `misconfigured`。权限不足不得标 `verified`。

## `branch_policy(branch)`

```text
{
  exists: bool,
  allow_force: bool|null,
  allow_delete: bool|null,
  require_pr: bool|null,
  required_checks: [str],
  bypass_summary: [{actor_id, actor_type, bypass_mode}],
  merge_methods: [merge_commit|squash|rebase],
  enforcement?,
  status: verified|unknown|misconfigured|unsupported,
  kind?, detail?
}
```

## `create_promotion_pr` / `get_pr`

```text
{
  external_id, url, head, base, head_sha, base_sha,
  state, merged, merge_commit_sha?,
  mergeable: bool|null,   # null = 尚未计算；adapter 内有界退避后再返回
  mergeable_state?,
  merge_method?,          # merge_commit | squash | rebase
  kind?, detail?
}
```

squash/rebase（或单 parent「合并」）→ `kind=merge_not_syncable`。

## `get_checks(external_id, source_sha)`

仅统计 **head_sha == source_sha** 的 check runs：

```text
{ external_id, source_sha, checks: [{name, conclusion, status, head_sha, bound_to_source}], ... }
```

## `get_reviews(external_id, source_sha)`

```text
{
  external_id, source_sha,
  reviews: [{actor, state, commit_id, bound_to_source, is_bot, counts_as_code_owner}],
  approved_bound_human_count
}
```

Bot 审批：`is_bot=true`，`counts_as_code_owner=false`（即使 APPROVED）。
