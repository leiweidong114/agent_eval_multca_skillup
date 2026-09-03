# 指定 Agent、模型和 Prompt

## 1. 同步 LiteLLM 模型目录

在项目根目录执行：

```powershell
$env:PYTHONPATH = (Resolve-Path 'backend/src').Path
python -m agent_eval.cli models --refresh
```

命令从 LiteLLM 的 `/v1/models` 读取目录并更新：

```text
backend/config/litellm-models.json
```

只查看某一类模型：

```powershell
python -m agent_eval.cli models --prefix opencode-go/
python -m agent_eval.cli models --prefix glm-4.7
```

目录可见不等于当前一定可推理。额度、区域和上游状态仍应通过实际请求验证。

## 2. 指定 Agent、模型并发送任意 Prompt

```powershell
python -m agent_eval.cli check-agent `
  --agent codex `
  --profile litellm_glm_4_7 `
  --model glm-4.7 `
  --prompt "只回复 COMMAND_OK" `
  --timeout 120 `
  --database-verify
```

若已安装项目的命令行入口，也可以将 `python -m agent_eval.cli` 简写为 `agent-eval`。

`--agent` 可替换为当前支持的 Agent，例如 `claude`、`codebuddy`、`codex`、`justdo`、`openclaw` 或 `opencode`。可通过以下命令查看本机 Agent 发现和能力信息：

```powershell
python -m agent_eval.cli agents
```

成功时输出同时包含：

- Agent 返回内容；
- Agent 进程退出状态；
- Trace Key 对应的数据库调用数；
- 数据库实际记录的模型；
- `model_verification.verified`；
- Trace Key 清理状态。

只有 Agent 成功、数据库精确匹配指定模型并成功清理 Trace Key 时，命令才以退出码 `0` 结束。
