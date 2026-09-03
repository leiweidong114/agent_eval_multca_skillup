# Agent Eval 命令行完整使用手册

本文档覆盖当前 `agent-eval` 的全部命令，重点支持：模型、Agent、Skill 和结果查看，多 Agent 同 Prompt、多 Agent 同任务评测，以及原理图生成四 Skill pipeline 一键评测。

## 1. 运行前准备

项目路径：

```text
D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup
```

如果尚未安装命令行入口，在 PowerShell 中执行：

```powershell
cd "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup"
$env:PYTHONPATH = (Resolve-Path "backend\src").Path
```

此后把下文的 `agent-eval` 替换成 `python -m agent_eval.cli` 即可。例如：

```powershell
python -m agent_eval.cli models
```

如果已经在 `backend` 目录执行过：

```powershell
python -m pip install -e ".[web,database,dev]"
```

则可以直接使用 `agent-eval`。查看总帮助：

```powershell
agent-eval --help
```

## 2. 命令总览

| 命令 | 作用 |
|---|---|
| `agent-eval doctor` | 检查运行时、模型配置和基础环境 |
| `agent-eval models` | 查看缓存的 LiteLLM 模型目录 |
| `agent-eval models --refresh` | 从 LiteLLM `/v1/models` 重新同步模型目录 |
| `agent-eval agents` | 查看支持的 Agent、本机可执行文件和能力 |
| `agent-eval skills` | 查看当前可评测的 Skill |
| `agent-eval check-agent` | 向一个 Agent 发送任意 Prompt，并核验数据库模型 |
| `agent-eval prompt` | 向多个 Agent 并发发送相同 Prompt |
| `agent-eval run` | 使用一个 Agent 评测一个 Skill |
| `agent-eval run-multi` | 使用多个 Agent 并发评测同一个 Skill/任务 |
| `agent-eval pipeline-eval` | 对原理图生成 pipeline 的四个 Skill 做组合评测 |
| `agent-eval results` | 查看保存在本机的评测结果列表 |

每个命令都支持独立帮助：

```powershell
agent-eval run-multi --help
agent-eval pipeline-eval --help
```

## 3. 查看支持的模型

先从 LiteLLM 同步最新目录：

```powershell
agent-eval models --refresh
```

之后可直接读取本地缓存：

```powershell
agent-eval models
```

按模型前缀筛选：

```powershell
agent-eval models --prefix opencode-go/
agent-eval models --prefix glm-4.7
```

目录文件位于：

```text
backend/config/litellm-models.json
```

该文件不保存 API Key。`/v1/models` 返回“目录可见”模型，不代表额度、区域和上游推理状态一定正常；实际可用性应使用 `check-agent` 或 `prompt` 验证。

## 4. 查看支持的 Agent

```powershell
agent-eval agents
```

输出包括 Agent 名称、默认命令、本机是否找到可执行文件、是否支持指定模型、是否支持 Skill 注入和协议适配信息。

当前重点验证的六个 Agent 名称为：

```text
claude
codebuddy
codex
justdo
openclaw
opencode
```

## 5. 向一个 Agent 发送任意 Prompt

```powershell
agent-eval check-agent `
  --agent codex `
  --profile litellm_glm_4_7 `
  --model glm-4.7 `
  --prompt "只回复 COMMAND_OK" `
  --timeout 120 `
  --database-verify
```

成功结果应同时满足：Agent 正常退出、返回内容、数据库 Trace Key 精确命中、数据库模型匹配指定模型以及 Trace Key 成功清理。

## 6. 向多个 Agent 同时发送相同 Prompt

重复使用 `--agent` 指定多个 Agent，`--workers` 控制同时运行数量：

```powershell
agent-eval prompt `
  --agent claude `
  --agent codebuddy `
  --agent codex `
  --agent justdo `
  --agent openclaw `
  --agent opencode `
  --profile litellm_glm_4_7 `
  --model glm-4.7 `
  --prompt "HI" `
  --workers 3 `
  --timeout 120 `
  --database-verify
```

这里的 `--workers 3` 表示最多同时运行 3 个 Agent，剩余 Agent 在当前命令内部排队。设为 `6` 可尝试六 Agent 同时启动，但应根据本机 CPU、内存和 Agent 限流情况选择。

该命令用于连接和路由验证，不执行 Skill 评分。输出中的每个 Agent 都有独立 Trace Key 和数据库模型核验结果。

## 7. 使用一个 Agent 发起 Skill 评测

```powershell
agent-eval run `
  --skill backend/skills/example-marker `
  --agent codex `
  --profile litellm_glm_4_7 `
  --model glm-4.7 `
  --prompt "Return the evaluation marker using the installed Skill." `
  --iterations 1 `
  --parallelism 1 `
  --timeout 1800 `
  --max-turns 12 `
  --database-trace `
  --require-model-verification `
  --llm-judge
```

默认启用基准对照、数据库轨迹、指定模型严格核验和 LLM Judge。输出写入：

```text
backend/evaluation_results/<user>/<task>/<时间__task_id>/
```

核心报告为 `evaluation-report.json`。

## 8. 使用多个 Agent 同时评测同一个 Skill

```powershell
agent-eval run-multi `
  --skill example-marker `
  --agent claude `
  --agent codebuddy `
  --agent codex `
  --agent opencode `
  --profile litellm_glm_4_7 `
  --model glm-4.7 `
  --prompt "Return the evaluation marker using the installed Skill." `
  --workers 2 `
  --iterations 1 `
  --parallelism 1 `
  --timeout 1800 `
  --max-turns 12 `
  --database-trace `
  --require-model-verification `
  --llm-judge
```

`--workers` 控制同时评测的 Agent 数；`--parallelism` 控制每个 Agent 内部同时执行的 case 数，两者含义不同。命令会等待全部 Agent 完成，并汇总每个 Agent 的状态、分数和报告目录。

也可以通过多个 `--case` 使用 Skill 自带或外部 case：

```powershell
agent-eval run-multi `
  --skill example-marker `
  --agent codex `
  --agent opencode `
  --profile litellm_glm_4_7 `
  --model glm-4.7 `
  --prompt "完成测试任务" `
  --case "D:\cases\case-01.yaml" `
  --case "D:\cases\case-02.yaml"
```

## 9. 查看当前支持的 Skill

```powershell
agent-eval skills
```

输出包含 Skill 名称、描述、绝对路径、评测 case 数量以及是否属于原理图 pipeline。

只查看原理图 pipeline 的四个 Skill：

```powershell
agent-eval skills --pipeline
```

四个 Skill 为：

1. `schematic-pipeline`：总编排。
2. `signal-interface-generation`：生成多 Sheet 信号接口表。
3. `schematic-layout-codegen`：器件级代码生成和自动布局。
4. `schematic-web-apply`：把布局结果应用到网页并生成 URL。

## 10. 一键评测原理图生成四 Skill pipeline

`pipeline-eval` 会把上述四个 Skill 复制到一个隔离组合包中。总编排 Skill 负责执行顺序，另外三个 Skill 提供阶段能力。

对两个 Agent 并发评测：

```powershell
agent-eval pipeline-eval `
  --agent codex `
  --agent opencode `
  --profile litellm_glm_4_7 `
  --model glm-4.7 `
  --prompt "生成一个包含电源输入、MCU、传感器和通信接口的多页原理图，并输出最终网页 URL。" `
  --workers 2 `
  --iterations 1 `
  --parallelism 1 `
  --timeout 1800 `
  --max-turns 30 `
  --database-trace `
  --require-model-verification `
  --llm-judge
```

运行前应确认原理图服务健康：

```powershell
Invoke-RestMethod http://127.0.0.1:8631/api/health
```

pipeline 评测会记录四 Skill 名称、Agent 轨迹、工具调用、subagent 信息、模型数据库证据、规则分数和 LLM Judge 分数。某个 Agent 不支持 subagent 时，应在结果中按降级路径和实际轨迹判定，不能伪造 subagent 记录。

## 11. 查看评测结果列表

查看最近 20 条：

```powershell
agent-eval results
```

调整数量：

```powershell
agent-eval results --limit 100
```

按 Agent、Skill 或状态过滤：

```powershell
agent-eval results --agent codex
agent-eval results --skill schematic-pipeline
agent-eval results --status completed
```

组合过滤：

```powershell
agent-eval results `
  --agent opencode `
  --skill schematic-pipeline `
  --status completed `
  --limit 50
```

查看自定义结果根目录：

```powershell
agent-eval results --results-root "D:\evaluation-results" --limit 50
```

结果列表显示任务 ID、时间、状态、Agent、实际模型、Profile、Skill、总分、是否可用于排名以及报告文件位置。

## 12. 环境诊断

```powershell
agent-eval doctor
```

该命令用于检查 Skill-Up、Multica 运行时、模型 Profile 和基础配置。它不会启动正式评测，也不会修改 LiteLLM 鉴权。

## 13. 常用参数说明

| 参数 | 作用 |
|---|---|
| `--agent` | Agent 名称；多 Agent 命令中可重复提供 |
| `--profile` | `backend/config/models.yaml` 中的模型 Profile |
| `--model` | 本次实际请求的 LiteLLM 模型 |
| `--prompt` | 发送给 Agent 的任务文本 |
| `--workers` | 多 Agent 顶层并发数 |
| `--parallelism` | 单个 Agent 内评测 case 并发数 |
| `--iterations` | 每个 case 的重复次数 |
| `--timeout` | 单次 Agent 任务超时，单位秒 |
| `--max-turns` | Agent 最大交互轮数 |
| `--database-verify` | Prompt 探测时要求数据库精确核验 |
| `--database-trace` | 评测时收集 LiteLLM 数据库轨迹 |
| `--require-model-verification` | 将指定模型数据库核验作为通过条件 |
| `--llm-judge` | 使用配置的 LiteLLM Judge 评分 |
| `--benchmark` | 同时运行无 Skill 基线，用于计算 Skill 增益 |

## 14. 推荐工作顺序

```text
agent-eval doctor
  → agent-eval models --refresh
  → agent-eval agents
  → agent-eval skills
  → agent-eval prompt（先验证模型路由）
  → agent-eval run-multi 或 pipeline-eval
  → agent-eval results
```
