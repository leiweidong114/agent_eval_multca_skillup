# OpenCode 六 Agent 模型适配问题解决方案

日期：2026-09-03

## 1. 处理优先级

### P0：恢复至少一个 OpenCode 模型的真实推理能力

当前 30 个模型返回每周额度耗尽。可选方案：

1. 等待上游提示的周期重置后重跑。
2. 由用户在 OpenCode workspace 显式允许使用可用余额。
3. 在 LiteLLM 中增加具有独立可用额度的 OpenCode provider/deployment，并保持公共模型映射不变。

这些操作涉及账户、额度或鉴权配置。按照项目约束，本次没有自动执行；实施前必须取得用户明确授权。

完成条件：至少一个 `opencode-go/*` 模型对 `HI` 返回 HTTP 2xx，并产生成功 SpendLogs。

### P1：处理区域限制模型

- `deepseek-v4-flash`、`deepseek-v4-pro`：确认数据托管和合规要求后，由用户决定是否启用中国托管 opt-in。
- `muse-spark-1.2-contributor`：当前地区不可用，应在 UI/CLI 标记 `region_unavailable`，不进入本机可用模型集合；不要用 fallback 冒充同一模型。

### P1：恢复 JustDo 运行态

当前 launcher 返回退出码 69：

```text
JustDo is not running. Start JustDo and keep it open or in the tray.
```

处理：启动与该 development launcher 匹配的 JustDo 桌面端并保持托盘运行，然后先执行：

```powershell
& "$env:APPDATA\JustDo\multica\development\JustDo-agent.exe" --version
```

通过条件：退出码 0。之后再运行 JustDo 单项 `HI` + DB 核验。

### P1：复测 OpenClaw 无数据库请求超时

模型恢复后按以下顺序定位：

1. `openclaw --version` 必须退出 0。
2. 检查临时 `OPENCLAW_CONFIG_PATH`、state 和 workspace 是否实际生效。
3. 对本地兼容代理记录首个请求到达时间、路径和目标模型，不记录 Authorization。
4. 将 Agent 启动超时和模型响应超时分开。
5. 若仍无请求，检查 OpenClaw 是否等待交互、迁移或首次启动确认。

通过条件：单次 `HI` 至少产生一条按 trace alias 精确匹配的成功 SpendLogs。

### P1：统一项目测试 Python 环境

当前系统 Python 在显式绑定本仓库 `backend/src` 后能运行适配器相关测试，但缺少 FastAPI；未绑定时还会优先导入另一个 `agent_eval_multca_skillup_win_offline` 副本，现有根目录隔离 Python 又曾缺少 `httpx`。应将 setup、README、CLI、后端服务和 pytest 全部统一到项目规范的 `backend/.runtime/windows/python`，清除旧 editable install 或全局 `PYTHONPATH` 污染，并一次性安装 `.[web,database,dev]`。

通过条件：新环境不依赖 Anaconda，全量 `python -m pytest -q backend/tests` 可完成收集和执行。

## 2. 已实施的适配器改进

### 2.1 Claude 使用规范 CLI 模型别名

通用 LiteLLM Profile 的 Claude CLI 模型名由 `sonnet` 改为：

```text
claude-sonnet-4-6
```

网络层仍由每次运行的兼容代理强制改写为用户指定的 `opencode-go/<model>`，因此 CLI 身份和网关模型身份分离，不会退回 Anthropic 默认模型。

### 2.2 永久额度 429 快速失败

代理保留对瞬时 429、500、502、503、504 的有限重试，但以下错误不再重试：

```text
Weekly usage limit reached
usage limit reached
GoUsageLimitError
insufficient_quota
quota exceeded
```

这能避免 Agent 自身重试 × 本地代理重试造成请求放大，并缩短失败反馈时间。

### 2.3 trace key 清理成为显式验收字段

`check-agent` 结果现在包含：

```json
{
  "trace_key_alias": "agent-eval-...",
  "trace_key_cleanup": {
    "status": "deleted"
  }
}
```

原始 Key 不会写入结果。删除失败会令 connectivity 检查失败，而不是被静默忽略。

## 3. 改进模型发现语义

建议前端和 CLI 将模型状态拆成：

| 状态 | 含义 |
|---|---|
| `catalog_visible` | `/v1/models` 中存在 |
| `probe_healthy` | 最近一次最小推理成功 |
| `agent_certified` | 指定 Agent + trace + DB 精确核验成功 |
| `stable` | 最近 N 次满足稳定性阈值 |

默认可选模型列表应优先展示 `probe_healthy`，但保留查看失败模型及原因的入口。不得把目录中的 33 个模型全部标记为“可用”。

建议缓存健康探针 5–15 分钟并限制并发，避免前端刷新触发大量付费请求。

## 4. 完整复测命令

恢复至少一个模型后，从项目根目录运行：

```powershell
$env:PYTHONPATH="$PWD\backend\src;$PWD\backend"
D:\software\anaconda3\python.exe `
  backend/tests/live/run_opencode_agent_model_matrix.py `
  --profile litellm_opencode_go `
  --repeats 3 `
  --workers 3 `
  --timeout 120 `
  --preflight-timeout 45 `
  --output-dir tests_reports/<new-run-directory>
```

中断后可续跑：

```powershell
# 在上面的命令末尾添加：
--resume
```

只复测一个模型：

```powershell
D:\software\anaconda3\python.exe `
  backend/tests/live/run_opencode_agent_model_matrix.py `
  --profile litellm_opencode_go `
  --model opencode-go/<model-id> `
  --repeats 3 `
  --workers 3 `
  --timeout 120 `
  --output-dir tests_reports/<single-model-run>
```

## 5. 最终验收条件

每个目录中被标记为“可用”的 OpenCode 模型必须满足：

- 直接 `HI` 预检成功。
- 六 Agent 全部存在本机可执行文件且可启动。
- 18/18 通过：6 Agent × 3 次重复。
- 每次 Agent 只收到 `HI`。
- 每次至少一条成功 SpendLogs。
- trace alias 精确归因，数据库实际模型匹配指定模型。
- trace key 清理 100% 成功。
- 无 `no-thinking` 路由，无静默 fallback。
- 失败时能区分额度、区域、鉴权、Agent 启动、协议和数据库问题。

只有达到上述门槛，才能把对应模型标为“六 Agent 稳定可用”。
