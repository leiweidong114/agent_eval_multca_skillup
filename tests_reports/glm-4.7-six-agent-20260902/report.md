# GLM-4.7 六 Agent 全流程验收报告

- 日期：2026-09-02
- LiteLLM 对外模型：`glm-4.7`
- Judge：`litellm_glm_4_7 / glm-4.7`
- 认证批次：`batch-76b4df4c266547228a2b9cac8745399f`
- 评测内容：`example-marker` Skill，任务结果、过程轨迹、Skill 质量、精确模型 trace、LLM Judge

## 结论

六个 Agent 均已证明能够通过 LiteLLM 实际调用 GLM-4.7，并且各自至少存在一次任务完成、模型精确校验通过、GLM Judge 完成的记录。系统的任务执行、trace、规则评分、LLM Judge 和报告生成链路可用。

但不能认定“六 Agent 当前全部稳定通过”：最新认证批次为 4/6，Claude 的批次运行使用旧的 CLI 别名配置而失败，修复后单项主任务已通过；JustDo 最近两次没有遵循 Skill，虽然模型调用成功且此前曾完整通过，表现存在明显随机性。OpenClaw 的无 Skill 对照也返回了目标标记，Skill 增益证据不足。

## 每个 Agent 的成功证据

| Agent | Job ID | 状态 | 总分 | 结果 | 过程 | Skill | Judge | 模型验证 | 工具调用/结果 | Subagent | Token | 用例 |
|---|---|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---|
| Claude | `4db953a10e584461b6f10473e1cdfd66` | completed | 86.20 | 100 | 94 | 40 | completed / glm-4.7 | true | 3/3 | 0 | 7,723 | with_skill=PASS |
| CodeBuddy | `03cdbedcd6d845bba68fd1a0193086d4` | completed | 85.00 | 100 | 90 | 40 | completed / glm-4.7 | true | 6/6 | 0 | 58,558 | PASS / FAIL |
| Codex | `0970c26cedf8450c9d8a1760a0b0797e` | completed | 86.80 | 100 | 96 | 40 | completed / glm-4.7 | true | 4/4 | 0 | 275 | PASS / FAIL |
| JustDo | `3487388880c340ea94e69f59c658a488` | completed | 85.60 | 100 | 92 | 40 | completed / glm-4.7 | true | 0/0 | 0 | 121,312 | PASS / FAIL |
| OpenClaw | `e3da5c66b7e84695b5055515d00eb9c5` | completed | 84.75 | 93.5 | 100 | 40 | completed / glm-4.7 | true | 0/0 | 0 | 24,235 | PASS / PASS |
| OpenCode | `f7cc6215b1a7401caa2af5fd3e8f6f53` | completed | 88.00 | 100 | 100 | 40 | completed / glm-4.7 | true | 2/2 | 0 | 47,335 | PASS / FAIL |

`PASS / FAIL` 分别代表 with-skill 与 without-skill 对照。Subagent 为 0 是本任务未触发子 Agent，不代表采集字段缺失。

## 稳定性与失败证据

- 最新认证批次 `batch-76b4df4c266547228a2b9cac8745399f`：CodeBuddy、Codex、OpenClaw、OpenCode 完成；Claude、JustDo 失败。
- Claude 已改为在 CLI 内使用完整可识别标识 `claude-sonnet-4-6`，由本地代理仅在网络层改写到 `glm-4.7-anthropic`。修复后主任务、trace 和 Judge 全部完成。
- JustDo 在同一 GLM 配置下共观察到一次成功、随后两次任务失败。失败时 LiteLLM trace 仍为 100%，说明问题是模型没有遵循 Skill，而不是鉴权、网络或模型路由失败。
- 失败结果会被标记为 diagnostic-only，Judge 在执行失败时显示 `skipped_due_to_execution_failure`，没有把失败任务伪装成有效排名结果。

## 回归测试

- 后端：75 tests passed。
- 前端：Vite production build passed（1679 modules）。
- 模型连通性：`/api/models/test` 返回 requested=`glm-4.7`、actual=`glm-4.7`。
- 源码与运行目录均指向提交 `c9cdb78ecd7c8986f65c52fda31c0d389a79c434`，并与 `origin/dev` 一致。

## 建议

当前系统可以投入进一步评测，但如果验收标准要求“六 Agent 每次都完成任务”，JustDo 仍不合格。建议为 Agent 认证增加 3 次或 5 次重复运行，并以成功率阈值而非单次 PASS 作为可用性标准；OpenClaw 需要更有区分度的 Skill 用例，避免基线也猜中固定 marker。
