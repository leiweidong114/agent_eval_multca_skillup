# 全 Agent 指定模型 / Skill 评测审计（2026-09-01）

## 结论

当前机器不能成功完成全部 26 个已声明 Agent 的在线评测。

- 静态配置契约：21/26 支持同时指定模型并注入 Skill。
- 本机 CLI 检测：6/26 已安装。
- 实际在线用例：3/6 通过，3/6 失败；其余 20 个因 CLI 未安装未执行。
- 精确模型归属：0/6。LiteLLM 临时 trace key 创建返回 403，只能取得模型/时间窗记录；该记录在并行运行时会串任务，不能作为精确通过证据。

本次统一使用：

- profile：`litellm_opencode_go_minimax_2_7`
- provider model：`opencode-go/minimax-m2.7`
- Skill：`backend/skills/example-marker`
- case：`backend/skills/example-marker/evals/cases/marker.yaml`
- benchmark：关闭
- LLM Judge：关闭

## 在线结果

| Agent | 用例结果 | 模型时间窗命中 | 结论 / 失败原因 |
|---|---:|---:|---|
| codex | PASS | 是 | 输出 `MULTICA_SKILL_UP_OK` |
| justdo | PASS | 是 | 输出 `MULTICA_SKILL_UP_OK` |
| opencode | PASS | 是 | 输出 `MULTICA_SKILL_UP_OK` |
| claude | ERROR | 是 | `unrecognized_model`；网关 deployment id 被 Claude SDK 路径拒绝 |
| codebuddy | ERROR | 是 | 账号返回 429，Token Plan 用量已达上限 |
| openclaw | ERROR | 是 | 本机 legacy workspace 需要执行 `openclaw doctor --fix` |

在线产物保存在开发副本的
`backend/evaluation_results/matrix-audit/specified-model-skill/`，任务 id 以 `matrix-` 开头。

## 未安装的 Agent

`antigravity`、`copilot`、`cursor`、`deveco`、`dim`、`dsh`、`grok`、`hermes`、
`kimi`、`kiro`、`mcode`、`omp`、`pi`、`qoder`、`qoderclicn`、`qwen`、`qwenpaw`、
`reasonix`、`traecli`、`zeroclaw`。

## 能力限制

- `mcode`、`qwenpaw`、`zeroclaw` 的模型由 Agent 运行时管理，不能按任务指定。
- `dim`、`hermes`、`zeroclaw` 在当前本地直连运行时中没有 Skill 注入适配器。
- 因上述集合重叠，能满足“指定模型 + 指定 Skill”静态契约的 Agent 为 21 个。

## 本次修复与验证

- 发现 API、CLI 和报告增加机器可读 `capabilities`。
- 不满足指定模型或 Skill 契约时，在排队/创建运行目录/调用模型之前失败。
- UI 标出未安装、运行时管理模型和无 Skill 适配器的 Agent。
- 时间窗模型命中改为 `matched_unattributed`；只有 run-scoped virtual key 精确关联才可令 `verified=true`。
- 后端测试：39 passed。
- 前端：Vite production build passed（仅有既存的大 chunk 警告）。
