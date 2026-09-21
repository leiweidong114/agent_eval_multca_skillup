# 六 Agent 原理图生成与 Judge 实测（2026-09-21）

## 测试范围

- 任务：智能路灯控制器简易原理图，完整执行四个原理图 Skill、生成信号接口列表、布局和可访问网页。
- 用例：`backend/skills/schematic-pipeline/evals/cases/smart-street-light-e2e.yaml`。
- 六个本机 Agent：Codex、Claude Code、OpenCode、OpenClaw、CodeBuddy、JustDo。
- 模型：经 LiteLLM 实际 `HI` 请求验证的 `oc/mimo-v2.5`；所有运行启用运行级数据库模型核验。
- Judge：默认模型改为 `oc/mimo-v2.5`，不关闭模型推理或 Judge。
- 首轮参数：每 Agent 1 次迭代、30 turns、1800 秒超时、最多 2 个 Agent 并发、Skill/无 Skill 基准对照、启用 Judge。
- 产物根目录：`D:\ae-runs\local\six-agent-street-light-20260921`。

## 模型与依赖前置检查

LiteLLM 模型目录可访问，57 个部署中 8 个模型在当时的短请求连通性探测中成功；此结果只证明文本推理，不证明工具调用协议。`glm-4.7` 与 `glm-4.5-air` 当时返回 HTTP 429（余额不足或无可用资源包），因此没有选它们执行完整任务。`oc/mimo-v2.5` 通过了 Codex 的真实 Agent 请求和数据库精确运行 Key 核验。PostgreSQL 连接正常，原理图服务健康检查正常。

## 首轮真实交付结果

| Agent | 真实模型调用 | 原理图交付 | 主要观察 |
| --- | ---: | --- | --- |
| Codex | 57 次成功 | 成功 | 四个 Skill、产物、布局、URL 验收；生成的 URL 返回 HTTP 200。 |
| Claude Code | 28 次成功 | 未完成 | 读取了四个 Skill，两个 Subagent 完成，但没有完成布局和网页应用。 |
| OpenCode | 48 次成功 | 成功 | 四阶段产物和可访问 URL；三个 Subagent 完成。 |
| OpenClaw | 3 次成功 | 未完成 | 无原理图产物；已收到完整电路要求，却要求用户再次提供说明。 |
| CodeBuddy | 33 次成功 | 未完成 | 生成了 catalog 与信号接口列表，但在 30 turns 上限前未完成布局和网页应用。 |
| JustDo | 59 次成功 | 成功 | 四阶段产物、三个 Subagent、可访问 URL；布局前两次切片检查失败，第三次修复成功。 |

这些模型调用数是数据库中对应运行 Key 的成功调用数，不是结果评分。首轮按真实产物和修正后的评测规则复核，交付为 **3/6**；一次完整任务只代表单轮结果，不能据此宣称六个 Agent 均稳定。

三个交付网页再次独立以 HTTP HEAD 验证，均返回 200：

- Codex：`http://127.0.0.1:8631/static_schematic/92975f009423/shell.html`
- OpenCode：`http://127.0.0.1:8631/static_schematic/2a4587fd2cdc/shell.html`
- JustDo：`http://127.0.0.1:8631/static_schematic/0d48a061b3ee/shell.html`

## 发现并修复的评测平台问题

1. 无 Skill 基线按设计应失败，但原评测器把它的 `FAIL` 计入正式交付的 `cases_passed`，导致 Codex、OpenCode、JustDo 的有效产物都被显示为失败。现仅将 `with_skill` 的结果用于交付判定，保留无 Skill 基线作为对照。
2. JustDo 的工具调用 ID 在本地轨迹中形如 `call_abc...`，LiteLLM 的工具结果中形如 `callabc...`。原评测器无法关联，误报全部脚本执行失败。现只对这类十六进制 ID 做精确归一匹配。
3. 原评测器把 `update_plan` 中提到的脚本名称也当成执行。现只统计 shell 工具 `command`/`cmd` 中真实执行的 Python 脚本，并区分失败后重试。JustDo 复核得到：抓目录 1/2，校验 1/1，Markdown 1/1，代码基线 1/1，布局 1/3，应用 1/1。CodeBuddy 的 shell 工具名是 `PowerShell`，现同样纳入脚本调用识别。

修复后用保存的原始运行证据离线重新评分：Codex、OpenCode、JustDo 均为交付验收 100 分、无失败检查；Claude、OpenClaw、CodeBuddy 仍未通过。**首轮磁盘上的原始报告不会被悄悄篡改**；在线第二轮评测用于产生应用了新规则的正式报告。

## Judge LLM 测试

旧默认 `oc/nemotron-3.5-lightning` 虽最终能通过两阶段自检，但一次耗时 413 秒，包含多次超时重试，不适合当前默认。改用 `oc/mimo-v2.5` 后，两次异步两阶段自检约 35.9、60.1 秒，两次直接两阶段自检约 172.1、30.8 秒，任务分类与会话指标两阶段均返回有效 JSON。Judge 又对 Codex 和 JustDo 的真实原理图评测证据分别完成了一次任务评判，约 60 和 50 秒。可用性已证明；在并发负载下仍有明显时延波动，不能承诺每次固定响应时间。

## 第二轮在线重复评测

使用同一 YAML、同一模型和相同的 30 turns / 1800 秒限制重新运行六个 Agent。第二轮产物目录：`D:\ae-runs\local\six-agent-street-light-repeat-caseonly-20260921`。这次采用修正后的评测规则，正式批次结果是 **1/6 通过**：

| Agent | 第一轮真实交付 | 第二轮正式结果 | 第二轮成功模型调用 | 第二轮关键证据 |
| --- | --- | --- | ---: | --- |
| Codex | 成功 | 失败 | 16 | 在确认器件目录后提前结束，只说“现在写 sheets.json”，没有实际写入。 |
| Claude Code | 失败 | 失败 | 50 | 完成目录、接口列表、代码基线及部分布局尝试，但进程最终报错，未调用网页应用。 |
| OpenCode | 成功 | **成功** | 57 | 四阶段产物、4 个 Subagent、URL HTTP 200；Judge 完成，综合 96.78 分。 |
| OpenClaw | 失败 | 失败 | 2 | 确认四个 Skill 已安装后要求“继续下一步指令”，未执行原 prompt 中已有的任务。 |
| CodeBuddy | 失败 | 失败 | 120 | 目录、校验、Markdown、代码基线脚本成功；布局脚本失败，未调用网页应用。 |
| JustDo | 成功 | 失败 | 54 | 4 个 Subagent 完成，但布局脚本 0/1，最终只说明需修复切片，没有真正重跑和应用。 |

第二轮六份报告均通过指定模型的运行级数据库核验。OpenCode 第二轮网页为 `http://127.0.0.1:8631/static_schematic/bcb9182ffe12/shell.html`，再次独立验证 HTTP 200。其 Judge 使用 `oc/mimo-v2.5` 成功返回中文维度理由，并指出主 Agent 在布局失败后直接修改 Subagent 代码，违反“原样落盘”的 Skill 约束；因此结果通过不等于过程完全合规。

两轮成功次数分别为：OpenCode 2/2，Codex 1/2，JustDo 1/2，Claude Code / OpenClaw / CodeBuddy 均 0/2。**当前不能证明六个 Agent 稳定完成原理图任务**。模型路由在两轮中都能成功；失败主要是 Agent 未持续执行到最后、脚本错误未被恢复，或 Agent 进程异常退出。两轮样本仍然不足以给出严格的长期成功率置信区间。

Judge 在有效交付的第二轮 OpenCode 上完成了正式评分；对没有有效交付的五项按设计跳过 Judge。这与 Judge 模型不可用不同。旧默认模型曾明显慢且多次超时；新默认模型已通过多次两阶段自检和三次真实原理图证据评分，但响应时间仍有波动。第三方模型服务无法由本项目保证绝对稳定，应持续监测超时、429 和 Judge 审计日志。
