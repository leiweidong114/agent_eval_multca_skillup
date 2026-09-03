# OpenCode 模型 × 六 Agent HI 适配测试分析报告

- 测试日期：2026-09-03
- 项目路径：`D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup`
- 基础提交：`fac355003c78547ccbbcdef223743fd6e384e7b9`（测试期间包含本报告所列未提交适配修复）
- Profile：`litellm_opencode_go`
测试 Prompt：严格为 `HI`

> 注意：上面的基础提交完整值以 `matrix-manifest.json` 为准。本次报告与修复提交完成后应以新的 Git commit 重新运行认证矩阵。

## 1. 测试目标

验证当前 CC-Switch 风格模型适配器能否让以下六个本机 Agent 使用 LiteLLM 实时目录中的全部 `opencode-go/*` 模型：

- Claude Code
- CodeBuddy
- Codex
- JustDo
- OpenClaw
- OpenCode

每个在线 Agent 组合必须同时满足：

1. Agent 只收到一条用户消息 `HI`。
2. Agent 进程成功结束。
3. 每次运行创建独立 LiteLLM trace key。
4. PostgreSQL 按 trace alias 精确命中成功的 SpendLogs。
5. 数据库模型与指定模型一致。
6. 临时 trace key 成功删除。

## 2. 方法

测试分三层进行：

1. **目录发现**：调用 LiteLLM `/v1/models`，筛选 `opencode-go/*`。
2. **静态适配解析**：对模型 × 六 Agent 生成 requested/agent/gateway model、协议和 reasoning 配置，拒绝 `no-thinking`。
3. **在线验证**：先对每个目录模型直发最小 `HI`，只有 HTTP 成功的当前可推理模型才进入六 Agent × 模型数据库矩阵。

另使用 `opencode-go/minimax-m2.7` 对六 Agent 做了一轮真实 Agent、trace key 和数据库预检，以确认失败发生在哪一层。

## 3. 环境摘要

| 项目 | 结果 |
|---|---|
| LiteLLM 模型目录 | 可访问 |
| LiteLLM 模型总数 | 39 |
| `opencode-go/*` 模型数 | 33 |
| PostgreSQL | 正常，测试时 SpendLogs 记录数 2065 |
| trace key 创建/删除 | 正常 |
| Claude Code | 2.1.248 |
| CodeBuddy | 2.143.0 |
| Codex | 0.146.0 |
| OpenClaw | 2026.8.1 |
| OpenCode | 1.18.25 |
| JustDo launcher | 已发现，但桌面端未处于 launcher 可用状态 |

## 4. 总体结果

| 层级 | 通过 | 失败 | 结论 |
|---|---:|---:|---|
| LiteLLM 目录发现 | 33 | 0 | 33 个 OpenCode 模型在目录中可见 |
| 静态 Agent 模型适配 | 198 | 0 | 六 Agent × 33 模型均能生成结构上有效的适配配置 |
| 当前可推理模型预检 | 0 | 33 | 当前没有一个目录模型能完成最小 `HI` |
| 六 Agent MiniMax 2.7 在线预检 | 0 | 6 | 不能完成真实模型调用与数据库成功核验 |

结论：**当前不能证明适配器稳定支持六 Agent 使用 OpenCode 的全部模型。** 静态配置覆盖达到 198/198，但上游当前可推理模型集合为 0，真实全矩阵被前置可用性门禁正确阻止。

## 5. 33 个模型的在线预检结果

### 5.1 HTTP 429：每周额度耗尽（30 个）

上游统一返回 `Weekly usage limit reached` / `GoUsageLimitError`，并提示约 3 天后重置或启用可用余额。

受影响模型：

```text
opencode-go/deepseek-v4-flash-vision-exp
opencode-go/glm-5
opencode-go/glm-5.1
opencode-go/glm-5.2
opencode-go/glm-5.3
opencode-go/glm-5.3-flash
opencode-go/gpt-5.6-luna
opencode-go/grok-4.5
opencode-go/grok-4.6
opencode-go/hy3
opencode-go/hy3-preview
opencode-go/hy4-preview
opencode-go/kimi-k2.5
opencode-go/kimi-k2.6
opencode-go/kimi-k2.7-code
opencode-go/kimi-k3
opencode-go/longcat-2.0
opencode-go/mimo-v2-omni
opencode-go/mimo-v2-pro
opencode-go/mimo-v2.5
opencode-go/mimo-v2.5-pro
opencode-go/minimax-m2.5
opencode-go/minimax-m2.7
opencode-go/minimax-m3
opencode-go/qwen3.5-plus
opencode-go/qwen3.6-plus
opencode-go/qwen3.7-max
opencode-go/qwen3.7-plus
opencode-go/qwen3.8-flash
opencode-go/qwen3.8-max
```

这是账户/上游额度状态，不是本机 Agent 参数错误。目录可见不代表当前有推理额度。

### 5.2 HTTP 403：需要显式区域 opt-in（2 个）

```text
opencode-go/deepseek-v4-flash
opencode-go/deepseek-v4-pro
```

上游说明最新版仅在中国托管，需要在对应 OpenCode workspace 显式 opt-in。这属于账户侧设置；测试未修改。

### 5.3 HTTP 403：区域不可用（1 个）

```text
opencode-go/muse-spark-1.2-contributor
```

上游返回 `RegionError`，表示当前国家/地区不可用。

## 6. 六 Agent MiniMax 2.7 真实预检

| Agent | Agent 状态 | DB 行数 | DB 成功 | 模型核验 | trace 清理 | 主要现象 |
|---|---|---:|---:|---|---|---|
| Claude | failed | 16 | 0 | false | deleted | 超时；旧短别名 `sonnet` 被 CLI 标为不可识别；上游请求均失败 |
| CodeBuddy | failed | 16 | 0 | false | deleted | 超时；多层重试放大永久 429 |
| Codex | failed | 1 | 0 | false | deleted | 429 Too Many Requests |
| JustDo | failed | 0 | 0 | false | deleted | launcher 返回“JustDo is not running”，退出码 69 |
| OpenClaw | failed | 0 | 0 | false | deleted | 120 秒超时，未观察到 SpendLogs |
| OpenCode | failed | 8 | 0 | false | deleted | 明确返回 weekly usage limit |

汇总：

- 通过：0/6。
- trace key 创建并删除：6/6。
- PostgreSQL 精确命中：4/6。
- 精确命中的模型请求：41 条，成功 0 条。
- 模型核验成功：0/6。

数据库证明请求确实被路由到了目标 OpenCode 模型组，但所有请求都失败，因此不能把“DB 有记录”解释为“成功使用指定模型”。

## 7. 发现的问题

| ID | 严重度 | 问题 | 归属 |
|---|---:|---|---|
| OCM-001 | P0 | 30 个 OpenCode 模型共享的每周额度已耗尽 | 上游账户/额度 |
| OCM-002 | P1 | 两个 DeepSeek 模型需要中国托管 opt-in | 上游账户设置 |
| OCM-003 | P1 | Muse Spark 当前地区不可用 | 上游区域限制 |
| OCM-004 | P1 | Claude 通用 Profile 使用 `sonnet` 短别名，当前 CLI 不识别 | 本地适配器，已修复 |
| OCM-005 | P1 | 永久额度 429 被代理重复重试，单次 HI 被放大 | 本地弹性代理，已修复 |
| OCM-006 | P1 | JustDo 可执行文件存在，但桌面端/托盘服务未运行 | 本机运行状态 |
| OCM-007 | P1 | OpenClaw 超时且没有数据库调用 | 本地 Agent/适配器，需在模型恢复后复测 |
| OCM-008 | P2 | `/v1/models` 只反映目录配置，不反映实时额度和区域可用性 | 模型发现语义 |
| OCM-009 | P1 | 当前系统 Python 缺少 FastAPI，完整后端 pytest 在收集 3 个 Web 测试模块时中止 | 本机测试运行时/依赖 |
| OCM-010 | P1 | 当前 Python 的导入路径曾优先指向另一个 `agent_eval_multca_skillup_win_offline` 副本 | 本机 Python 路径污染 |

## 8. 本次已实施的非鉴权修复

1. `check-agent` 增加 `--prompt`，测试能够严格发送 `HI`。
2. connectivity 结果新增 trace alias 和 trace key 清理状态。
3. Claude LiteLLM 通用默认模型别名改为 `claude-sonnet-4-6`。
4. OpenCode Profile 的 Claude 别名同步改为规范名称。
5. 兼容代理识别 weekly usage limit、GoUsageLimitError、insufficient quota 等永久 429，不再进行无效重试。
6. 增加永久 429 不重试和自定义 `HI` 的回归测试。
7. 增加可恢复、可并发、带断点结果文件的完整在线矩阵脚本。

显式将 `PYTHONPATH` 绑定到当前仓库 `backend/src` 后，相关离线回归 30 项通过。若不绑定，当前 Python 会错误导入另一个离线副本，OCM-010 需要随统一运行时一并消除。

修复后另对 Claude、CodeBuddy 使用同一不可用 MiniMax 2.7 做了在线回归：

- Claude 不再出现 `unrecognized_model: sonnet`。
- 两个 Agent 的单项数据库失败记录均由 16 条降为 4 条，证明代理层的四倍重试放大已经移除。
- 剩余重试来自 Agent 客户端自身；由于上游额度仍耗尽，两项继续失败是正确结果。
- 两项 trace key 均成功删除。

完整 `backend/tests` 也已尝试执行，但当前 `D:\software\anaconda3\python.exe` 缺少 FastAPI，导致 `test_api.py`、`test_model_eval_integration.py` 和 `test_schematic.py` 在收集阶段报错。本次没有向全局 Python 安装依赖；应先统一并修复项目隔离运行时，再执行全量回归。

## 9. 证据文件

- `model-preflight.json`：33 个模型的实时 `HI` 预检。
- `adapter-resolution.json`：198 个静态模型适配组合。
- `matrix-manifest.json`：模型、Agent、Profile、参数和提交信息。
- `matrix-results.json/csv`：当前可推理模型的 Agent 矩阵；本次因 0 个模型通过预检而为空。
- `matrix-summary.md`：自动摘要。
- `../20260903_opencode_matrix_smoke/`：MiniMax 2.7 × 六 Agent 的真实数据库预检证据。
- `../20260903_opencode_matrix_postfix_smoke/`：Claude/CodeBuddy 修复后在线回归证据。

原始文件可能包含上游 workspace 标识等诊断字段，只应保存在受控测试环境；正式对外报告应使用本报告的脱敏摘要。

## 10. 测试限制

- 因 33/33 模型当前均不能推理，无法完成“每个模型 × 六 Agent”的动态认证，更无法证明稳定性。
- MiniMax 2.7 的六 Agent 测试主要证明 trace 和失败归因链路正常，不能证明恢复额度后的协议兼容性。
- Claude 别名和永久 429 快速失败修复当前只能做离线回归；必须在至少一个 OpenCode 模型恢复后做在线回归。
- 一次成功不足以证明稳定；正式认证应对每个组合至少重复 3 次。
