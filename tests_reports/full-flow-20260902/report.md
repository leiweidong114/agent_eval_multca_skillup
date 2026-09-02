# 六 Agent 全流程评测报告

| 字段 | 值 |
|---|---|
| 日期 | 2026-09-02 |
| 测试提交 | `a5ac302` |
| 前端地址 | `http://127.0.0.1:5174` |
| 后端地址 | `http://127.0.0.1:8000` |
| 指定 Profile | `litellm_opencode_go_minimax_2_7` |
| 指定模型 | `opencode-go/minimax-m2.7` |
| 范围 | CLI、API、前端、6 Agent、Skill、轨迹、评分、报告、LLM Judge |

## 结论

代码、CLI、API、前端和评测编排链路均可运行，但当前环境还不能判定为“六个 Agent 完全可用”。六个 Agent 的本地可执行文件均被发现且探测成功；真正调用指定 LiteLLM 模型时，网关对推理请求返回 HTTP 429，对运行级虚拟 trace key 创建返回 HTTP 403。因此严格评测为 0/6 成功，Judge 也因同一网关 429 而不可用。

系统对上述外部故障采取失败关闭：没有伪造成功、token、工具调用或 Judge 分数。失败行不再进入排名。

## 基础验证

- 后端：`68 passed`。
- 前端：Vite production build 成功；只有 bundle 大小警告。
- Multica：Go 测试通过。
- Skill-Up：构建通过。
- API：health、agents、models、model-config、database health、skills、runs、batches、capacity、Prism 均返回正常响应。
- 前端：主页、新建评测、Skill 表单、模型与 Agent、结果列表、批次详情均完成真实浏览器加载，未发现运行时脚本错误。
- 运行时目录：26 个支持的 Agent 中发现 6 个；这 6 个均显示可用。LiteLLM 目录同步到 35/36 个可用模型。
- LLM Judge：前端和 `/api/model-config` 均显示 `opencode-go/minimax-m2.7`。
- 新建评测：运行模型默认值也已严格对齐为 `opencode-go/minimax-m2.7`，不会误选同名无前缀模型。

## 六 Agent 可执行文件探测

| Agent | 本地探测 | 可执行文件 |
|---|---:|---|
| Claude | 通过 | `claude.EXE` |
| CodeBuddy | 通过 | `codebuddy.CMD` |
| Codex | 通过 | `codex.CMD` |
| JustDo | 通过 | `JustDo-agent.exe` |
| OpenClaw | 通过 | `openclaw.CMD` |
| OpenCode | 通过 | `opencode.CMD` |

“本地探测通过”只证明 CLI 可启动，不等于指定远程模型推理成功。

## 严格模型归因测试

- 批次：`batch-05bddbb139e0406a985ee845d4961714`
- 结果：0/6。
- 失败阶段：进度 15%，模型调用前。
- 统一错误：`Exact LiteLLM trace-key creation failed: LiteLLM trace-key endpoint returned HTTP 403`。

这说明现有 LiteLLM Master Key 没有创建运行级虚拟 key 的权限，无法满足“每个运行精确绑定并核验指定模型”的严格要求。

## 兼容模式完整执行测试

- 批次：`batch-e97f5fea530e4aecbe5cfdfb7dae6b9a`
- Skill：`example-marker`
- 要求输出：`MULTICA_SKILL_UP_OK`
- 结果：6 个任务均走完编排和落盘，0/6 推理成功。

| Agent | 运行 ID | 状态 | 分数 | 故障分类 |
|---|---|---:|---:|---|
| Claude | `43655213631a472f9725a4b43253e8d9` | 失败 | 27.25 | `gateway_rate_limited` |
| CodeBuddy | `84ddebe82cb34ca699525787eccd4f10` | 失败 | 27.25 | `gateway_unavailable` |
| Codex | `0ceb887eed8f4c72abc11cc8e2e5396f` | 失败 | 27.25 | `gateway_rate_limited` |
| JustDo | `ec3c4313fe7a4ff388a828090ae6a58a` | 失败 | 27.25 | `agent_execution_failed` |
| OpenClaw | `8d1d736395194370b03f827927097820` | 失败 | 27.25 | `gateway_unavailable` |
| OpenCode | `cbc439ffc20549ffa35c2bb3e15b98e8` | 失败 | 24.44 | `gateway_quota_exhausted` |

六项均为 0 token、0 工具调用、Agent contract 未满足。数据库轨迹查询状态为 `matched`，但精确模型核验均为 `unverified`，不能把模型目录中的“可用”标签当作一次真实推理成功。

## LLM Judge 与评分正确性

- Judge 已启用，配置为 `opencode-go/minimax-m2.7`。
- 六项 Judge 状态均为 `unavailable`。
- Judge 在 4 次尝试后仍收到 HTTP 429，因此没有生成 LLM Judge 分数。
- 当前显示的 24.44/27.25 是失败任务的过程/Skill 等诊断性分数，不是有效结果质量分，也不会再进入排名。
- 批次详情现显示“所有组合均失败，无有效排名”，避免把失败任务误报为第一名。

## 前端证据

- `screenshots/runtime-catalog.png`：Agent、模型、数据库和 Judge 运行目录。
- `screenshots/new-evaluation.png`、`screenshots/skill-evaluation-form.png`：新建评测和 Skill 配置。
- `screenshots/batch-results.png`：批次列表。
- `screenshots/batch-detail-fixed.png`：六 Agent 结果及“无有效排名”修复。

## 恢复完全可用所需条件

1. 为 LiteLLM 网关补充 `opencode-go/minimax-m2.7` 的可用额度/上游并消除 HTTP 429。
2. 为当前 Master Key 开通虚拟 key / trace key 创建权限，消除 HTTP 403。
3. 以严格模型核验重新执行同一六 Agent 批次；验收标准应为 6/6 completed、非零 token、模型核验通过、Judge available，并逐项核对工具调用和 subagent 轨迹。
