# OpenCode 模型 × 六 Agent HI 在线矩阵摘要

- 生成时间：2026-09-03T03:37:35.504067+00:00
- 模型数：1
- Agent 数：2
- 已完成尝试：2
- 通过：0
- 失败：2
- 单条用户消息：`HI`
- 通过定义：Agent 成功 + trace alias 精确命中成功 SpendLogs + 指定模型匹配 + trace key 删除成功

| Agent | 通过/总数 | 通过率 |
|---|---:|---:|
| claude | 0/1 | 0.00% |
| codebuddy | 0/1 | 0.00% |

## 未通过组合

| Agent | 模型 | 尝试 | 状态 | 核验原因 | 错误摘要 |
|---|---|---:|---|---|---|
| claude | `opencode-go/minimax-m2.7` | 1 | failed | no_successful_model_call | claude timed out after 45s |
| codebuddy | `opencode-go/minimax-m2.7` | 1 | failed | no_successful_model_call | codebuddy timed out after 45s |
