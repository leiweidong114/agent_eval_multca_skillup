# 六 Agent 原理图评测复测报告（2026-09-12）

## 结论

- 六个已安装 Agent 的基础调用与指定模型注入全部通过：`claude`、`codebuddy`、`codex`、`justdo`、`openclaw`、`opencode`，共 6/6。
- 每个基础调用都使用独立运行级虚拟 Key，并由 LiteLLM PostgreSQL 记录确认实际模型为 `openai/qwen3.8-flash-free`；成功模型调用率均为 100%。
- 原理图复杂任务不是 6/6 通过。OpenCode 首轮完整完成并通过严格产物验收；其余 Agent 或后续重复运行出现超时/未完成产物。这属于任务能力和运行稳定性差异，不能伪造成系统调用失败或成功。
- 后端全量测试通过，前端生产构建通过；复测结束时前端、后端和原理图服务健康端点均返回 HTTP 200。

## 使用的任务

模型：`qwen3.8-flash-free`

Prompt：

> 生成一个智能路灯的简易原理图：12V直流输入，经降压得到5V，ESP32读取光敏传感器并通过MOSFET控制LED路灯。必须严格使用本评测安装的四个原理图生成Skill，启动真实Subagent，依次完成接口表、原理图生成、自动布局和网页应用，最终返回可打开的原理图URL。

## 六 Agent 基础连通性

执行命令：

```powershell
agent-eval prompt `
  --agent claude --agent codebuddy --agent codex `
  --agent justdo --agent openclaw --agent opencode `
  --model qwen3.8-flash-free `
  --prompt "HI" --workers 6 --timeout 180 --database-verify
```

结果：6/6 `connected`。六个 Agent 的进程退出码和 Agent 退出码均为 0，数据库模型匹配均为 `verified`。JustDo 由评测系统直接拉起，无需用户预先启动桌面端。

## 首轮完整原理图批次

| Agent | 运行状态 | 指定模型验证 | 工具调用 | Subagent | 严格结果 | 主要原因 |
|---|---:|---:|---:|---:|---:|---|
| Claude | failed | verified | 100 | 1 | 25/100 | 900 秒超时 |
| CodeBuddy | failed | verified | 206 | 4 | 40/100 | 900 秒超时 |
| Codex | failed | verified | 24 | 0 | 20/100 | 提前停止，缺少原理图产物 |
| JustDo | failed | verified | 90 | 2 | 25/100 | 900 秒超时 |
| OpenClaw | failed | verified | 70 | 1 | 40/100 | 900 秒超时 |
| OpenCode | completed | verified | 92 | 5 | 100/100 | 全部严格检查通过 |

OpenCode 成功运行 ID：`217d722d3eba4e2d942dd0c65006c6af`。

成功产物 URL：`http://127.0.0.1:8631/static_schematic/6a4ab46eaa03/shell.html`。

## OpenCode 稳定服务重复运行

重复运行关闭了无 Skill 基线，将单轮超时提高到 1200 秒。结果为 `agent_timeout`：

- 30/30 次模型调用成功，模型归属验证通过；
- 64 次工具调用，工具失败数为 0；
- 检测到 2 次真实 Subagent；
- `sheets.json` 已通过，但 Agent 停在切片回收阶段，未产生布局、网页应用结果和最终 URL。

这说明同一 Agent/模型组合存在非确定性的 Subagent 编排稳定性问题；评测器正确拒绝了未完成结果。成功首轮证明端到端路径可运行，失败复测证明不能把“曾经成功”解释为“每次必定成功”。

## 本轮发现并修复的问题

1. Codex Chat Completions 适配器会把多个 system/developer 消息留在对话中间，部分上游拒绝请求。现已合并为唯一的首条 system 消息。
2. OpenClaw 新版本旧调用方式可能执行成功后不退出。现改用 `openclaw agent exec` 的稳定 JSON 协议；JustDo 保持自己的桥接协议。
3. CodeBuddy 在 Agent 超时断开后向关闭的 socket 写响应会输出 WinError 10053/10054 堆栈。现已将其作为正常超时清理处理。
4. 批次汇总曾把“LLM Judge 不可用导致不可排名”错误等同于“严格任务失败”，使严格验收 100 分的 OpenCode 被统计为 0/6。现已分离 `evaluation_passed` 与 `valid_for_ranking/diagnostic_only`。
5. 默认 Judge 原模型返回 HTTP 500。默认 Judge 已改为当日连通性验证可用的 `deepseek-v4-flash-vision-exp-free`，实际 Judge 请求已成功返回三维 JSON 分数。

## 尚需注意

- 原理图服务 `127.0.0.1:8631` 是当前项目外部依赖。首轮中该服务一度由 Agent 临时启动并随会话变化，影响了公平性。正式批量评测前必须让服务由独立服务管理器稳定托管，并先检查三个健康端点。
- 对拥有全盘文件读取权限的 Agent，无 Skill 基线可能搜索到项目源码中的 Skill，造成基线泄漏。只验证任务完成能力时应使用 `--no-benchmark`；需要评估 Skill 增益时，应在真正隔离的机器/容器中运行基线。
- “Agent 可正常调用”与“Agent 必定完成复杂原理图任务”是两个不同结论。前者本轮为 6/6；后者受 Agent 推理、Subagent 编排和时限影响，本轮只有一次 OpenCode 完整通过。

## 自动化验证

- 后端：`python -m pytest backend/tests -q`，全部通过。
- 前端：`vite build`，通过。
- HTTP：后端 `/api/health`、前端 `/`、原理图 `/api/health` 均返回 200。
