# OpenCode 模型 × 六 Agent HI 在线矩阵摘要

- 生成时间：2026-09-03T03:50:46.817873+00:00
- 模型数：1
- Agent 数：6
- 已完成尝试：18
- 通过：13
- 失败：5
- 单条用户消息：`HI`
- 通过定义：Agent 成功 + trace alias 精确命中成功 SpendLogs + 指定模型匹配 + trace key 删除成功

| Agent | 通过/总数 | 通过率 |
|---|---:|---:|
| claude | 2/3 | 66.67% |
| codebuddy | 3/3 | 100.00% |
| codex | 2/3 | 66.67% |
| justdo | 0/3 | 0.00% |
| openclaw | 3/3 | 100.00% |
| opencode | 3/3 | 100.00% |

## 未通过组合

| Agent | 模型 | 尝试 | 状态 | 核验原因 | 错误摘要 |
|---|---|---:|---|---|---|
| claude | `glm-4.7` | 2 | failed |  | claude returned an error result without details |
| codex | `glm-4.7` | 2 | failed |  | codex timed out after 2m0s |
| justdo | `glm-4.7` | 1 | failed | no_database_interactions | openclaw --version failed: exit status 69 |
| justdo | `glm-4.7` | 2 | failed | no_database_interactions | openclaw --version failed: exit status 69 |
| justdo | `glm-4.7` | 3 | failed | no_database_interactions | openclaw --version failed: exit status 69 |
