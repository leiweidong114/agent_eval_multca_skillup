# agent_eval_multca_skillup

一个本地、无登录、无数据库、无 LiteLLM 依赖的 Agent Skill 评测工具：

- Skill-Up 负责隔离 Skill、执行用例、断言、基准对照和生成 JSON/HTML/JUnit 报告。
- Multica 的开源 Agent backend 负责统一调用不同 Agent CLI。
- 本项目不启动 Multica Server，不调用 Multica 登录、Issue、数据库或云服务。
- 发给 Agent 的系统提示词固定为空；单条用例 Prompt 按原始字节内容传递，不注入 Multica 默认提示词。

## 架构

```text
agent-eval CLI
  -> Skill-Up（用例、隔离、断言、报告）
    -> multica-eval-runtime（本地自定义引擎）
      -> Multica Agent backend
        -> 指定的 Agent CLI + 指定模型
```

Agent CLI 自身可能需要厂商账号、API Key 或本地配置；这属于 Agent 的认证，不是 Multica 登录。本项目不会读取 PostgreSQL，也不会访问 LiteLLM。

## Windows 安装

要求：Windows 10/11、PowerShell、Git、Python 3.10+。运行：

```powershell
Set-Location D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup
.\scripts\setup_windows.ps1
```

安装内容都保存在项目的 `.runtime/windows` 和 `.tools/windows` 下，不修改系统 Go。脚本固定使用：

- Multica `v0.4.36` / commit `c1a61e1e863eb62ddd7b5fd5ab5ff85391f212fd`
- Skill-Up `v0.9.1` / commit `80c3147101f81017c66f882b767bdc532de5e74f`
- Go `1.26.7`

Skill-Up 0.9.1 的自定义本地引擎硬编码了 POSIX 命令语法。本项目构建时自动应用 [`patches/skill-up-v0.9.1-windows-custom-engine.patch`](patches/skill-up-v0.9.1-windows-custom-engine.patch)，仅修复 Windows `cmd.exe` 的路径引用和旧输出清理，不改变评分逻辑。

## Linux 安装

要求：x86_64/arm64 Linux、`sh`、Git、curl、Python 3.10+：

```sh
cd /path/to/agent_eval_multca_skillup
sh scripts/setup_linux.sh
```

Linux 使用同版本 Multica、Skill-Up 和 Go，产物保存在 `.runtime/linux`、`.tools/linux`。整个项目目录可迁移，但 Windows 与 Linux 的本地二进制目录彼此独立；在目标系统首次运行对应的 setup 脚本即可。

## CLI 使用

检查运行层：

```powershell
.\.runtime\windows\python\Scripts\agent-eval.exe doctor
.\.runtime\windows\python\Scripts\agent-eval.exe agents
```

指定 Agent、模型、Skill 和已有用例：

```powershell
.\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill .\skills\example-marker `
  --agent codex `
  --model gpt-5.4 `
  --case .\skills\example-marker\evals\cases\marker.yaml `
  --agent-executable C:\path\to\codex.exe `
  --parallelism 2 `
  --iterations 1 `
  --benchmark
```

直接用 Prompt 和确定性字符串约束生成临时用例：

```powershell
.\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill C:\skills\my-skill `
  --agent claude `
  --model claude-sonnet-4-5 `
  --prompt "执行这个任务" `
  --must-contain "expected marker" `
  --must-not-contain "forbidden text" `
  --agent-executable C:\path\to\claude.exe
```

Linux 将入口替换为 `.runtime/linux/python/bin/agent-eval`，参数完全相同。`--agent-executable` 可省略，此时从 `PATH` 查找该 Agent 的默认命令。模型字符串直接传给所选 Agent；模型是否可用由该 Agent 的本地配置和服务端权限决定。

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
.\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill .\skills\example-marker `
  --agent openclaw `
  --model main `
  --agent-executable "$env:APPDATA\JustDo\multica\development\JustDo-agent.exe" `
  --case .\skills\example-marker\evals\cases\marker.yaml `
  --parallelism 1 `
  --iterations 1 `
  --benchmark
```

Linux：

```sh
./.runtime/linux/python/bin/agent-eval run \
  --skill ./skills/example-marker \
  --agent openclaw \
  --model main \
  --agent-executable "$HOME/.local/bin/JustDo-agent" \
  --case ./skills/example-marker/evals/cases/marker.yaml \
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
