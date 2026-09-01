# agent_eval_multca_skillup

一个本地、无 Multica 登录的 Agent Skill 评测工具。支持 Agent 原生模型认证或远程 LiteLLM，并可直接读取 LiteLLM PostgreSQL 交互数据参与过程评测：

- Skill-Up 负责隔离 Skill、执行用例、断言、基准对照和生成 JSON/HTML/JUnit 报告。
- Multica 的开源 Agent backend 负责统一调用不同 Agent CLI。
- 本项目不启动 Multica Server，不调用 Multica 登录、Issue 或数据库服务。
- 发给 Agent 的系统提示词固定为空；单条用例 Prompt 按原始字节内容传递，不注入 Multica 默认提示词。
- PostgreSQL 作为指定模型硬校验的数据源；默认要求数据库精确确认当前任务实际调用了指定模型。

## 项目结构（dev 分支，前后端分离）

本分支在原有 CLI 工具基础上新增了前后端分离的 Web 界面：

```text
agent_eval_multca_skillup/
├── backend/                 # 后端（Python + FastAPI）
│   ├── app/
│   │   ├── main.py          # FastAPI 入口（含 CORS、路由装配）
│   │   ├── config.py        # 后端路径配置
│   │   └── api/             # REST 接口
│   │       ├── routes_eval.py    # 评测运行 /api/run、/api/validate
│   │       ├── routes_skill.py   # Skill/Agent 发现 /api/skills、/api/agents
│   │       ├── routes_schematic.py # 原理图生成、工程读取与 JSON 专项 Judge
│   │       └── routes_runs.py    # 历史记录 /api/runs、/api/runs/{id}
│   ├── src/                 # 原有 agent_eval 评测核心逻辑
│   ├── tests/               # 原有单元测试
│   ├── skills/              # 评测用 Skill（example-marker）
│   ├── run_server.py        # 后端启动脚本
│   └── requirements.txt     # Web 后端依赖（fastapi/uvicorn）
├── frontend/                # 前端（Vue 3 + Vite + Element Plus）
│   ├── src/
│   │   ├── views/           # 评测运行/评测结果/Skill管理 页面
│   │   ├── components/      # 评分卡片等组件
│   │   └── api/             # axios 封装
│   ├── vite.config.js       # dev 代理 /api -> http://127.0.0.1:8000
│   └── package.json
├── tests_reports/           # 各步骤测试报告
└── README.md
```

### 后端接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/health | 健康检查 |
| GET | /api/agents | 支持的 Agent 列表 |
| GET | /api/skills | 可用 Skill 列表 |
| GET | /api/skills/{name}/cases | 某 Skill 的用例列表 |
| GET | /api/model-config | 非敏感模型配置 |
| GET | /api/database/health | PostgreSQL 直连状态与交互记录数 |
| POST | /api/run | 创建后台评测任务，立即返回 job_id |
| GET/POST | /api/jobs、/api/jobs/{id}/cancel | 进度查询与取消 |
| GET | /api/capacity | 顶层任务池与单任务 case 并发容量 |
| POST | /api/validate | 仅校验配置不完整运行 |
| GET | /api/runs | 历史评测记录 |
| GET | /api/runs/{run_id} | 单次评测详情 |
| POST/GET | /api/skills/upload、/api/skills/versions | Skill ZIP 上传与内容版本管理 |
| GET/POST | /api/privacy/retention、/api/privacy/retention/cleanup | 保留策略预览与显式清理 |
| GET/POST | /api/schematic/example、/api/schematic/generate | 框图示例与完整原理图流水线 |
| POST | /api/schematic/judge | 对外部生成的原理图 JSON 做专项评分 |
| GET | /api/schematic/projects/{id} | 读取可在网页打开的原理图工程 |

### 启动方式

后端（需先运行 `backend/scripts/setup_windows.ps1` 生成运行时）：

```powershell
cd backend
python run_server.py --port 8000
```

前端：

```powershell
cd frontend
npm install
npm run dev   # 打开 http://127.0.0.1:5173
```

前端 dev 服务器会把 `/api` 请求代理到后端 `http://127.0.0.1:8000`。

## 架构

```text
agent-eval CLI
  -> Skill-Up（用例、隔离、断言、报告）
    -> multica-eval-runtime（本地自定义引擎）
      -> Multica Agent backend
        -> 指定的 Agent CLI + 指定模型
```

Agent CLI 自身可能需要本地安装和配置；这属于 Agent 运行环境，不是 Multica 登录。评测会为每个任务创建独立的 LiteLLM 虚拟 Key，并按 Key 别名关联 PostgreSQL `LiteLLM_SpendLogs`，原始记录写入任务目录的 `model-interactions.json`。

评测任务由后端工作线程执行，任务状态写入 `backend/evaluation_results/_jobs`，前端可实时查询进度和取消。所有任务产物按 `用户/任务/时间__run_id` 集中归档；服务重启后未完成任务会标记为 `interrupted`。

并发、三维评分、Skill 安装语义、可观测字段限制和新 Agent 上线门槛见
[`docs/evaluation-scoring-and-agent-contract.md`](docs/evaluation-scoring-and-agent-contract.md)。

## 模型配置

默认配置位于 `backend/config/models.yaml`：

```yaml
default_profile: native_codex
profiles:
  native_codex:
    type: native
    model: gpt-5.4
  litellm_deepseek_flash:
    model: deepseek-v4-flash
    api_base: http://8.137.196.46/v1
    api_key_env: LITELLM_API_KEY
  litellm_minimax:
    model: MiniMax-M3
    api_base: http://8.137.196.46/v1
    api_key_env: LITELLM_API_KEY
  litellm_opencode_go:
    model: opencode-go/minimax-m3
    api_base: http://8.137.196.46/v1
    api_key_env: LITELLM_API_KEY
  litellm_opencode_go_minimax_2_7:
    model: opencode-go/minimax-m2.7
    api_base: http://8.137.196.46/v1
    api_key_env: LITELLM_API_KEY
```

虚拟 Key 使用环境变量 `LITELLM_API_KEY`，或写入被 Git 忽略的
`backend/config/local.yaml`：

```yaml
secrets:
  LITELLM_API_KEY: sk-your-virtual-key
```

运行时会为不同 Agent CLI 同时提供 OpenAI 兼容变量
`OPENAI_BASE_URL`/`OPENAI_API_KEY` 和 Anthropic 兼容变量
`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`。虚拟 Key 不会写入生成的
`eval.yaml`、评测报告或 Git。可通过修改 `models.yaml` 添加多个 profile，运行时用
`--profile <name>` 选择；`--model` 仅用于临时覆盖该 profile 的默认模型。
Codex 还会自动获得 `model_provider=litellm` 的命令行配置，避免已有 ChatGPT 登录覆盖
LiteLLM 地址。OpenClaw/JustDo 会继续使用 Agent ID `main`，并由评测端生成临时
LiteLLM provider 配置；配置只引用 `${LITELLM_API_KEY}`，不会包含真实 Key。

OpenCode Go 模型在 LiteLLM UI 中使用 `opencode-go/<model-id>` 名称管理。无需启动
Skill-Up 评测即可先做 Agent/模型连通性检查：

```powershell
agent-eval check-agent --agent codex --profile litellm_opencode_go
agent-eval check-agent --agent claude --profile litellm_opencode_go
agent-eval check-agent --agent codebuddy --profile litellm_opencode_go
agent-eval check-agent --agent openclaw --profile litellm_opencode_go
agent-eval check-agent --agent opencode --profile litellm_opencode_go
```

例如统一验证 MiniMax 2.7：

```powershell
agent-eval check-agent --agent codex --profile litellm_opencode_go_minimax_2_7
```

各 Agent 的 CLI 模型名可以不同，但底层 LiteLLM deployment 必须是同一个。例如
CodeBuddy 只接受 `custom-local:MiniMax-M2.7`，评测系统会将它确定性映射到
`opencode-go/minimax-m2.7`；报告中保留统一模型名和 Agent 实际参数，避免把别名误当成
另一个模型。

该命令只发送一次 `CONNECTIVITY_OK` 探针，不创建 Skill-Up 运行、评分或评测结果目录。
`agent-eval agents` 可列出 Multica 支持的后端及当前机器实际安装的 CLI；只有探测到
可执行文件的 Agent 才能在本机完成连通性验证。

## PostgreSQL 配置

非敏感连接参数位于 `backend/config/database.yaml`。密码通过环境变量提供：

```powershell
$env:LITELLM_DATABASE_PASSWORD = "数据库密码"
```

也可在被 Git 忽略的 `backend/config/local.yaml` 中配置：

```yaml
secrets:
  LITELLM_API_KEY: sk-your-virtual-key
  LITELLM_DATABASE_PASSWORD: your-database-password
  LITELLM_MASTER_KEY: sk-your-litellm-master-key
```

如果运行环境已经提供完整 `DATABASE_URL`，它优先于分项配置。数据库用户只需对 `LiteLLM_SpendLogs` 具有只读权限。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/database/health
```

部署脚本也可以生成被 Git 忽略的 `backend/config/secrets.env`：

```dotenv
LITELLM_DATABASE_PASSWORD=your-database-password
LITELLM_MASTER_KEY=sk-your-litellm-master-key
```

如需把一次运行和数据库记录严格一一对应，额外在后端进程环境设置 LiteLLM Master Key：

```powershell
$env:LITELLM_MASTER_KEY = "你的 LiteLLM Master Key"
```

后端优先为每个任务创建一小时有效的临时虚拟 Key，并按 `key_alias` 精确读取 SpendLogs，运行结束后删除。若网关禁止管理接口，则退化为任务时间窗口匹配，并在报告中明确标记较弱的 Agent 归因。默认开启模型硬校验：没有成功调用或实际模型不匹配都会令任务失败。只有显式使用 `--no-require-model-verification` 才允许保留“未确认”的诊断结果。默认不读取 messages/response。

## 原理图完整 Skill 与专项 Judge

内置 Skill 位于 `backend/skills/schematic-generation`，包含 147 示例、格式契约、流水线脚本和专项 Judge。可脱离 Agent 单独验证：

```powershell
python backend/skills/schematic-generation/scripts/schematic_pipeline.py `
  --input backend/skills/schematic-generation/assets/example_block_diagram.json `
  --output backend/schematic_projects/demo/generated
python backend/skills/schematic-generation/scripts/schematic_judge.py `
  --input backend/skills/schematic-generation/assets/example_block_diagram.json `
  --output backend/schematic_projects/demo/generated
```

网页 `/schematic` 可编辑/展示框图，执行信号接口提取、公共/私有 CBB 分流、器件并行生成、整版 JSON 打包和专项评分，并返回 `/schematic?project=<id>` 工程 URL。Judge 总分 100：器件 25、引脚 15、连线拓扑 40、网络名 15、Schema/过程产物 5。

## Windows 安装

要求：Windows 10/11、PowerShell、Git、Python 3.10+。运行：

```powershell
Set-Location D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup
.\backend\scripts\setup_windows.ps1
```

安装内容都保存在项目的 `backend/.runtime/windows` 和 `backend/.tools/windows` 下，不修改系统 Go。脚本固定使用：

- Multica `v0.4.36` / commit `c1a61e1e863eb62ddd7b5fd5ab5ff85391f212fd`
- Skill-Up `v0.9.1` / commit `80c3147101f81017c66f882b767bdc532de5e74f`
- Go `1.26.7`

Skill-Up 0.9.1 的自定义本地引擎硬编码了 POSIX 命令语法。本项目构建时自动应用 [`backend/patches/skill-up-v0.9.1-windows-custom-engine.patch`](backend/patches/skill-up-v0.9.1-windows-custom-engine.patch)，仅修复 Windows `cmd.exe` 的路径引用和旧输出清理，不改变评分逻辑。

## Linux 安装

要求：x86_64/arm64 Linux、`sh`、Git、curl、Python 3.10+：

```sh
cd /path/to/agent_eval_multca_skillup
sh backend/scripts/setup_linux.sh
```

Linux 使用同版本 Multica、Skill-Up 和 Go，产物保存在 `backend/.runtime/linux`、`backend/.tools/linux`。整个项目目录可迁移，但 Windows 与 Linux 的本地二进制目录彼此独立；在目标系统首次运行对应的 setup 脚本即可。

## CLI 使用

检查运行层：

```powershell
.\backend\.runtime\windows\python\Scripts\agent-eval.exe doctor
.\backend\.runtime\windows\python\Scripts\agent-eval.exe agents
```

指定 Agent、模型、Skill 和已有用例：

```powershell
.\backend\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill .\backend\skills\example-marker `
  --user wedax `
  --task-name marker-regression `
  --agent codex `
  --profile litellm_deepseek_flash `
  --case .\backend\skills\example-marker\evals\cases\marker.yaml `
  --agent-executable C:\path\to\codex.exe `
  --parallelism 2 `
  --iterations 1 `
  --benchmark
```

直接用 Prompt 和确定性字符串约束生成临时用例：

```powershell
.\backend\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill C:\skills\my-skill `
  --agent claude `
  --profile litellm_deepseek_flash `
  --prompt "执行这个任务" `
  --must-contain "expected marker" `
  --must-not-contain "forbidden text" `
  --agent-executable C:\path\to\claude.exe
```

Linux 将入口替换为 `backend/.runtime/linux/python/bin/agent-eval`，参数完全相同。`--agent-executable` 可省略，此时从 `PATH` 查找该 Agent 的默认命令。模型字符串直接传给所选 Agent；模型是否可用由该 Agent 的本地配置和服务端权限决定。

当前可选 Agent 包含 Multica 原生 backend，以及映射到 OpenClaw backend 的 `justdo` 入口。运行 `agent-eval agents` 可查看当前机器实际探测到的可执行文件。

### 使用 JustDo Agent

JustDo 提供兼容 OpenClaw CLI 的本地 Agent launcher。保持 JustDo 运行或驻留托盘，
使用 `--agent justdo` 和模型 profile；评测系统会自动使用 OpenClaw backend，并生成只对
当前任务有效的模型配置。Windows 会自动发现开发 launcher，也可用
`JUSTDO_AGENT_EXECUTABLE` 或 `--agent-executable` 显式指定。

Windows 开发模式：

```powershell
Set-Location D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo
npm run multica:dev-agent
npm run electron:dev:openclaw

Set-Location D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup
.\backend\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill .\backend\skills\example-marker `
  --agent justdo `
  --profile litellm_opencode_go_minimax_2_7 `
  --agent-executable "$env:APPDATA\JustDo\multica\development\JustDo-agent.exe" `
  --case .\backend\skills\example-marker\evals\cases\marker.yaml `
  --parallelism 1 `
  --iterations 1 `
  --benchmark
```

Linux：

```sh
./backend/.runtime/linux/python/bin/agent-eval run \
  --skill ./backend/skills/example-marker \
  --agent justdo \
  --profile litellm_opencode_go_minimax_2_7 \
  --agent-executable "$HOME/.local/bin/JustDo-agent" \
  --case ./backend/skills/example-marker/evals/cases/marker.yaml \
  --parallelism 1 \
  --iterations 1 \
  --benchmark
```

## 用例和输出

最小用例：

```yaml
id: json-output
title: Generate expected JSON
input:
  prompt: Generate the requested artifact.
expect:
  must_contain:
    - '"schema_version"'
  must_not_contain:
    - traceback
```

每次运行会把源 Skill 复制到隔离目录：

```text
backend/evaluation_results/<用户>/<任务>/<时间>__<run_id>/
  staging/skill/            # 隔离副本与本次 eval.yaml
  skill-up/                 # Skill-Up JSON、HTML、JUnit、日志和 transcript
  model-interactions.json   # PostgreSQL 中与本次运行匹配的模型交互
  evaluation-report.json    # 本项目统一汇总
```

`evaluation-report.json` 的 `scores` 字段均为透明的确定性统计：

- `task_score`：有 Skill 时断言通过率，0–100。
- `baseline_score`：无 Skill 基准的断言通过率；未启用 benchmark 时为 `null`。
- `skill_gain`：`task_score - baseline_score`，衡量 Skill 带来的净提升。
- `execution_stability`：有 Skill 用例中成功完成评测流程的比例；PASS 和普通断言 FAIL 都算完成，运行错误/超时不算。
- `skill_quality_score`：对 Skill 名称、描述、流程、约束、产物、异常处理和验证说明的透明结构评分。
- `model_trace_score`：数据库匹配模型调用的成功率；未走 LiteLLM 或没有匹配记录时为 `null`。
- `model_verification_score`：数据库精确确认指定模型时为 100，否则为 0；详情见 `model_verification`。
- `total_tokens`、`total_duration_ms`：所有本次执行的资源统计。

默认 `--benchmark` 会同时运行有 Skill 和无 Skill 两组。只验证任务结果、不做基线时使用 `--no-benchmark`。增加 `--iterations N` 可用于稳定性评测。

## 无登录与无默认提示词保证

本地运行层只导入 `server/pkg/agent`，不会启动或引用 Multica 的 Web、daemon、auth、issue、数据库和任务提示词模块。执行时明确传入 `SystemPrompt: ""`。对应行为由 Go 单元测试覆盖，可运行：

```powershell
.\.runtime\windows\go\bin\go.exe test .\cmd\multica-eval-runtime
```

该命令需要在 `.runtime/windows/src/multica/server` 下执行。项目 Python 测试：

```powershell
.\.runtime\windows\python\Scripts\python.exe -m pytest
```
