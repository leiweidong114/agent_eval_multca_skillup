# 评测打分、并发与 Agent 接入契约

## 两层并发

当前项目没有启动 Multica Server 调度器，只复用了 Multica 的 Agent backend。并发由两层组成：

1. FastAPI 任务池：`AGENT_EVAL_WORKERS`，默认 2，可同时执行不同 Agent 的任务。
2. 单任务 Skill-Up 用例池：请求字段 `parallelism`，范围 1–16。

因此默认最多有 2 个顶层任务同时运行，而每个任务又可并发执行多条 case。总 Agent 进程数近似为 `AGENT_EVAL_WORKERS × parallelism`，还要乘 benchmark 的 with/without Skill 变体；生产配置必须按 CPU、内存、Agent CLI 限流和模型网关限流设定。`GET /api/capacity` 返回当前两层容量。

## Skill 安装与 eval.yaml 所有权

Skill 不需要预先安装到用户机器的全局 Agent 目录。评测器把 Skill 复制到每个任务的隔离 workspace，再由生成的 Skill-Up 配置安装到目标 Agent 的标准发现路径，例如 `.agents/skills` 或 `.claude/skills`。如果某 Agent 不支持目录发现，则必须新增该 Agent 的安装适配器；把 Skill 内容直接拼到 prompt 属于另一种实验，不能与“原生 Skill 使用能力”混为一谈。

用例作者负责 `evals/cases/*.yaml` 中的任务、断言和基准设计。平台的 `agent_eval.runner.build_eval_config` 根据 Agent、模型、case、并发和超时自动生成 `runs/<task_id>/staging/skill/evals/eval.yaml`。报告中的 `eval_config_file` 和 `eval_config_generated_by` 可追溯该配置。

## 标识与安全边界

每次服务端任务都有唯一 `task_id`，并保留兼容字段 `job_id`。调用方可传 `client_task_id` 关联自己的业务任务；`user_id` 用于列表过滤、报告归属和轨迹标记。

当前 `user_id` 是调用方声明的归属字段，不是身份认证。部署为多人服务时，API 网关必须完成认证，并用认证主体覆盖请求体中的 `user_id`；否则不能把该字段作为访问控制依据。

## 三维评分

报告的 `scoring` 同时保留规则分、LLM 分、权重、证据和最终分：

| 维度 | 默认总权重 | 规则证据 | LLM Judge 重点 |
|---|---:|---|---|
| 结果 | 50% | Skill-Up 断言通过率、相对无 Skill 的增益 | 正确性、完整性、是否真正满足任务 |
| 过程 | 30% | 执行稳定性、模型调用成功率、工具完成率、错误事件 | 工具选择、推理/执行效率、异常恢复 |
| Skill 质量 | 20% | SKILL.md 结构、元数据、引用文件和可执行性规则 | 指令清晰度、可复用性、边界与鲁棒性 |

默认维度内规则/LLM 比例写在 `config/scoring.yaml`。Judge 不可用时默认降级为纯规则分；设置 `required: true` 才会让 Judge 失败阻断整个任务。

默认 Judge 使用项目当前声明的最强 LiteLLM profile `litellm_deepseek_pro` 和 `deepseek-v4-pro`。可在不提交密钥的 `config/local.yaml` 覆盖：

```yaml
scoring:
  llm_judge:
    enabled: true
    required: false
    profile: my_judge_profile
    model: my-best-model
    timeout_seconds: 180
```

模型地址与密钥仍由 `config/models.yaml`、`config/local.yaml` 和 `LITELLM_API_KEY` 管理。LLM 返回值必须通过严格 JSON 校验，三维分数会被限制在 0–100；评测证据被当作不可信数据，避免 Skill 内容通过提示注入操纵 Judge。

## 可观测信息及限制

标准报告可记录：最终输出、耗时、请求模型、实际模型（可观察时）、输入/输出/cache token、工具调用与结果、错误、模型调用成功率、最大单次 prompt token。LiteLLM 网关是跨 Agent token/模型/上下文近似值最稳定的数据源。

以下信息不是所有 Agent 都能可靠提供：

- subagent：仅当 Agent 协议暴露对应工具事件时做 best-effort 统计；部分 Agent 会过滤内部子线程。
- 实际上下文窗口占用：统一接口没有该字段，当前以 LiteLLM 的 `max_prompt_tokens` 作为近似，而不是宣称获得完整上下文长度。
- thinking：只有 Agent CLI 主动输出结构化 thinking 事件才可获得。
- 工具事件：依赖适配器/CLI 协议；普通任务的 0 次工具调用不代表采集失败。

任何指标都必须同时报告值、来源和可用性，不能把缺失值当作 0。

## 新 Agent 上线门槛

新增 Agent 不能只把名字加入列表。合并前必须完成：

1. 连续多次 live case 成功，验证命令行调用稳定。
2. 显式指定模型，并在 Agent 返回或 LiteLLM trace 可观察时验证实际模型一致。
3. 运行强制工具调用的 telemetry probe，至少产出 final output、duration、input/output tokens、requested model、tool call/result。
4. 明确 actual model、subagent、context、cache、session ID 的支持状态和来源。
5. 通过统一契约测试，并保存一份真实 `evaluation-report.json` 作为认证证据。

支持的 backend、可指定模型、可注入 Skill、当前机器已安装、完成 live 认证是五种不同状态。
发现接口以 `capabilities` 明确返回前两项；只有
`specified_model_and_skill_evaluation=true` 且 `detected_executable` 非空，才具备开始在线矩阵
评测的前提。静态配置测试不能替代真实 CLI、凭据、模型轨迹和 Skill 断言结果。

`GET /api/agents` 返回每个 Agent 的 `evaluation_contract`。单次报告的 `agent_contract.run_contract_passed` 只表示本次基础字段齐全，不能替代上述 live 认证。
