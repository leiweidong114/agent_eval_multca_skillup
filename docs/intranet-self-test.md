# 内网一键自测

仓库根目录的 `self-test.ps1` 用于在 Windows 内网部署后分层检查：

1. 项目内 Python、Multica、Skill-Up、Node、npm、Go 和前端依赖；
2. 根目录 `.env` 的必要配置（报告不保存密码或 Key）；
3. LiteLLM 模型目录和指定模型的真实推理；
4. PostgreSQL 连接及 `LiteLLM_SpendLogs` 只读权限；
5. 本机 Agent 发现和真实 `Agent + 模型 + Prompt` 调用；
6. 可选的临时 Trace Key、数据库归因和指定模型严格核验。

## 基本用法

在仓库根目录运行：

```powershell
.\self-test.ps1
```

默认行为：

- 列出 LiteLLM 对当前 Key 可见的模型；
- 对 `.env` 中 `LITELLM_MODEL`、`AGENT_TEST_MODEL` 和
  `LITELLM_JUDGE_MODEL` 的不同取值逐一发送一次 `HI`；
- 列出全部 Agent，并对本机实际发现的每个 Agent 使用第一个配置模型发送最小 Prompt；
- 检查 PostgreSQL，但基础 Agent 调用不依赖数据库核验；
- 所有输出写入 `backend/.runtime/self-test/<时间>/`。

只测试指定 Agent 和模型：

```powershell
.\self-test.ps1 `
  -Agent codex,justdo `
  -Model glm-4.5-air `
  -Timeout 180
```

如果 JustDo 参与测试，必须先启动与 `JustDo-agent.exe` 匹配的 JustDo
桌面端并保持在主界面运行。

## 严格核验

```powershell
.\self-test.ps1 `
  -Agent codex,justdo `
  -Model glm-4.5-air `
  -Strict `
  -Timeout 300
```

`-Strict` 会让每个选中 Agent 再执行一次数据库严格核验，要求：

- `LITELLM_MASTER_KEY` 可以调用 `/key/generate` 和 `/key/delete`；
- PostgreSQL 可用；
- 当前数据库用户可以读取 `LiteLLM_SpendLogs`；
- LiteLLM 能按本次临时 Key 记录模型调用。

## 探测全部模型

```powershell
.\self-test.ps1 `
  -Agent codex `
  -Model glm-4.5-air `
  -ProbeAllModels `
  -ModelWorkers 2 `
  -Timeout 60
```

`-ProbeAllModels` 会对 LiteLLM 返回的每个可见模型发送真实推理请求，可能产生
Token 消耗并受上游并发限制。默认不启用。

## 报告

每次运行生成：

```text
backend/.runtime/self-test/<时间>/
├─ self-test-summary.txt
├─ self-test-report.json
├─ doctor.stdout.json
├─ model-catalog.stdout.json
├─ model-<模型>.stdout.json
├─ database.stdout.json
├─ agent-catalog.stdout.json
└─ agent-<Agent>-basic.stdout.json
```

开启 `-Strict` 后还会生成 `agent-<Agent>-strict.stdout.json`。

`self-test-report.json` 中的 `issues` 包含：

- `component`：失败层级；
- `category`：可机器识别的错误类别；
- `summary`：问题摘要；
- `detail`：脱敏后的详细错误；
- `suggested_action`：建议处理方法。

脚本发现错误时默认返回退出码 `1`，便于批处理或验收脚本识别。仅希望收集报告、
不希望 PowerShell 返回失败时，可使用：

```powershell
.\self-test.ps1 -NoFailExit
```

不要把包含内网主机名、模型名称或业务 Prompt 的完整报告上传到公网。报告会尽量
遮蔽 API Key、Master Key、数据库密码、Bearer Token 和 `sk-` Key，但分享前仍应人工复核。
