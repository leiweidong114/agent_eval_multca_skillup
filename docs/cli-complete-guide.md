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
| `agent-eval models --refresh` | 同步 LiteLLM 目录，逐个真实推理并只返回可用模型 |
| `agent-eval agents` | 只查看本机已安装、可启动评测的 Agent |
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

该命令不是只读取 `/v1/models`：它会对目录中的每个模型调用一次
`/v1/chat/completions` 并发送 `HI`。输出的 `models` 只包含 HTTP 推理成功的模型。
失败数量通过 `unavailable_model_count` 显示；需要查看已脱敏的逐模型失败原因时增加
`--show-unavailable`。可调整探测并发和单模型超时：

```powershell
agent-eval models --refresh --workers 8 --timeout 15
agent-eval models --refresh --show-unavailable
```

真实探测会产生少量 token 消耗。它证明模型当前能够通过 LiteLLM 完成 Chat Completions
推理，但不等于所有 Agent 协议都兼容。Codex 新版本只使用 Responses API，因此为
Codex 筛选模型时应执行：

```powershell
agent-eval models --refresh --agent codex --workers 8 --timeout 15
```

这时只保留 `/v1/responses` 实际推理成功的模型。其他指定 Agent 最终仍应使用
`check-agent` 验证完整适配链。Agent 专属结果是实时视图，不会覆盖共享的
`litellm-models.json` Chat Completions 快照。

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

该文件不保存 API Key，并保存最近一次连通性探测结果。模型额度或上游状态会变化，正式评测前仍建议重新执行 `models --refresh`，再用 `check-agent` 验证具体 Agent。

## 5. 查看支持的 Agent

```powershell
agent-eval agents
```

默认只输出本机已找到可执行文件的 Agent，并包括默认命令、实际路径、指定模型/Skill 能力和协议适配信息。需要诊断未安装的全部后端时使用：

```powershell
agent-eval agents --all
```

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

2026-09-07 更新：当前已实测的默认 Agent/Judge 模型是 `glm-4.5-air`，
历史示例中的 `glm-4.7` 仍可作为模型 ID 配置，但当日上游额度不足时不能推理。

```text
agent-eval doctor
  → agent-eval models --refresh
  → agent-eval agents
  → agent-eval skills
  → agent-eval prompt（先验证模型路由）
  → agent-eval run-multi 或 pipeline-eval
  → agent-eval results
```

## 16. 根目录 .env、连接诊断与自动启动 JustDo

统一代码路径：`D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup`。
根目录 `.env.example` 是不含真实凭据的模板，`.env` 已被 Git 忽略。
已有 `.env` 时请编辑原文件，不要用模板覆盖。配置示例：

```dotenv
LITELLM_API_BASE=http://your-litellm:4000/v1
LITELLM_API_KEY=your-inference-key
LITELLM_MASTER_KEY=your-existing-key-with-key-management-permission
LITELLM_MODEL=glm-4.5-air
LITELLM_JUDGE_MODEL=glm-4.5-air
LITELLM_USERNAME=admin
LITELLM_PASSWORD=your-dashboard-password
DATABASE_URL=postgresql://reader:password@your-database:5432/litellm
```

配置优先级：当前进程环境变量 > 根目录 `.env` > `backend/.env` >
旧 `backend/config/litellm.env` / `secrets.env` > `local.yaml`。
显式 `--model` 覆盖默认模型。数据库 URL 中的特殊字符需要 URL 编码。
LiteLLM 控制台账号密码、推理 API Key、数据库账号密码是三套不同用途的凭据：
`check-litellm` 使用 API Key，不把控制台密码当作推理 Key，不测试网页登录。
临时 trace key 使用你原有的 Master Key 权限创建，未修改服务器鉴权。

```powershell
cd "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup"
agent-eval check-litellm                         # /models 访问检查，不消耗推理 token
agent-eval check-litellm --model glm-4.5-air      # 再发送 HI，验证真实推理
agent-eval check-database                       # PostgreSQL 连接及 SpendLogs 读取
agent-eval models --list                        # 最新可见模型；可见不等于可用
agent-eval models --refresh --timeout 30 --workers 3  # 对全部可见模型发 HI，缓存成功模型
agent-eval models --refresh --show-unavailable   # 同时输出失败模型及原因
agent-eval models                              # 查看上次连通性探测缓存
agent-eval agents                              # 只列出可执行且版本探测成功的 Agent
agent-eval check-agent --agent justdo            # 自动启动 JustDo CLI，使用默认模型/提示词
agent-eval check-agent --agent justdo --model glm-4.5-air --prompt "你是谁" --database-verify
```

`models --refresh` 会消耗真实推理额度。“可用”只表示本次文本 HI 成功；
不是所有模型、工具、多模态和全部 Agent 的永久兼容认证。
数据库日志异步写入：严格核验等待记录稳定，最长约 60 秒，因此有回答后命令可能还需等待。
trace key 与本次运行绑定；查询到其他 Agent 或探针的调用不能作为本 Agent 调用成功的证据。

新的 LiteLLM 模型可直接 `--model "LiteLLM返回的完整模型ID"`，不必新增 Profile。
统一适配层将 Claude Messages、Codex Responses 的文本/函数工具请求转换为 Chat Completions；
不支持的输入形态会明确报错，不能把视觉、嵌入、服务端搜索能力自动变成文本模型能力。
Codex 的内置 hosted web_search 在该转换模式中关闭，本地文件/命令工具仍可用。
转换模式目前缓冲上游完整响应后输出 SSE，首字延迟不等于原生流式响应。
推理未被关闭；模型自身是否支持/返回 reasoning 字段取决于部署，不能仅看模型名称断言。

## 17. 可重复的系统验收

```powershell
python test/verify_system.py --phase connectivity --model glm-4.5-air --workers 2 --timeout 180
python test/verify_system.py --phase evaluation --model glm-4.5-air --workers 2 --timeout 240
python -m pytest backend/tests -q
```

前两条命令会调用真实模型，默认覆盖当前检测到的六个 Agent；可以重复 `--agent` 缩小范围。
测试文件保存在 `test/`，原始运行证据保存在 `backend/.runtime/verification/`，正式评测报告在
`backend/evaluation_results/`，可在网页“评测结果”中查看。
完整验收额外检查工具调用、落盘 marker 文件、有 Skill=100 / 无 Skill=0、精确模型核验和 LLM Judge。
只有回答正确而文件未落盘，不算通过这套验收。测试不触发 subagent，也不代表四个原理图 Skill 已验收。

原理图总览默认显示最近交互，支持用户/会话过滤、加载更多、展开完整原始请求和响应；
保留数据库已记录的正文并隐藏凭据字段。上游没记录的内容、被上游截断的内容无法补回。
这里只适用于原有受信任本机服务，不新增公网权限；若部署多人环境，需要另行确认访问控制方案。

JustDo 迁移：复制源码后，在目标 Windows 上准备 Node 24、npm 依赖、Electron 原生模块和
OpenClaw runtime，再运行 `npm run multica:build-agent` 重新生成 launcher。
不要只复制 `%APPDATA%\JustDo\multica\development\JustDo-agent.exe`。
详细说明见相邻 JustDo 项目的 `docs/features/multica-headless-cli.md`。

## 18. 启动前端和后端服务

以下命令均在 PowerShell 中执行。前端和后端需要分别占用一个终端窗口。

### 18.1 首次安装

进入项目根目录并安装后端运行时。该脚本会创建项目内独立 Python 环境、安装 Web/数据库依赖，
并构建固定版本的 Multica 和 Skill-Up 运行时：

```powershell
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup"
.\backend\scripts\setup_windows.ps1
```

安装前端依赖：

```powershell
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup\frontend"
npm ci
```

如果已经完成安装，不需要每次启动都重复以上步骤。LiteLLM 和 PostgreSQL 参数放在项目根目录
`.env`；后端会按第 16 节所述优先级加载，不需要在启动命令中写出密钥。

### 18.2 启动后端

推荐使用项目安装脚本创建的 Python。新开一个 PowerShell 窗口：

```powershell
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup"
.\backend\.runtime\windows\python\Scripts\python.exe .\backend\run_server.py --host 127.0.0.1 --port 8000
```

如果当前 Python 已执行过 `python -m pip install -r backend/requirements.txt`，也可以使用：

```powershell
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup"
python .\backend\run_server.py --host 127.0.0.1 --port 8000
```

开发时自动重载：

```powershell
python .\backend\run_server.py --host 127.0.0.1 --port 8000 --reload
```

验证后端和查看自动生成的接口文档：

```powershell
curl.exe http://127.0.0.1:8000/api/health
Start-Process http://127.0.0.1:8000/docs
Start-Process http://127.0.0.1:8000/prism/docs
```

- 主 API OpenAPI JSON：`http://127.0.0.1:8000/openapi.json`
- Prism API OpenAPI JSON：`http://127.0.0.1:8000/prism/openapi.json`
- 停止服务：在后端终端按 `Ctrl+C`。

当前核心 API 和内嵌 Prism 都按“受信任本机”边界运行。不要在没有反向代理、认证和访问控制的
情况下把 `--host` 改成 `0.0.0.0` 暴露到局域网或公网。

### 18.3 启动前端

后端保持运行，再新开一个 PowerShell 窗口：

```powershell
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup\frontend"
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。Vite 会把 `/api` 和 `/prism` 请求代理到
`http://127.0.0.1:8000`。如果后端使用其他地址，在启动前端前设置：

```powershell
$env:VITE_API_TARGET = "http://127.0.0.1:9000"
npm run dev
```

停止前端同样按 `Ctrl+C`。修改前端后可运行生产构建检查：

```powershell
npm run build
```

## 19. 后端主评测 API 与 curl 命令

以下示例使用 Windows 自带的 `curl.exe`，避免 PowerShell 5 中 `curl` 别名指向
`Invoke-WebRequest`。先定义地址；文中的 `$JOB_ID`、`$RUN_ID`、`$BATCH_ID`、
`$PROJECT_ID`、`$PROFILE_NAME` 等变量需要替换为上一步响应里的真实值。

```powershell
$API = "http://127.0.0.1:8000/api"
$MODEL = "glm-4.5-air"
$PROFILE_NAME = "litellm_glm_4_7"
```

### 19.1 健康、Agent、模型和数据库

| 方法与路径 | 作用 |
|---|---|
| `GET /api/health` | 后端进程健康检查 |
| `GET /api/agents` | 全部支持的 Agent、可执行文件探测和适配能力 |
| `POST /api/agents/{agent_name}/test` | 使用“设置”中的默认测试模型，让本机 Agent 完成一次 `HI` 请求 |
| `GET /api/model-config` | 返回脱敏后的默认模型和 Judge 配置 |
| `GET /api/model-profiles` | 返回所有模型 Profile，不回显 API Key |
| `PUT /api/model-profiles/{profile_name}` | 创建或更新本地 Profile，可选择写入 API Key |
| `DELETE /api/model-profiles/{profile_name}` | 删除本地 Profile 覆盖配置 |
| `GET /api/models` | 读取 LiteLLM 可见模型，并合并最近一次真实推理连通性结果 |
| `POST /api/models/test` | 使用指定 Profile 对模型发送最小推理请求 |
| `POST /api/models/test-batch` | 对 LiteLLM 当前全部可见模型并发发送 `HI`，持久化红绿状态 |
| `GET /api/settings` | 查看 LLM Judge 与 Agent 可用性测试的默认模型 |
| `PUT /api/settings` | 保存两个默认模型 ID；不修改地址、密钥或鉴权 |
| `GET /api/database/health` | 检查 PostgreSQL 和 SpendLogs 读取能力 |

```powershell
curl.exe "$API/health"
curl.exe "$API/agents"
curl.exe -X POST "$API/agents/codex/test"
curl.exe "$API/model-config"
curl.exe "$API/model-profiles"
curl.exe "$API/models"
curl.exe "$API/database/health"
curl.exe --json "{`"model`":`"$MODEL`",`"profile`":`"$PROFILE_NAME`"}" "$API/models/test"
curl.exe --json '{"workers":8,"timeout_seconds":30}' "$API/models/test-batch"
curl.exe "$API/settings"
curl.exe -X PUT --json "{`"judge_model`":`"$MODEL`",`"agent_test_model`":`"$MODEL`"}" "$API/settings"
```

创建或更新 Profile 会写入本机且被 Git 忽略的配置文件。下面的 `api_key` 只是占位符；
也可以省略该字段，改为在 `.env` 中配置 `LITELLM_API_KEY`：

```powershell
curl.exe -X PUT --json '{"model":"glm-4.5-air","api_base":"http://your-litellm/v1","api_key_env":"LITELLM_API_KEY","api_key":"REPLACE_ME","protocol":"openai_compatible","context_window":200000,"max_output_tokens":32000,"agent_models":{},"gateway_models":{},"make_default":false}' "$API/model-profiles/my-litellm"
```

删除 Profile 是配置变更，请确认名称后执行：

```powershell
curl.exe -X DELETE "$API/model-profiles/my-litellm"
```

### 19.2 Skill 和本地保留策略

| 方法与路径 | 作用 |
|---|---|
| `GET /api/skills` | 列出内置和已上传 Skill |
| `GET /api/skills/{skill_name}` | 读取 Skill 内容、文件清单和用例数 |
| `GET /api/skills/{skill_name}/cases` | 列出 Skill 的 YAML 用例 |
| `GET /api/skills/{skill_name}/files/{file_path}` | 预览 Skill 内一个文件，最大 2 MB |
| `POST /api/skills/upload` | 上传不超过 20 MB 的 ZIP Skill |
| `GET /api/skills/versions` | 查看上传 Skill 的版本 |
| `DELETE /api/skills/{skill_name}/versions/{version}` | 删除一个上传版本 |
| `GET /api/privacy/retention` | 只预览过期运行，不删除 |
| `POST /api/privacy/retention/cleanup` | 显式删除已过期运行目录 |

```powershell
curl.exe "$API/skills"
curl.exe "$API/skills/example-marker"
curl.exe "$API/skills/example-marker/cases"
curl.exe "$API/skills/example-marker/files/SKILL.md"
curl.exe -F "name=my-skill" -F "archive=@D:\path\my-skill.zip;type=application/zip" "$API/skills/upload"
curl.exe "$API/skills/versions"
curl.exe -X DELETE "$API/skills/my-skill/versions/REPLACE_VERSION"
curl.exe "$API/privacy/retention"
curl.exe --json '{"confirm":true}' "$API/privacy/retention/cleanup"
```

最后两条 `DELETE`/`cleanup` 命令会删除本地数据，执行前先使用版本列表或 retention 预览确认目标。

### 19.3 单 Agent 评测、任务状态和结果

`POST /api/run` 把任务加入后台队列并立即返回 `job_id`；它不代表评测已经完成。
以下请求启用真实模型推理、数据库模型硬核验和 LLM Judge：

```powershell
$runBody = @{
  user_id = "local"
  task_name = "curl-smoke"
  evaluation_type = "skill"
  skills = @("example-marker")
  agent = "codex"
  model = $MODEL
  profile = $PROFILE_NAME
  prompt = "Return the evaluation marker using the installed Skill."
  must_contain = @("SKILL_EVAL_MARKER_OK")
  must_not_contain = @()
  parallelism = 1
  iterations = 1
  timeout_seconds = 300
  max_turns = 12
  benchmark = $true
  collect_database_trace = $true
  require_model_verification = $true
  llm_judge = $true
} | ConvertTo-Json -Depth 10

curl.exe --json $runBody "$API/run"
```

全部任务与结果接口：

| 方法与路径 | 作用 |
|---|---|
| `POST /api/run` | 提交一个后台 Skill/原理图评测任务 |
| `POST /api/validate` | 校验同一请求，不执行完整评测 |
| `GET /api/capacity` | 查看任务池和 case 并发容量 |
| `GET /api/jobs?user_id=...` | 查看任务，可选按用户过滤 |
| `GET /api/jobs/{job_id}` | 查询状态、进度、实时 `events`、模型交互和最终结果 |
| `POST /api/jobs/{job_id}/cancel` | 请求取消任务 |
| `GET /api/runs?user_id=...` | 列出已经生成报告的运行 |
| `GET /api/runs/{run_id}` | 读取完整 `evaluation-report.json` |

```powershell
curl.exe --json $runBody "$API/validate"
curl.exe "$API/capacity"
curl.exe "$API/jobs"
curl.exe --get --data-urlencode "user_id=local" "$API/jobs"
$JOB_ID = "REPLACE_JOB_ID"
curl.exe "$API/jobs/$JOB_ID"
curl.exe -X POST "$API/jobs/$JOB_ID/cancel"
curl.exe "$API/runs"
curl.exe --get --data-urlencode "user_id=local" "$API/runs"
$RUN_ID = "REPLACE_RUN_ID"
curl.exe "$API/runs/$RUN_ID"
```

轮询任务时，直到响应中的 `status` 变为 `completed`、`failed`、`cancelled` 或
`interrupted` 再读取运行报告。`cancel` 是协作式取消，正在退出的 Agent 进程可能需要短暂时间。

### 19.4 多 Agent/模型批量评测

`POST /api/batches` 接收 2–32 个目标，重复的 `(agent, model, profile)` 会去重。
批次内部的实际并发由 `/api/capacity` 和后端任务池控制。

| 方法与路径 | 作用 |
|---|---|
| `POST /api/batches` | 创建多 Agent/模型比较批次 |
| `GET /api/batches?user_id=...` | 列出批次，可选按用户过滤 |
| `GET /api/batches/{batch_id}` | 查询批次及其子任务状态 |

```powershell
$batchBody = @{
  name = "curl-two-agent"
  targets = @(
    @{ agent = "codex"; model = $MODEL; profile = $PROFILE_NAME },
    @{ agent = "opencode"; model = $MODEL; profile = $PROFILE_NAME }
  )
  base_request = @{
    user_id = "local"
    task_name = "curl-batch"
    evaluation_type = "skill"
    skills = @("example-marker")
    prompt = "Return the evaluation marker using the installed Skill."
    must_contain = @("SKILL_EVAL_MARKER_OK")
    parallelism = 1
    iterations = 1
    timeout_seconds = 300
    max_turns = 12
    benchmark = $true
    collect_database_trace = $true
    require_model_verification = $true
    llm_judge = $true
  }
} | ConvertTo-Json -Depth 10

curl.exe --json $batchBody "$API/batches"
curl.exe "$API/batches"
curl.exe --get --data-urlencode "user_id=local" "$API/batches"
$BATCH_ID = "REPLACE_BATCH_ID"
curl.exe "$API/batches/$BATCH_ID"
```

### 19.5 原理图演示、工程、Judge 和模型交互记录

这里的 `/api/schematic/generate` 是确定性演示流水线；对四个原理图 Skill 的真实 Agent
评测仍应使用 `/api/run`、`/api/batches` 或 `agent-eval pipeline-eval`。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/schematic/example` | 获取内置框图示例 |
| `POST /api/schematic/generate` | 生成演示原理图工程并运行专项 Judge |
| `GET /api/schematic/projects/{project_id}` | 读取已生成工程、输入和评分 |
| `POST /api/schematic/judge` | 对外部原理图 JSON 做专项评分 |
| `GET /api/schematic/interactions` | 从 LiteLLM 数据库分页读取完整模型交互 |

```powershell
curl.exe "$API/schematic/example"

$diagram = @{
  title = "LED demo"
  components = @(
    @{ id = "U1"; type = "MCU"; pins = @("PA0", "GND") },
    @{ id = "D1"; type = "LED"; pins = @("A", "K") }
  )
  connections = @(
    @{ from = "U1.PA0"; to = "D1.A"; net = "LED_CTRL" }
  )
} | ConvertTo-Json -Depth 10
curl.exe --json $diagram "$API/schematic/generate"

$PROJECT_ID = "REPLACE_PROJECT_ID"
curl.exe "$API/schematic/projects/$PROJECT_ID"

$judgeBody = @{
  diagram = ConvertFrom-Json $diagram
  schematic = @{ components = @(); connections = @(); nets = @() }
} | ConvertTo-Json -Depth 20
curl.exe --json $judgeBody "$API/schematic/judge"

curl.exe "$API/schematic/interactions?limit=50&offset=0"
curl.exe --get --data-urlencode "user_id=local" --data-urlencode "session_id=REPLACE_SESSION" --data-urlencode "limit=50" --data-urlencode "offset=0" "$API/schematic/interactions"
```

交互接口返回数据库已经记录的 request/response 正文，可能包含用户输入或模型输出。仅在受信任
环境调用，不要把响应上传到公开日志。数据库未启用正文记录时，接口无法恢复历史正文。

## 20. Prism 题库与模型实验 API

Prism 是挂载在同一个后端进程里的独立子应用，API 前缀不是 `$API`，而是：

```powershell
$PRISM = "http://127.0.0.1:8000/prism/api"
```

本项目通过 `create_app(..., trusted_local=True)` 启动 Prism，所有请求使用本地 bootstrap
管理员身份，不需要 Cookie。认证接口是为 Prism 独立部署保留的兼容接口；如果未来关闭
`trusted_local`，可用 `curl.exe -c prism-cookie.txt` 登录，再为后续命令添加
`-b prism-cookie.txt`。

### 20.1 健康、认证、用户和设置

| 方法与路径 | 作用 |
|---|---|
| `GET /prism/api/health` | Prism、Codex、Claude、OpenClaw 探测状态 |
| `GET /prism/api/auth/bootstrap-status` | bootstrap 管理员状态 |
| `POST /prism/api/auth/login` | 独立认证模式登录并设置 Cookie |
| `POST /prism/api/auth/logout` | 注销并撤销 Cookie |
| `GET /prism/api/auth/me` | 当前用户 |
| `GET /prism/api/users` | 管理员查看用户列表 |
| `POST /prism/api/users` | 创建用户 |
| `PUT /prism/api/users/{user_id}` | 更新用户、角色、启用状态或密码 |
| `GET /prism/api/settings` | 当前用户设置 |
| `PUT /prism/api/settings` | 更新健康检查、主题和语言 |
| `GET /prism/api/admin/usage` | 管理员查看用户、实验、Token 和成本统计 |

```powershell
curl.exe "$PRISM/health"
curl.exe "$PRISM/auth/bootstrap-status"
curl.exe "$PRISM/auth/me"
curl.exe -c prism-cookie.txt --json '{"username":"admin","password":"REPLACE_PASSWORD"}' "$PRISM/auth/login"
curl.exe -b prism-cookie.txt -X POST "$PRISM/auth/logout"
curl.exe "$PRISM/users"
curl.exe --json '{"username":"evaluator1","display_name":"Evaluator 1","password":"REPLACE_WITH_10_OR_MORE_CHARS","role":"evaluator"}' "$PRISM/users"
curl.exe -X PUT --json '{"display_name":"Evaluator 1","role":"evaluator","active":true,"password":null}' "$PRISM/users/REPLACE_USER_ID"
curl.exe "$PRISM/settings"
curl.exe -X PUT --json '{"health_check_enabled":true,"health_check_interval_minutes":60,"theme":"forest","language":"zh-CN"}' "$PRISM/settings"
curl.exe "$PRISM/admin/usage"
```

创建/更新用户会修改 Prism 的本地 SQLite 数据；生产密码不要写进 shell 历史或文档。

### 20.2 Provider 和健康检查

| 方法与路径 | 作用 |
|---|---|
| `GET /prism/api/providers` | 查看可访问的模型/Agent Provider |
| `POST /prism/api/providers` | 手动创建 Provider，API Key 加密保存 |
| `POST /prism/api/providers/auto` | 从评测系统的模型 Profile 创建或复用 Provider |
| `PUT /prism/api/providers/{provider_id}` | 更新 Provider |
| `DELETE /prism/api/providers/{provider_id}` | 删除没有历史结果引用的 Provider |
| `POST /prism/api/providers/{provider_id}/test` | 发真实请求测试 Provider 并写入健康记录 |
| `GET /prism/api/provider-health?limit=N` | 查看最近的 Provider 健康历史 |
| `GET /prism/api/protocols` | 查看评测 track、采样策略和默认预算 |

```powershell
curl.exe "$PRISM/providers"
curl.exe --json '{"name":"LiteLLM GLM","kind":"openai","model":"glm-4.5-air","base_url":"http://your-litellm/v1","api_key":"REPLACE_ME","settings":{},"shared":false}' "$PRISM/providers"
curl.exe --json '{"agent":"direct","model":"glm-4.5-air","profile":"litellm_glm_4_7","task_kind":"direct"}' "$PRISM/providers/auto"
curl.exe --json '{"agent":"codex","model":"glm-4.5-air","profile":"litellm_glm_4_7","task_kind":"repo"}' "$PRISM/providers/auto"
curl.exe -X PUT --json '{"name":"LiteLLM GLM Updated","kind":"openai","model":"glm-4.5-air","base_url":"http://your-litellm/v1","api_key":null,"settings":{},"shared":false}' "$PRISM/providers/REPLACE_PROVIDER_ID"
curl.exe -X POST "$PRISM/providers/REPLACE_PROVIDER_ID/test"
curl.exe "$PRISM/provider-health?limit=100"
curl.exe "$PRISM/protocols"
curl.exe -X DELETE "$PRISM/providers/REPLACE_PROVIDER_ID"
```

`providers/{id}/test` 会消耗模型额度。删除 Provider 不可恢复，而且存在历史结果时后端会拒绝删除。

### 20.3 题库、题目和套件

| 方法与路径 | 作用 |
|---|---|
| `GET /prism/api/benchmarks` | 列出可见题库 |
| `GET /prism/api/benchmarks/{benchmark_id}` | 题库详情 |
| `POST /prism/api/benchmarks/{benchmark_id}/install?limit=N` | 安装内置/远程题库，可限制题数 |
| `POST /prism/api/benchmarks/import` | 从 JSON 导入自定义题库 |
| `POST /prism/api/benchmarks/import-jsonl` | 从原始 JSONL 请求体导入题库 |
| `GET /prism/api/benchmarks/{benchmark_id}/items?limit=N` | 查看题目 |
| `GET /prism/api/benchmarks/{benchmark_id}/export?format=json|jsonl` | 导出题库 |
| `GET /prism/api/benchmarks/{benchmark_id}/items/{item_id}/export` | 导出单题 |
| `POST /prism/api/benchmarks/{benchmark_id}/items/{item_id}/try` | 用可用 Provider 试跑单题 |
| `PUT /prism/api/benchmarks/{benchmark_id}/items/{item_id}/access` | 设置自定义题目 private/shared |
| `GET /prism/api/suites` | 查看预设套件及缺失题库 |

```powershell
curl.exe "$PRISM/benchmarks"
curl.exe "$PRISM/benchmarks/REPLACE_BENCHMARK_ID"
curl.exe -X POST "$PRISM/benchmarks/REPLACE_BENCHMARK_ID/install?limit=20"

$benchmarkBody = @{
  id = "curl_demo"
  name = "Curl Demo"
  description = "One-item local benchmark"
  license = "Internal"
  version = "1"
  task_type = "custom_qa"
  language = "zh-CN"
  visibility = "private"
  replace = $false
  items = @(
    @{ key = "q1"; category = "smoke"; prompt = "1+1=?"; expected = "2"; scorer = "exact"; metadata = @{}; access_level = "private" }
  )
} | ConvertTo-Json -Depth 10
curl.exe --json $benchmarkBody "$PRISM/benchmarks/import"

curl.exe -H "Content-Type: application/x-ndjson" --data-binary "@D:\path\benchmark.jsonl" "$PRISM/benchmarks/import-jsonl"
curl.exe "$PRISM/benchmarks/curl_demo/items?limit=100"
curl.exe "$PRISM/benchmarks/curl_demo/export?format=json" -o curl_demo.json
curl.exe "$PRISM/benchmarks/curl_demo/export?format=jsonl" -o curl_demo.jsonl
curl.exe "$PRISM/benchmarks/curl_demo/items/REPLACE_ITEM_ID/export" -o curl_demo_item.json
curl.exe --json '{"provider_id":1,"allow_unsafe_code":false}' "$PRISM/benchmarks/curl_demo/items/REPLACE_ITEM_ID/try"
curl.exe -X PUT --json '{"access_level":"shared"}' "$PRISM/benchmarks/curl_demo/items/REPLACE_ITEM_ID/access"
curl.exe "$PRISM/suites"
```

安装题库可能访问外部数据源；`try` 会真实调用模型。只有最近健康检查为可用的 Provider
才能试跑题目。`replace=true` 会替换同 ID 的自定义题库，使用前先确认目标。

### 20.4 实验、结果、比较、导出、审计和仪表盘

| 方法与路径 | 作用 |
|---|---|
| `POST /prism/api/experiments` | 创建并异步启动题库实验 |
| `GET /prism/api/experiments` | 实验列表 |
| `GET /prism/api/experiments/{experiment_id}` | 实验配置、状态和汇总 |
| `GET /prism/api/experiments/{experiment_id}/results?limit=N` | 逐题结果和证据 |
| `GET /prism/api/experiments/{experiment_id}/comparison` | 汇总和成对比较 |
| `GET /prism/api/experiments/{experiment_id}/export?format=json|csv` | 导出实验 |
| `POST /prism/api/experiments/{experiment_id}/cancel` | 请求取消实验 |
| `GET /prism/api/audit?limit=N` | 查看审计事件 |
| `GET /prism/api/dashboard` | Provider、题库、实验、结果和通过率汇总 |

```powershell
$experimentBody = @{
  name = "curl-prism-smoke"
  provider_ids = @(1)
  benchmark_ids = @("curl_demo")
  repeats = 1
  sample_limit = 1
  concurrency = 1
  allow_unsafe_code = $false
  track = "model_direct"
  random_seed = 42
  sampling_strategy = "ordered"
  budget = @{
    timeout_seconds_per_task = 300
    max_output_tokens = 4096
    max_context_chars = 60000
    max_files_changed = 8
  }
} | ConvertTo-Json -Depth 10

curl.exe --json $experimentBody "$PRISM/experiments"
curl.exe "$PRISM/experiments"
$EXPERIMENT_ID = "REPLACE_EXPERIMENT_ID"
curl.exe "$PRISM/experiments/$EXPERIMENT_ID"
curl.exe "$PRISM/experiments/$EXPERIMENT_ID/results?limit=1000"
curl.exe "$PRISM/experiments/$EXPERIMENT_ID/comparison"
curl.exe "$PRISM/experiments/$EXPERIMENT_ID/export?format=json" -o "experiment-$EXPERIMENT_ID.json"
curl.exe "$PRISM/experiments/$EXPERIMENT_ID/export?format=csv" -o "experiment-$EXPERIMENT_ID.csv"
curl.exe -X POST "$PRISM/experiments/$EXPERIMENT_ID/cancel"
curl.exe "$PRISM/audit?limit=100"
curl.exe "$PRISM/dashboard"
```

`track` 必须与 Provider 类型匹配：`model_direct` 使用直接模型 Provider，`native_agent`
使用 Agent Provider，`reference_agent` 使用 OpenAI/Anthropic HTTP Provider。创建实验会立即开始
真实推理并消耗额度；先调用 Provider test，并用小 `sample_limit` 完成冒烟测试。

## 21. HTTP 状态与排错

| HTTP 状态 | 常见含义 |
|---|---|
| `200` | 请求成功；仍需查看 JSON 中的 `ok`、`status`、`failure` |
| `400` | 参数、Profile、Skill、能力组合或确认字段不合法 |
| `401/403` | 独立 Prism 鉴权失败，或角色/网关权限不足 |
| `404` | Job、Run、Skill、Provider、题库、题目或实验不存在 |
| `409` | 资源状态冲突，例如 Provider 不可用或仍被历史结果引用 |
| `413` | Skill 文件预览超过 2 MB |
| `422` | FastAPI 字段校验失败，或原理图生成/Judge 输入不可处理 |
| `429` | LiteLLM 或上游模型额度/频率达到限制 |
| `500/503` | 后端内部错误、数据库或上游服务不可用 |

接口返回失败时优先执行：

```powershell
agent-eval doctor
agent-eval check-litellm --model glm-4.5-air
agent-eval check-database
curl.exe "$API/model-config"
curl.exe "$API/database/health"
curl.exe "$API/jobs"
```

API 调用中不要使用 `--no-require-model-verification` 对应的弱化配置来掩盖数据库或模型问题。
需要调试请求时可给 curl 添加 `-v`，但其输出可能包含请求头；配置真实密钥后不要把完整
`-v` 日志发布到 issue、聊天或公开测试报告。

JustDo 报错 `openclaw --version failed: exit status 70` 时，先直接运行 launcher 查看其 stderr：

```powershell
& "$env:APPDATA\JustDo\multica\development\JustDo-agent.exe" --version
```

如果提示 `NODE_MODULE_VERSION` 不匹配，说明 `better-sqlite3` 被编译成了 Node.js ABI，
而 launcher 需要 Electron ABI。进入 JustDo 源码目录恢复并重建 launcher：

```powershell
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo"
npm run multica:build-agent
```

修复后的 JustDo 测试脚本会在 Vitest 结束后自动恢复 Electron ABI。不要在运行 launcher 前
单独执行 `npm rebuild better-sqlite3`；如果执行过，至少再运行
`npm run rebuild:electron-native`。

## 22. 将整个系统迁移到新电脑

项目的规范目录和历史目录归一说明见 [`project-structure.md`](project-structure.md)。后端运行时、
工具、Skill 和结果统一位于 `backend/`，仓库根目录不再保留兼容副本。

### 22.1 能否直接复制项目文件夹

可以迁移源码并在新电脑重新构建，但不能把当前电脑生成的虚拟环境、原生模块和 launcher
当成免安装的便携二进制直接使用。完整的可重建系统至少包含两个源码目录：

```text
migration-bundle/
├── agent_eval_multca_skillup/   # dev 分支
└── JustDo/                      # justdo_eval 分支
```

评测系统本身没有依赖旧电脑的固定盘符；路径由仓库位置、环境变量和 `%APPDATA%` 动态解析。
JustDo 的开发 launcher 会记录构建时的源码绝对路径，因此换电脑、换盘符或移动 JustDo
源码后必须重新运行 `npm run multica:build-agent`。其它 Agent 不需要放进项目目录，安装后
保证其命令在新电脑 `PATH` 中即可。

以下生成物不要作为“可移植构建结果”依赖，目标电脑应重新生成：

- 评测系统：`backend/.runtime/`、`backend/.tools/`、Python 虚拟环境、前端
  `node_modules/` 和 `dist/`。
- JustDo：`node_modules/`、`dist/`、`dist-electron/`、`release/` 和旧电脑
  `%APPDATA%\JustDo\multica\development\JustDo-agent.exe`。
- 全局 `agent-eval.exe`：它可能引用旧电脑 Python 或旧源码路径；迁移后优先使用新项目
  虚拟环境内的入口。

`.env`、`backend/config/local.yaml`、`backend/config/secrets.env`、运行报告、Prism SQLite
数据和上传 Skill 都被 Git 忽略，不会随 `git clone` 自动迁移。源码可公开传输，真实凭据应
通过受控渠道单独传输或在新电脑重新填写。

### 22.2 推荐的源码传输方式

目标电脑能访问 GitHub 时，直接检出指定分支最干净：

```powershell
git clone --branch dev https://github.com/leiweidong114/agent_eval_multca_skillup.git
git clone --branch justdo_eval https://github.com/leiweidong114/JustDo.git
```

内网电脑无法访问 GitHub 时，可在当前电脑把两个分支导出成不含 `.git`、运行产物和密钥的
源码 ZIP：

```powershell
New-Item -ItemType Directory -Force -Path "D:\migration-bundle" | Out-Null

git -C "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup" archive `
  --format=zip --output="D:\migration-bundle\agent_eval_multca_skillup-dev.zip" dev

git -C "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo" archive `
  --format=zip --output="D:\migration-bundle\JustDo-justdo_eval.zip" justdo_eval
```

`git archive` 只包含已经提交的文件；执行前先用 `git status` 确认所需修改都已提交。
`.env` 不会进入 ZIP，应单独安全迁移。完全离线的内网环境还需要预先准备 npm 缓存、Python
包缓存、Electron/OpenClaw 下载缓存，以及评测脚本所需的 Go/Multica/Skill-Up 资源；仅有
两个源码 ZIP 而没有网络或依赖缓存，不能完成首次构建。

### 22.3 新电脑前置环境

建议准备：

- Windows 10/11 x64；
- Git；
- Python 3.10 或更高版本，并确保 `python` 可从 PowerShell 调用；
- Node.js `24.15.x–24.x` 和 npm。JustDo 的 `package.json` 不支持 Node 25/26；
- Visual Studio Build Tools 的 C++ 构建工具，用于原生 npm 模块缺少预编译包时回退构建；
- Windows/.NET Framework C# 编译器，用于生成 JustDo 开发 launcher；
- 能访问 LiteLLM HTTP 服务和 LiteLLM PostgreSQL 的网络；
- 其它 Agent 的 CLI。安装后使用各自 `--version` 命令确认，并将目录加入 `PATH`。

### 22.4 在新电脑构建 JustDo

先构建 JustDo，使评测系统能够自动发现无界面 launcher：

```powershell
Set-Location "D:\new-path\JustDo"
npm ci
npm run openclaw:runtime:host
npm run multica:build-agent
```

检查生成结果：

```powershell
$JUSTDO_AGENT = "$env:APPDATA\JustDo\multica\development\JustDo-agent.exe"
Test-Path -LiteralPath $JUSTDO_AGENT
& $JUSTDO_AGENT --version
```

必须得到 `True`、版本号和退出码 0。`openclaw:runtime:host` 首次执行通常需要下载 OpenClaw
依赖；离线迁移必须提前准备对应 runtime。以后只要 JustDo 源码路径发生变化，就在新路径
重新执行 `npm run multica:build-agent`。

### 22.5 在新电脑构建评测系统

```powershell
Set-Location "D:\new-path\agent_eval_multca_skillup"
.\backend\scripts\setup_windows.ps1

Set-Location .\frontend
npm ci
Set-Location ..
```

`setup_windows.ps1` 会在 `backend/.runtime/windows/` 内创建新的 Python 虚拟环境、下载固定
Go、检出固定 Multica/Skill-Up 版本、应用 Windows 补丁、编译运行时并执行测试。不要复用
旧电脑复制来的 Python venv。

创建本机配置：

```powershell
Copy-Item .env.example .env
notepad .env
```

至少按实际环境填写以下值，示例值不能直接使用：

```dotenv
LITELLM_API_BASE=http://your-litellm/v1
LITELLM_API_KEY=your-inference-key
LITELLM_MASTER_KEY=your-existing-master-key
LITELLM_MODEL=glm-4.5-air
LITELLM_JUDGE_MODEL=glm-4.5-air
DATABASE_URL=postgresql://reader:encoded-password@database-host:5432/litellm
```

数据库 URL 中用户名或密码包含 `@`、`:`、`/` 等字符时必须 URL 编码。这里是用户在目标机
填写已有凭据，不需要、也不应该修改 LiteLLM 服务端鉴权设置。

### 22.6 新电脑验收顺序

不要先做六 Agent 全量评测。按下面顺序可以快速定位依赖、网关、数据库或 Agent 问题：

```powershell
$AGENT_EVAL = ".\backend\.runtime\windows\python\Scripts\agent-eval.exe"

& $AGENT_EVAL doctor
& $AGENT_EVAL check-litellm --model glm-4.5-air
& $AGENT_EVAL check-database
& $AGENT_EVAL models --refresh --timeout 30 --workers 3
& $AGENT_EVAL agents
& $AGENT_EVAL check-agent --agent justdo --model glm-4.5-air --prompt "hi" --timeout 180 --database-verify
```

其它 Agent 手动安装完成后，逐个执行相同的 `check-agent`，只替换 `--agent`。`agents`
只会列出本机可执行且版本探测通过的 Agent。全部单 Agent 检查通过后，再执行：

```powershell
python test/verify_system.py --phase connectivity --model glm-4.5-air --workers 2 --timeout 180
python test/verify_system.py --phase evaluation --model glm-4.5-air --workers 2 --timeout 240
```

### 22.7 是否迁移历史数据

只需要在新电脑重新评测时，无需迁移历史数据。需要保留历史时，还要单独复制这些 Git 忽略
目录，并确保仅在后端停止时复制 SQLite 数据：

- `backend/evaluation_results/`：Skill/Agent 历史报告；
- `backend/model_eval_data/`：Prism SQLite、加密密钥和题库实验结果；
- `backend/skills/.registry/`：网页上传的 Skill 版本；
- `backend/schematic_projects/`：原理图演示工程。

Prism 的加密 Provider Key 依赖 `backend/model_eval_data/secret.key`；只复制 SQLite 而遗漏该
文件会导致原有 Provider 密钥无法解密。LiteLLM SpendLogs 位于外部 PostgreSQL，不在项目
文件夹中，新电脑只需连接同一数据库或新的兼容数据库。

## 23. Windows 端口 8000 被占用但查不到进程

错误：

```text
[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)
通常每个套接字地址只允许使用一次
```

表示端口已经被监听或被转发占用，不是 FastAPI 启动失败。先检查 Windows 监听器：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,OwningProcess

netstat -aon | Select-String ":8000"
```

得到 PID 后查看进程：

```powershell
$PORT_PID = (Get-NetTCPConnection -State Listen -LocalPort 8000).OwningProcess
Get-Process -Id $PORT_PID
Get-CimInstance Win32_Process |
  Where-Object ProcessId -eq $PORT_PID |
  Select-Object ProcessId,ExecutablePath,CommandLine
```

如果 Windows 查不到监听 PID，但 socket 仍能连接，应检查 WSL。WSL 2 的 localhost 转发可将
Linux 监听端口映射到 Windows，而监听进程不会总是出现在 Windows `netstat` 中：

```powershell
wsl.exe --list --running
wsl.exe -d Ubuntu-22.04 -- sh -lc "ss -ltnp '( sport = :8000 )'"
curl.exe --noproxy "*" -i http://127.0.0.1:8000/openapi.json
```

2026-09-08 本机实测结果是 WSL `Ubuntu-22.04` 中的 `vllm` 监听
`0.0.0.0:8000`，所以 Windows 后端无法绑定 8000。访问该端口得到的是 vLLM 的 Uvicorn
OpenAPI，`/api/health` 返回 404，并不是 Agent Eval 后端。

本机的 8001 也被该 vLLM 的 Python 子进程监听；当前已确认 8010 可绑定。最安全的做法是
保留 vLLM，先检查候选端口，再让 Agent Eval 使用空闲端口：

```powershell
# 只做本地 bind 检查；输出 PORT_FREE 才继续
python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',8010)); print('PORT_FREE'); s.close()"

# 终端 1：后端
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup"
.\backend\.runtime\windows\python\Scripts\python.exe .\backend\run_server.py `
  --host 127.0.0.1 --port 8010

# 验证必须返回 agent-eval-backend
curl.exe --noproxy "*" http://127.0.0.1:8010/api/health
```

前端终端必须同步指定后端地址：

```powershell
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup\frontend"
$env:VITE_API_TARGET = "http://127.0.0.1:8010"
npm run dev
```

如果确认不再需要 vLLM，应回到启动 vLLM 的 WSL 终端、服务管理器或容器编排工具中正常停止
它。不要仅因为看到端口冲突就执行宽泛的 `taskkill`、`pkill` 或停止整个 WSL；先用 `ss`
确认准确进程和用途。服务停止后再验证 8000：

```powershell
python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',8000)); print('PORT_8000_FREE'); s.close()"
```
