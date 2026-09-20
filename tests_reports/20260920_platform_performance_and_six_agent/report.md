# 平台性能与六 Agent 原理图评测报告（2026-09-20）

## 结论

- 平台单元/集成测试与前端生产构建通过，六 Agent 批次能够并行调度、相互隔离、独立落盘和展示。
- 六个 Agent 的基础连接均已验证成功；Codex 在 `oc/nemotron-3.5-lightning` 下返回 `AGENT_SELF_TEST_OK`。
- 本次完整智能路灯评测为 **0/6 通过**。这不是批次调度失败：六项均完成执行、数据库追踪和评分。主要原因是当前模型没有完成四阶段 Skill 交付；Claude、JustDo 还受到上游 `api.furry.vg` Cloudflare Tunnel 1033/HTTP 530 影响。
- 第二轮候选模型无法启动：`oc/muse-spark-1.3-contributor` 返回 HTTP 530；`glm-4.5-air` 返回 HTTP 429（余额不足）。在上游恢复前不能给出“六 Agent 完整生成均通过”的结论。

## 测试环境

- Agent：Claude、CodeBuddy、Codex、JustDo、OpenClaw、OpenCode。
- 完整评测模型：`oc/nemotron-3.5-lightning`。
- 并行度：6；OpenClaw/JustDo 因共享桥接运行时使用互斥锁，其余 Agent 并行。
- 自动布局与网页应用服务：`http://127.0.0.1:8631`，三个健康检查均通过。
- 证据目录：`.runtime/verification/20260920-210139-schematic`。
- 正式报告：`backend/evaluation_results/local/run/20260920-210139__*`，可从“评测结果”页面查看。

## 六 Agent 结果

| Agent | 用时（秒） | 模型调用成功/总数 | Token | 断言通过 | 失败分类 |
|---|---:|---:|---:|---:|---|
| Claude | 2112.6 | 44/55 | 1,840,111 | 1/14 | `agent_execution_failed`，上游隧道 530 |
| CodeBuddy | 1518.7 | 24/25 | 603,978 | 1/14 | `agent_execution_failed` |
| Codex | 657.7 | 34/34 | 816,055 | 2/14 | `agent_output_invalid` |
| JustDo | 1903.2 | 8/16 | 上游未完整记录 | 2/14 | `agent_execution_failed`，上游隧道 530 |
| OpenClaw | 1086.1 | 26/26 | 266,743 | 0/14 | `agent_output_invalid` |
| OpenCode | 751.1 | 25/28 | 502,138 | 2/14 | `agent_output_invalid` |

所有运行的 `model_verification.verified` 均为 `true`。因此“是否调用指定模型”与“是否正确执行 Skill”已被正确区分。LLM Judge 在没有有效最终交付时按设计跳过，不消耗额外模型额度；规则诊断结果仍保留，但不参与排名。

## 新增过程断言与指标

`schematic-default` evaluator v3 对以下证据做结构化断言：

1. 四个选定 Skill 均被观察到。
2. `fetch_catalog.py`、`validate_sheets.py`、`render_sheets_markdown.py`、`codegen_base.py`、`layout_sheet.py`、`apply.py` 均有成功工具结果。
3. Subagent 至少有一次成功完成。
4. catalog、sheets、可读接口文档、代码切片、布局 JSON、apply_result 均存在。

网页新增显示断言完成度、脚本成功率、脚本重试/失败、真实流水线产物数量。工具返回消息成功与命令实际成功已分离：`Exit code: 1`、JSON 非零 `exit_code`、超时或 traceback 会计为语义失败。LiteLLM 请求/响应中的工具调用与工具结果也参与断言，不再只依赖 Agent 本地 transcript。

## 页面性能优化与实测

- 结果列表使用摘要接口，不下载每份完整报告：本机 157 条记录从约 **4.22 MB** 降到约 **103 KB**，减少约 **97.6%**。
- 已完成任务先请求轻量状态接口，再按需请求单份运行报告；活动任务才轮询完整 Job。
- 活动任务轮询间隔从 800 ms 调整为 1200 ms。
- 原理图总览进入页面不自动查询；用户点击“搜索”后才读取会话。
- 工号/模型筛选项仅在首次展开下拉框时加载，并缓存 300 秒。
- 历史指标会话列表缓存 60 秒。
- 原理图会话查询缓存命中实测约 7 ms；首页、结果中心和详情页均通过浏览器实测。

## 自动化验证

- 后端测试：258 项通过，仅有既有 FastAPI lifespan 弃用警告。
- evaluator/runner 定向测试：通过；新增了退出码语义失败、LiteLLM 工具证据、产物计数不受依赖文件污染的回归用例。
- 前端：Vite 生产构建通过。
- 自检：Python、Node、npm、Go、Multica、Skill-Up、PostgreSQL、模型目录、Agent 目录均通过；数据库包含 15,033 条 SpendLogs。
- UI：结果列表显示最新六条任务；详情显示模型交互、Token/耗时、流程断言和指标；原理图总览初始态不自动搜索。

## 当前外部阻塞与后续复验

平台代码不应把上游故障伪装成 Agent 失败。待模型恢复后，执行：

```powershell
.\backend\.runtime\windows\python\Scripts\python.exe .\test\run_smart_street_light_e2e.py `
  --agent claude --agent codebuddy --agent codex --agent justdo --agent openclaw --agent opencode `
  --model oc/muse-spark-1.3-contributor --timeout 3600 --max-turns 30 --workers 6 --benchmark
```

启动完整批次前必须先运行 `agent-eval models --refresh` 或 `agent-eval check-agent`。若基础探针返回 429/530，不应启动长任务。
