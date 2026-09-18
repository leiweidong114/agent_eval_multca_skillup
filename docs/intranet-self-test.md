# 内网一键自测

仓库根目录的 `self-test.ps1` 用于在 Windows 内网部署后分层检查：

1. 项目内 Python、Multica、Skill-Up、Node、npm、Go 和前端依赖；
2. 根目录 `.env` 的必要配置（报告不保存密码或 Key）；
3. LiteLLM 模型目录和指定模型的真实推理；
4. PostgreSQL 连接、完整表/字段/索引清单及 `LiteLLM_SpendLogs` 只读权限；
5. 最近最多 1000 条数据库记录的内容可用性：模型、会话 ID、输入消息、模型响应和 Token；
6. 本机 Agent 发现和真实 `Agent + 模型 + Prompt` 调用；
7. 可选的临时 Trace Key、数据库归因和指定模型严格核验。

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
- 读取数据库元数据和最近最多 1000 条日志的 JSON 形状统计，不读取或输出 Prompt/响应正文；
- 将内网 `public."LiteLLM_SpendLogs"` 与
  `backend/config/database-schema-baseline.json` 中的当前可用环境基线比较；
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
├─ database-schema.stdout.json
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
- `configuration_changes`：应该修改的 `.env` 项、权限或服务配置；
- `evidence`：HTTP 状态、运行器/Agent 退出码、可执行文件和协议探测状态。

`database-schema.stdout.json` 还包含：

- PostgreSQL 服务版本、当前数据库和数据库用户；
- 所有非系统表的字段名称、数据类型、可空性、索引和当前用户 SELECT 权限；
- `LiteLLM_SpendLogs` 最近样本中 `metadata`、`proxy_server_request`、
  `messages` 和 `response` 是否存在；
- 上述 JSONB 字段在样本中是 object、array、string 还是 SQL NULL 的数量；
- `metadata` 与 `proxy_server_request.metadata` 的键名和出现次数，不包含值；
- 相对当前环境基线缺少/新增/类型变化的字段以及缺少的索引；
- 是否能够执行评测系统实际使用的 JSON 操作符和查询字段。

数据库检查会明确区分以下情况：

- 连接失败：网络、端口、TLS、账号或密码问题；
- 能连接但没有表：连接到了错误数据库，或 LiteLLM migration 未执行；
- 有表但没有 SELECT 权限：数据库账号权限不足；
- 表结构不兼容：LiteLLM 服务和数据库 migration 版本不一致；
- 表为空：LiteLLM 没有向该数据库写日志，原理图总览和模型归因不可用；
- 有记录但缺少 `session_id`：无法可靠合并多轮会话；
- 有记录但缺少 `end_user`：原理图总览无法按工号/用户筛选；
- 有记录但缺少 `messages` / `response`：页面能列出调用，但无法显示完整交互；
- 有记录但 Token 为零：上游没有返回 usage，或 LiteLLM 未保存 usage。

`self-test-summary.txt` 会列出每个检查步骤的 `OK / DONE / FAILED`、耗时和问题处理建议；
每个命令的原始脱敏 JSON 与 stderr 仍单独保存在同一报告目录中，便于继续定位。

数据库结构不一致时，优先让内网 LiteLLM 使用与基线环境匹配的版本并执行对应的
官方数据库迁移。不要为了让检查通过而手工创建一张空的
`LiteLLM_SpendLogs`，否则 LiteLLM 自身写入和后续升级仍会失败。

脚本发现错误时默认返回退出码 `1`，便于批处理或验收脚本识别。仅希望收集报告、
不希望 PowerShell 返回失败时，可使用：

```powershell
.\self-test.ps1 -NoFailExit
```

不要把包含内网主机名、模型名称或业务 Prompt 的完整报告上传到公网。报告会尽量
遮蔽 API Key、Master Key、数据库密码、Bearer Token 和 `sk-` Key，但分享前仍应人工复核。
