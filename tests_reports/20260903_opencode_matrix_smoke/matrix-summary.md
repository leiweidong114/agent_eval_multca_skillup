# OpenCode 模型 × 六 Agent HI 在线矩阵摘要

- 生成时间：2026-09-03T03:28:03.603926+00:00
- 模型数：1
- Agent 数：6
- 已完成尝试：6
- 通过：0
- 失败：6
- 单条用户消息：`HI`
- 通过定义：Agent 成功 + trace alias 精确命中成功 SpendLogs + 指定模型匹配 + trace key 删除成功

| Agent | 通过/总数 | 通过率 |
|---|---:|---:|
| claude | 0/1 | 0.00% |
| codebuddy | 0/1 | 0.00% |
| codex | 0/1 | 0.00% |
| justdo | 0/1 | 0.00% |
| openclaw | 0/1 | 0.00% |
| opencode | 0/1 | 0.00% |

## 未通过组合

| Agent | 模型 | 尝试 | 状态 | 核验原因 | 错误摘要 |
|---|---|---:|---|---|---|
| claude | `opencode-go/minimax-m2.7` | 1 | failed | no_successful_model_call | claude timed out after 2m0s; claude stderr: [claude-code:unrecognized_model] {"model":"sonnet","query_source":"sdk"} |
| codebuddy | `opencode-go/minimax-m2.7` | 1 | failed | no_successful_model_call | codebuddy timed out after 2m0s |
| codex | `opencode-go/minimax-m2.7` | 1 | failed | no_successful_model_call | exceeded retry limit, last status: 429 Too Many Requests |
| justdo | `opencode-go/minimax-m2.7` | 1 | failed | no_database_interactions | openclaw --version failed: exit status 69 |
| openclaw | `opencode-go/minimax-m2.7` | 1 | failed | no_database_interactions | openclaw timed out after 2m0s |
| opencode | `opencode-go/minimax-m2.7` | 1 | failed | no_successful_model_call | litellm.RateLimitError: AnthropicException - b'{"type":"error","error":{"type":"GoUsageLimitError","message":"Weekly usage limit reached. Resets in 3 days. To continue using this model now, enable usage from your available balance: https:// |
