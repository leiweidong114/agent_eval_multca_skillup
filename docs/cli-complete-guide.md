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

CLI 默认使用统一 LiteLLM 网关，不再要求 `--profile`。用户只需要选择 Agent、模型和 Prompt。统一网关的非敏感默认值在 `backend/config/models.yaml` 的 `litellm` 节点中；本机密钥可以写入 Git 默认忽略的：

```text
backend/config/litellm.env
```

可从 `backend/config/litellm.env.example` 复制模板。系统仍兼容原有 `secrets.env`，本次迁移不会自动复制或修改任何真实密钥。

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

## 3. 给 LiteLLM 配置模型并用 curl 调用

先区分两种密钥，二者不能混用：

| 名称 | 使用位置 | 用途 |
|---|---|---|
| 上游模型密钥，例如 `GLM_API_KEY` | 只配置在 LiteLLM 服务端 | LiteLLM 调用智谱等上游模型 |
| `LITELLM_API_KEY` | Agent、评测系统或 curl 客户端 | 调用 LiteLLM 网关；日常推理建议使用 Virtual Key |
| `LITELLM_MASTER_KEY` | 只用于 LiteLLM 管理操作 | 调用 `/model/new` 等管理接口，不能当作普通客户端密钥散发 |

下面全部使用占位符，不要把真实密钥写进 Git。LiteLLM 官方支持两种模型配置方式：静态 `config.yaml`，以及启用数据库后的管理 API。生产环境应选择一个作为模型定义的主要数据源，避免同一个模型在文件和数据库中出现两套配置。

### 3.1 方式一：在 LiteLLM 服务端使用 `config.yaml`

在运行 LiteLLM 的服务器新建 `litellm_config.yaml`。以下示例把智谱 OpenAI 兼容接口注册为客户端可见的 `glm-4.7`：

```yaml
model_list:
  - model_name: glm-4.7
    litellm_params:
      model: openai/glm-4.7
      api_base: os.environ/GLM_API_BASE
      api_key: os.environ/GLM_API_KEY

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

在 PowerShell 中设置服务端环境变量并启动 LiteLLM：

```powershell
$env:GLM_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
$env:GLM_API_KEY = "<UPSTREAM_GLM_API_KEY>"
$env:LITELLM_MASTER_KEY = "<LITELLM_MASTER_KEY>"

litellm --config .\litellm_config.yaml --host 0.0.0.0 --port 4000
```

如果使用 Docker：

```powershell
docker run --rm `
  -v "${PWD}\litellm_config.yaml:/app/config.yaml" `
  -e GLM_API_BASE="https://open.bigmodel.cn/api/paas/v4" `
  -e GLM_API_KEY="<UPSTREAM_GLM_API_KEY>" `
  -e LITELLM_MASTER_KEY="<LITELLM_MASTER_KEY>" `
  -p 4000:4000 `
  docker.litellm.ai/berriai/litellm:main-latest `
  --config /app/config.yaml
```

修改静态配置后需要重载或重启 LiteLLM。`model_name` 是客户端在请求中填写的公开名称；`litellm_params.model` 是 LiteLLM 实际路由到的提供商和模型。

### 3.2 方式二：通过管理 API 写入 LiteLLM 数据库

该方式要求 LiteLLM 已连接数据库，并设置以下任意一项：

```yaml
general_settings:
  store_model_in_db: true
```

或者服务端环境变量：

```powershell
$env:STORE_MODEL_IN_DB = "True"
```

使用 Master Key 创建模型。下面是 PowerShell 可直接执行的 `curl.exe` 写法：

```powershell
$env:LITELLM_ADMIN_URL = "http://127.0.0.1:4000"
$env:LITELLM_MASTER_KEY = "<LITELLM_MASTER_KEY>"

$body = @{
  model_name = "glm-4.7"
  litellm_params = @{
    model = "openai/glm-4.7"
    api_base = "https://open.bigmodel.cn/api/paas/v4"
    api_key = "os.environ/GLM_API_KEY"
  }
} | ConvertTo-Json -Depth 5 -Compress

curl.exe -sS -X POST "$env:LITELLM_ADMIN_URL/model/new" `
  -H "Authorization: Bearer $env:LITELLM_MASTER_KEY" `
  -H "Content-Type: application/json" `
  --data-binary $body
```

`GLM_API_KEY` 必须存在于 LiteLLM 服务进程的环境中，而不是只存在于运行 curl 的客户端。创建成功后无需重启。查看完整模型配置（密钥会被遮罩）：

```powershell
curl.exe -sS "$env:LITELLM_ADMIN_URL/model/info" `
  -H "Authorization: Bearer $env:LITELLM_MASTER_KEY"
```

如果 `/model/new` 返回 `403`，说明当前密钥没有模型管理权限；不要绕过鉴权，应由 LiteLLM 管理员提供有权限的 Master Key 或在管理界面创建模型。

### 3.3 使用 curl 调用 LiteLLM 模型

推荐把地址写到 `/v1`，并使用 LiteLLM Virtual Key：

```powershell
$env:LITELLM_BASE_URL = "http://127.0.0.1:4000/v1"
$env:LITELLM_API_KEY = "<LITELLM_VIRTUAL_KEY>"
```

查看当前密钥有权调用的模型：

```powershell
curl.exe -sS "$env:LITELLM_BASE_URL/models" `
  -H "Authorization: Bearer $env:LITELLM_API_KEY"
```

向 `glm-4.7` 发送 `HI`：

```powershell
$body = @{
  model = "glm-4.7"
  messages = @(
    @{ role = "user"; content = "HI" }
  )
  stream = $false
} | ConvertTo-Json -Depth 5 -Compress

curl.exe -sS -X POST "$env:LITELLM_BASE_URL/chat/completions" `
  -H "Authorization: Bearer $env:LITELLM_API_KEY" `
  -H "Content-Type: application/json" `
  --data-binary $body
```

Bash/Linux 等价命令：

```bash
export LITELLM_BASE_URL="http://127.0.0.1:4000/v1"
export LITELLM_API_KEY="<LITELLM_VIRTUAL_KEY>"

curl -sS "$LITELLM_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $LITELLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-4.7",
    "messages": [{"role": "user", "content": "HI"}],
    "stream": false
  }'
```

HTTP `200` 且 `choices[0].message.content` 有内容，表示本次推理成功。`GET /models` 成功只证明模型对该密钥可见，不证明上游额度可用；如果推理返回 `429` 且消息包含“余额不足”或“无可用资源包”，需要补充上游账户额度，不是 Agent 或 curl 命令故障。

LiteLLM 官方参考：[Quick Start](https://docs.litellm.ai/) 和 [Model Management](https://docs.litellm.ai/docs/proxy/model_management)。

### 3.4 在本项目中使用刚配置的模型

模型在 LiteLLM 中创建并通过 curl 验证后，刷新本项目目录并调用 Agent：

```powershell
agent-eval models --refresh

agent-eval check-agent `
  --agent codex `
  --model glm-4.7 `
  --prompt "HI" `
  --timeout 120 `
  --database-verify
```

本机评测系统的 LiteLLM 客户端地址和密钥写在 Git 默认忽略的 `backend/config/litellm.env` 中，格式参考 `backend/config/litellm.env.example`。这里配置的是 LiteLLM 客户端连接，不是在本机重复注册上游模型。

## 4. 查看支持的模型

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

## 5. 查看支持的 Agent

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

## 6. 向一个 Agent 发送任意 Prompt

```powershell
agent-eval check-agent `
  --agent codex `
  --model glm-4.7 `
  --prompt "只回复 COMMAND_OK" `
  --timeout 120 `
  --database-verify
```

成功结果应同时满足：Agent 正常退出、返回内容、数据库 Trace Key 精确命中、数据库模型匹配指定模型以及 Trace Key 成功清理。

## 7. 向多个 Agent 同时发送相同 Prompt

重复使用 `--agent` 指定多个 Agent，`--workers` 控制同时运行数量：

```powershell
agent-eval prompt `
  --agent claude `
  --agent codebuddy `
  --agent codex `
  --agent justdo `
  --agent openclaw `
  --agent opencode `
  --model glm-4.7 `
  --prompt "HI" `
  --workers 3 `
  --timeout 120 `
  --database-verify
```

这里的 `--workers 3` 表示最多同时运行 3 个 Agent，剩余 Agent 在当前命令内部排队。设为 `6` 可尝试六 Agent 同时启动，但应根据本机 CPU、内存和 Agent 限流情况选择。

该命令用于连接和路由验证，不执行 Skill 评分。输出中的每个 Agent 都有独立 Trace Key 和数据库模型核验结果。

## 8. 使用一个 Agent 发起 Skill 评测

```powershell
agent-eval run `
  --skill backend/skills/example-marker `
  --agent codex `
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

## 9. 使用多个 Agent 同时评测同一个 Skill

```powershell
agent-eval run-multi `
  --skill example-marker `
  --agent claude `
  --agent codebuddy `
  --agent codex `
  --agent opencode `
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
  --model glm-4.7 `
  --prompt "完成测试任务" `
  --case "D:\cases\case-01.yaml" `
  --case "D:\cases\case-02.yaml"
```

## 10. 查看当前支持的 Skill

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

## 11. 一键评测原理图生成四 Skill pipeline

`pipeline-eval` 会把上述四个 Skill 复制到一个隔离组合包中。总编排 Skill 负责执行顺序，另外三个 Skill 提供阶段能力。

对两个 Agent 并发评测：

```powershell
agent-eval pipeline-eval `
  --agent codex `
  --agent opencode `
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

## 12. 查看评测结果列表

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

结果列表显示任务 ID、时间、状态、Agent、实际模型、Skill、总分、是否可用于排名以及报告文件位置。报告中的旧 `profile` 字段仅用于兼容历史数据，新命令无需提供它。

## 13. 环境诊断

```powershell
agent-eval doctor
```

该命令用于检查 Skill-Up、Multica 运行时、统一 LiteLLM 网关和基础配置。它不会启动正式评测，也不会修改 LiteLLM 鉴权。

## 14. 常用参数说明

| 参数 | 作用 |
|---|---|
| `--agent` | Agent 名称；多 Agent 命令中可重复提供 |
| `--model` | 本次实际请求的 LiteLLM 模型，例如 `glm-4.7` |
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

## 15. 推荐工作顺序

```text
agent-eval doctor
  → agent-eval models --refresh
  → agent-eval agents
  → agent-eval skills
  → agent-eval prompt（先验证模型路由）
  → agent-eval run-multi 或 pipeline-eval
  → agent-eval results
```
