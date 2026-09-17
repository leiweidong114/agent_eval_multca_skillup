# 2026-09-16 全系统验收报告

## 结论

评测系统的核心链路可用，但不能认定“所有 Agent 均能稳定完成所有 Skill”。当前结论如下：

- 六个 Agent 的真实 Prompt 调用、LiteLLM 成功请求及数据库精确模型归因：**6/6 通过**。
- 完整智能路灯原理图生成：**5/6 通过**。Claude、CodeBuddy、Codex、JustDo、OpenCode 通过；OpenClaw 未完成产物。
- 前端登录、核心路由、结果详情、原理图会话和交互详情：**通过**，浏览器未捕获页面错误。
- 前端使用的统一后端 API 已实际提交并轮询一项 OpenCode + `example-marker` 任务（job `12235641cb1a49b3aeb33e16741984a6`）：任务、产物、Skill 轨迹和精确模型验证均通过，总分 88。前端与 CLI 最终进入同一个运行器/适配器，不存在另一套 Agent 执行逻辑。
- 后端测试：**229 项通过**；前端生产构建：**通过**；自动布局合理性接口测试：**3 项通过**。
- LLM Judge 最小闭环：**通过**。OpenCode 运行 `example-marker` 后由已配置的 `oc/nemotron-3.5-lightning` Judge 完成三维评分，中文理由、风险、token 用量和 `judge_interaction_id` 均已落库/落报告。
- 评测执行和评分已统一走 Evaluation Plugin v2。原理图使用 `schematic-default`；其他 Skill 默认使用新增的 `skill-default`；内置 generic 仅作为插件不存在时的兼容回退。
- 覆盖式 Skill × Agent 实测修复无效 YAML 后为 **5/9 严格通过**。其余失败保留为真实能力或轨迹问题，没有放宽评分规则。

## 测试环境

- 项目：`D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup`
- Agent：Claude、CodeBuddy、Codex、JustDo、OpenClaw、OpenCode
- 完整原理图/Skill 矩阵模型：`oc/muse-spark-1.3-contributor`
- 连通性批次模型：`deepseek-v4-flash-0731-free-3`
- `glm-4.5-air` 本轮未作为验收模型：其上游曾返回 429，这不是本地 Agent 适配失败。
- 服务：前端 5173、后端 8000、自动布局 8631。

## 六 Agent 验收

### 最小 Prompt

六个 Agent 均返回成功，数据库中均找到当前 run-scoped key 的成功模型请求，且实际模型匹配指定模型。该测试验证 CLI/统一适配层、LiteLLM、trace key、PostgreSQL 归因链路。

### 完整原理图生成

| Agent | 状态 | 总分 | 判断 |
| --- | --- | ---: | --- |
| CodeBuddy | 通过 | 98.68 | 产物、Skill、模型和 Judge 均通过 |
| Codex | 通过 | 96.61 | 产物、Skill、模型和 Judge 均通过 |
| OpenCode | 通过 | 95.83 | 产物、Skill、模型和 Judge 均通过 |
| Claude | 通过 | 93.30 | 产物、Skill、模型和 Judge 均通过 |
| JustDo | 通过 | 92.58 | 产物、Skill、模型和 Judge 均通过 |
| OpenClaw | 失败 | 69.51（首次批次）/ 55.00（修复后重试） | 模型请求和 Skill 读取成功，但 Agent 未继续生成产物 |

OpenClaw 修复后重试有 4/4 次成功模型调用，四个原理图 Skill 均被读取，模型身份验证成功；它仍只回复“门禁通过，请下发具体任务”，尽管具体任务就在同一条 Prompt 后半段。缺少 `sheets.json`、布局 JSON、apply 结果、URL 和 subagent 产物。因此该失败归类为 `agent_output_invalid`，不是 LiteLLM、数据库、Skill 注入或评分器故障。

## Skill × Agent 覆盖结果

覆盖模式轮转九个非流水线 Skill，并覆盖六个 Agent。原始结果 4/9；`schematic-rationality-analysis-writer` 的旧 YAML schema 修复并重跑后，校正结果为 **5/9**。

| Skill / Agent | 结果 | 分析 |
| --- | --- | --- |
| `schematic-web-apply` / OpenCode | 通过，100 | 真实产物结构满足插件规则 |
| `signal-interface-generation` / Claude | 通过，100 | 真实信号接口产物满足规则 |
| `webapp-testing` / CodeBuddy | 通过，94 | 测试产物与执行轨迹有效 |
| `xlsx` / Codex | 通过，95.5 | 工作簿产物有效，但耗时约 15 分钟 |
| `schematic-rationality-analysis-writer` / OpenClaw | 修复后通过，92.5 | 真实写入 MongoDB 并返回 id/uuid/sessionId |
| `docx` / CodeBuddy | 严格失败，87 | 文档产物通过，但没有可归因的显式 Skill 执行轨迹 |
| `example-marker` / Codex | 失败，63 | 输出文本存在，缺少要求的验证产物 |
| `schematic-layout-codegen` / JustDo | 失败，60 | 没有完成必需布局产物/结构 |
| `api-test-suite-builder` / Claude | 超时，53.5 | 未在 900 秒内完成必需用例和产物 |

这些差异符合评测目的：同一个统一运行和评分框架能暴露不同 Agent 对 Skill、工具和产物契约的遵从能力，而不是只检查它是否说“已完成”。

## 插件化确认

运行器先解析 `EvaluationPlugin`，并要求返回 `agent-eval-evaluator-v2` 的 `PluginEvaluation`：

- `schematic-default`：原理图结果规则、轨迹规则和 Judge Prompt。
- `skill-default`：普通 Skill 的通用规则，并按 Skill 名分派 artifact-aware 验收（ZIP、DOCX、XLSX、JSON、脚本、URL/数据库写入证据等）。
- `generic`：只作为缺少文件系统插件时的兼容回退，不再是默认 Skill 评分路径。

因此“评测执行/评分”已经插件化；YAML 仍负责定义输入用例和基础断言，插件负责跨用例的规则、轨迹、Judge 和最终分数。当前是“一个 `skill-default` 插件内含多个 Skill 合约”，不是每个 Skill 一个独立文件夹。若某 Skill 的评分逻辑复杂，应再拆成独立 evaluator plugin。

额外 Judge 验收任务 `50c32469fb854756b9e39c1b2edb60d2` 的规则验收为 100，Judge 对结果/过程分别给 100，对示例 Skill 文档质量给 40，总分 88。Judge 明确指出示例 Skill 缺少工作流、约束、输出合约、错误处理和验证描述；这与确定性结构检查一致，说明 Judge 没有无条件给高分。

## 本轮修复与测试脚本优化

- 新增 `skill-default` 插件，阻止仅凭最终回复获得高分，检查实际产物和结构。
- 修复合理性写入 Skill 的 YAML schema，并新增全量 YAML schema 回归测试。
- OpenClaw 子 Agent 指令使用本地 loopback 占位凭据；真实 trace key 仍只保存在代理侧。子进程已产生成功模型调用，不再报缺少凭据。
- CLI 失败结果现在保留 Agent stdout/stderr，便于区分 429、超时、协议和 Agent 自身错误。
- 新增可恢复的 Skill × Agent 矩阵脚本；原理图脚本支持全部 Agent、并发、结构化输出和 Judge 开关。
- 系统验证脚本增加服务预检、分阶段进度、结构化汇总和严格模型/插件验收。
- 浏览器脚本改为真实登录，检查真实结果、交互详情及 Input/Output 结构。
- 自动布局服务在显式 MongoDB URI 存在时不再被 Nacos 临时不可达阻断。

## 尚未达到的目标与建议

1. **完整原理图不是 6/6。** OpenClaw 当前组合的指令遵从不足。建议换更强模型复测，或把“门禁”和“执行”设计成多轮用例；不能通过放宽评分解决。
2. **OpenClaw subagent 探针未完全收口。** 子 Agent 已实际运行且同一 trace key 下有成功父/子模型请求，但父 Agent 在 300 秒内未输出完成标记。应优化父任务 Prompt/超时，而不是伪造 marker。
3. **不同 Agent 的 Skill 轨迹可观测性不一致。** CodeBuddy 的 DOCX 产物有效但没有显式 Skill read/invoke 证据；需要适配器提供稳定的 Skill 事件。
4. **Office/API Skill 很慢。** XSLX 和 Claude API 测试接近或超过 15 分钟，应按 Skill 配置独立超时，并在矩阵中保留断点续跑。
5. **CLI 的 `run --skill` 目前偏向文件路径。** Web API 能用 Skill 名；CLI 名称与路径行为应统一。
6. **前端主包偏大。** 构建成功但主 chunk 约 1.27 MB，可对结果详情、图表和编辑器做按路由拆包。
7. **不能据一次模型快照声称“全部 LiteLLM 模型均适配”。** 当前 catalog 的 57 个可见模型中 10 个通过纯文本探针；工具协议和六 Agent 兼容仍需逐模型矩阵验证。

## 证据位置

- 六 Agent 连通性：`.runtime/verification/20260916-175512-connectivity/summary.json`
- 六 Agent 原理图：`.runtime/verification/20260916-schematic-live/summary.json`
- OpenClaw 原理图重试：`.runtime/verification/20260916-openclaw-schematic-retest/summary.json`
- Skill 矩阵：`.runtime/verification/20260916-skill-agent-matrix/summary.json`
- 合理性写入重试：`.runtime/verification/20260916-rationality-openclaw-retest/summary.json`
- 浏览器验收：`.runtime/verification/browser/summary.json`
- Judge 闭环：`backend/evaluation_results/local/judge-plugin-acceptance/20260916-203041__50c32469fb854756b9e39c1b2edb60d2/evaluation-report.json`

`.runtime` 属于本机运行证据，默认不提交 Git；本报告和测试脚本可提交。
