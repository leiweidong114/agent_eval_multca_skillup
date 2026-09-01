# agent_eval_multca_skillup

一个本地、无 Multica 登录、无数据库依赖的 Agent Skill 评测工具。默认通过本机 LiteLLM 使用 MiniMax 模型：

- Skill-Up 负责隔离 Skill、执行用例、断言、基准对照和生成 JSON/HTML/JUnit 报告。
- Multica 的开源 Agent backend 负责统一调用不同 Agent CLI。
- 本项目不启动 Multica Server，不调用 Multica 登录、Issue 或数据库服务。
- 发给 Agent 的系统提示词固定为空；单条用例 Prompt 按原始字节内容传递，不注入 Multica 默认提示词。

## 项目结构（server_dev 分支，前后端分离）

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
| POST | /api/run | 触发评测运行 |
| POST | /api/validate | 仅校验配置不完整运行 |
| GET | /api/runs | 历史评测记录 |
| GET | /api/runs/{run_id} | 单次评测详情 |

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

Agent CLI 自身可能需要本地安装和配置；这属于 Agent 运行环境，不是 Multica 登录。本项目不会读取 PostgreSQL。默认模型流量发送到配置的 LiteLLM 服务。

## LiteLLM / MiniMax 模型配置

默认配置位于 `backend/config/models.yaml`：

```yaml
default_profile: litellm_minimax
profiles:
  litellm_minimax:
    model: MiniMax-M3
    api_base: http://127.0.0.1:4000/v1
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
LiteLLM 地址。

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
  --agent codex `
  --profile litellm_minimax `
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
  --profile litellm_minimax `
  --prompt "执行这个任务" `
  --must-contain "expected marker" `
  --must-not-contain "forbidden text" `
  --agent-executable C:\path\to\claude.exe
```

Linux 将入口替换为 `backend/.runtime/linux/python/bin/agent-eval`，参数完全相同。`--agent-executable` 可省略，此时从 `PATH` 查找该 Agent 的默认命令。模型字符串直接传给所选 Agent；模型是否可用由该 Agent 的本地配置和服务端权限决定。

当前 Multica 版本可选 backend：`antigravity`、`claude`、`codebuddy`、`codex`、`copilot`、`cursor`、`deveco`、`dim`、`dsh`、`grok`、`hermes`、`kimi`、`kiro`、`mcode`、`omp`、`openclaw`、`opencode`、`pi`、`qoder`、`qoderclicn`、`qwen`、`qwenpaw`、`reasonix`、`traecli`、`zeroclaw`。

### 使用 JustDo Agent

JustDo 的 `justdo_eval` 分支提供兼容 OpenClaw CLI 的本地 Agent launcher。保持 JustDo
运行或驻留托盘，在设置中启用外部连接，然后使用 `--agent openclaw`、`--model main` 和
launcher 的绝对路径。这里的 `main` 是 JustDo/OpenClaw agent ID，不是底层模型名；实际
模型和凭据由 JustDo 配置管理。

Windows 开发模式：

```powershell
Set-Location D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo
npm run multica:dev-agent
npm run electron:dev:openclaw

Set-Location D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup
.\backend\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill .\backend\skills\example-marker `
  --agent openclaw `
  --model main `
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
  --agent openclaw \
  --model main \
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
runs/<时间>__<skill>__<agent>-<model>__<id>/
  staging/skill/            # 隔离副本与本次 eval.yaml
  skill-up/                 # Skill-Up JSON、HTML、JUnit、日志和 transcript
  evaluation-report.json    # 本项目统一汇总
```

`evaluation-report.json` 的 `scores` 字段均为透明的确定性统计：

- `task_score`：有 Skill 时断言通过率，0–100。
- `baseline_score`：无 Skill 基准的断言通过率；未启用 benchmark 时为 `null`。
- `skill_gain`：`task_score - baseline_score`，衡量 Skill 带来的净提升。
- `execution_stability`：有 Skill 用例中成功完成评测流程的比例；PASS 和普通断言 FAIL 都算完成，运行错误/超时不算。
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
