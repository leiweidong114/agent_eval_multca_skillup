# 2026-09-17 Java 数据接口、Linux 离线包与六 Agent 并行评测报告

## 结论

- Java/MongoDB 查询链路已具备后端代理接口，接口按原样返回 Java 服务的 JSON 响应；本机 `.env` 未配置 Java 服务地址，因此本次完成了单元/接口测试，没有伪造线上 Java 调用结果。
- Linux x64 离线包已在干净的 WSL Ubuntu 22.04 目录中完成离线安装、前端构建、Go 测试和后端全量测试，并发布到 GitHub Release `linux-runtime-20260917`。
- 六 Agent 确实同时启动且互相隔离；单个 Agent 失败没有取消其他任务，六份报告均独立落盘。因此并行调度、故障隔离、超时收敛、数据库模型核验和报告生成链路正常。
- 本次完整原理图评测通过率为 `0/6`，不能认定“六 Agent 并发稳定完成原理图任务”。共同瓶颈是 `qwen3.8-flash-free` 上游容量不稳定：总计 269 次数据库可追踪调用中 141 次成功，成功率仅 52.42%，大量失败为 `no_available_workers / all circuits open`。此外 Codex 与 JustDo 分别暴露了最终产物不完整和输出不可解析问题。
- 六个 Agent 均通过精确运行 key 验证，证明实际调用的是指定模型；这次失败不是模型没有注入。

## Java/MongoDB 查询接口

现有客户端位于 `backend/app/schematic_data_client.py`，根据根目录 `.env` 中以下配置调用 Java 服务：

```dotenv
SCHEMATIC_DATA_API_BASE_URL=http://<java-host>:<port>
SCHEMATIC_DATA_QUERY_PATH=/schematic/schematicData/query
```

新增后端接口：

```http
GET /api/schematic-data/query?collectionName=HDschematicRationalityCollection&page=1&size=20&refresh=true
```

路由位于 `backend/app/api/routes_schematic_data.py`。接口要求登录，将 `collectionName`、`page`、`size` 转发给 Java 服务，并将 Java JSON 响应原样返回；`refresh=true` 可跳过进程内 TTL/LRU 缓存。

## Linux 离线包

- Release: <https://github.com/leiweidong114/agent_eval_multca_skillup/releases/tag/linux-runtime-20260917>
- 文件：`agent-eval-runtime-linux-x64-portable.tar.gz`
- 大小：390,859,017 bytes
- SHA-256：`294e6e5384cc5d73509e9f0462a24778b5762aabdcba3b7270575a93648ea78a`
- 目标分支：`dev`

包内包含 Linux amd64 的 Go、Node.js、便携 Python、Python wheelhouse、npm 离线缓存、Skill-Up/Multica 源码、JustDo AppImage 和 JustDo Agent。包内不含 `.env`、密钥、数据库密码、数据库文件或历史评测结果。

## 六 Agent 并行评测配置

- 时间：2026-09-17 21:41:18 至 22:41:50（Asia/Shanghai）
- 模型：`qwen3.8-flash-free`
- Agent：Claude、CodeBuddy、Codex、JustDo、OpenClaw、OpenCode
- Worker：6
- 每个 Agent 并行度：1
- 每轮超时：1800 秒
- 对照：开启 `--benchmark`，每个 Agent 包含带 Skill 和无 Skill 基线
- 数据库轨迹：开启，并要求模型精确匹配
- LLM Judge：开启；由于六项执行结果均未形成有效交付，Judge 按规则跳过，未伪造 Judge 分数
- Skill：`schematic-pipeline`、`signal-interface-generation`、`schematic-layout-codegen`、`schematic-web-apply`

评测提示词：

> 请严格使用已安装的 schematic-pipeline、signal-interface-generation、schematic-layout-codegen 和 schematic-web-apply 四个 Skill，生成一个智能路灯的简易原理图：包含 12V 输入与保护、降压供电、MCU、环境光检测、LED 驱动和通信接口。必须真实执行 Skill 提供的脚本，生成信号接口列表、原理图工程、布局 JSON 和可访问的最终网页 URL，不要只用文字描述。

## 结果

| Agent | 总分 | 结果分 | 过程分 | 模型调用成功率 | 模型精确匹配 | Skill 证据 | 结果 |
|---|---:|---:|---:|---:|---|---|---|
| Claude | 40.41 | 10.00 | 51.36 | 36/44，81.82% | 通过 | 完整 | 超时 |
| CodeBuddy | 38.50 | 10.00 | 45.00 | 28/56，50.00% | 通过 | 完整 | 超时 |
| Codex | 58.34 | 20.00 | 94.47 | 34/47，72.34% | 通过 | 完整 | 必要产物不完整 |
| JustDo | 39.07 | 10.00 | 46.89 | 22/37，59.46% | 通过 | 完整 | Agent 输出不可解析/异常退出 |
| OpenClaw | 31.47 | 0.00 | 38.24 | 6/37，16.22% | 通过 | 部分，缺布局与网页 Skill | 上游 HTTP 500 |
| OpenCode | 37.38 | 10.00 | 41.25 | 15/48，31.25% | 通过 | 完整 | 超时 |

全部运行目录位于：

`backend/evaluation_results/batch-validation-20260917/6-Agent--20260917/`

## 问题归因与下一步

1. 评测系统：本次未发现批量调度、Agent 隔离、超时、模型核验、轨迹收集或报告落盘缺陷。六个任务同一秒启动，各自独立结束。
2. 模型服务：当前免费上游不适合承载六个长链路任务并发。应先在 LiteLLM 为评测模型配置有并发保障的上游、健康检查与备用模型，再复测；不能通过放宽评分或隐藏 HTTP 500 来制造通过结果。
3. Agent 执行：上游稳定后仍需重点复测 Codex 的产物验收项与 JustDo 的结构化输出。若在稳定模型下仍复现，应归类为 Agent/Skill 执行能力问题。
4. 超时：不建议先盲目增加 1800 秒。Claude、CodeBuddy、OpenCode 已消耗大量调用但没有完成交付，应先消除上游失败和重复重试，再判断任务本身是否需要更长时限。
5. Judge：只有 Agent 先形成有效结果，LLM Judge 才应运行。本次 `skipped_due_to_execution_failure` 符合设计。
