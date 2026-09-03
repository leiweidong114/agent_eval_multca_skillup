# Agent Eval Multica Skill-Up 全系统测试与验证方案

版本：1.0  
日期：2026-09-03  
唯一项目路径：`D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup`

## 1. 目标

本方案用于判断项目是否真正满足以下目标，并通过“发现缺陷 → 修复 → 回归 → 留存证据”的闭环持续改进：

1. CLI、FastAPI 后端和 Vue 前端均可独立启动、组合运行和正确报错。
2. 本机重点六 Agent（Claude Code、CodeBuddy、Codex、JustDo、OpenClaw、OpenCode）可使用评测任务明确指定的模型完成任务。
3. 不允许通过关闭模型 reasoning/思考能力解决协议兼容问题，不允许静默切换模型或回退到 Agent 账号默认模型。
4. 可采集并展示 Agent 的最终结果、消息、工具调用、工具结果、subagent、token、耗时、错误和实际模型证据。
5. 可通过 LiteLLM 运行级 trace key 和 PostgreSQL `LiteLLM_SpendLogs` 精确验证每次运行的模型调用。
6. 可对结果、执行过程和 Skill 质量进行确定性评分与 LLM Judge 评分，并生成可追溯报告。
7. 可对正在开发的新原理图生成 Skill 进行有 Skill/无 Skill 对照、结构化产物校验、拓扑评分和六 Agent 全流程评测。

本方案中的“通过”必须有机器可读证据。Agent 名称存在于支持列表、本机找到可执行文件、接口返回 200、模型目录中显示可用，都不能单独证明一次真实评测成功。

## 2. 范围和术语

### 2.1 本轮强制验收范围

| 层级 | 范围 |
|---|---|
| CLI | `doctor`、`agents`、`check-agent`、`run`，以及待补齐的 `models`、`batch`、`quickstart` |
| 后端 | FastAPI 健康、发现、Skill、运行、批次、历史结果、原理图、数据库、Prism 题库接口 |
| 前端 | 首页、新建评测、原理图、题库、Skill、结果详情、模型与 Agent 页面 |
| Agent | Claude Code、CodeBuddy、Codex、JustDo、OpenClaw、OpenCode |
| 模型 | 首轮使用 `litellm_glm_4_7 / glm-4.7`；后续对任意 Profile 复用同一套测试 |
| 数据 | LiteLLM 模型发现、trace key 生命周期、PostgreSQL 精确归因、报告落盘 |
| 评分 | 结果 50%、过程 30%、Skill 质量 20%；规则评分 + LLM Judge |
| Skill | `example-marker` 仅作管线烟雾测试；新原理图 Skill 作为真实质量验收对象 |

### 2.2 三种 Agent 状态必须分开

1. **静态支持**：Multica/项目中存在该 Agent 适配器。
2. **本机可运行**：能够发现并启动该 Agent 的真实可执行文件。
3. **已认证**：在指定 Profile、指定模型、指定 Skill 下完成真实任务，模型精确核验、评分和报告全部通过。

前端、CLI 和报告不得把前两种状态显示成“已通过评测”。

### 2.3 模型身份必须分层记录

| 字段 | 含义 | 示例 |
|---|---|---|
| `requested_model` | 用户选择的公共模型 | `glm-4.7` |
| `profile` | 路由和适配配置 | `litellm_glm_4_7` |
| `agent_model` | 传给 Agent CLI 的模型名 | `claude-sonnet-4-6` |
| `gateway_model` | 发给 LiteLLM 的 deployment 名 | `glm-4.7-anthropic` |
| `actual_model` | SpendLogs/响应证明的实际模型 | 由数据库返回 |

别名不同可以接受，但必须存在显式、可测试的确定性映射；任何未声明回退均判为失败。

## 3. 验收原则

### 3.1 禁止假阳性

- Agent 执行失败时不得进入有效排名。
- trace key 未创建、数据库无精确命中或实际模型不匹配时，严格模式必须在模型调用前失败关闭。
- Judge 不可用时不得伪造 Judge 分数；发布验收批次要求 Judge 状态为 `completed`。
- token、工具、subagent 为 0 时必须区分“确实未发生”“Agent 不支持”和“采集缺失”。
- 不能使用固定 marker 的偶然命中证明 Skill 有效。

### 3.2 不关闭模型推理

- 禁止使用名称或参数包含 `no-thinking`、`disable_reasoning` 等语义的生产验收路由。
- 对支持 reasoning 的模型，适配器必须分别处理 reasoning、tool call、tool result 和 final answer。
- 可以只展示 reasoning 状态、token 和耗时，不要求保存或展示模型完整内部思维链。
- OpenCode 的 `glm-4.7-no-thinking` 当前配置属于待修复项；在移除前不得宣称六 Agent 完整通过。

### 3.3 可重复、可追踪

- 每个测试记录 Git commit、Agent/CLI 版本、Profile 指纹、用例版本、随机种子、开始/结束时间和主机信息。
- 所有在线运行使用唯一 `run_id`；六 Agent/多模型任务使用唯一 `batch_id`。
- 测试结果不得覆盖，修复前后报告必须同时保留。

### 3.4 鉴权边界

- 默认只验证现有权限，不修改 Master Key、Virtual Key、数据库用户或上游模型权限。
- 遇到 401/403 时记录所需权限、影响和最小变更建议，必须取得用户明确授权后才能修改鉴权。
- 日志、截图、报告和 API 响应不得包含原始 Key、数据库密码或 Authorization header。

## 4. 当前已知不足基线

以下条目必须在正式六 Agent 验收前重新确认并关闭：

| ID | 严重度 | 当前不足 | 验证/关闭条件 |
|---|---:|---|---|
| GAP-001 | P0 | OpenCode 的 GLM Profile 仍指向 `glm-4.7-no-thinking` | 改为保留 reasoning 的路由，并通过 reasoning + tool + final 协议回归 |
| GAP-002 | P0 | 历史最新六 Agent 批次不是稳定 6/6；JustDo 存在随机性 | 同一版本下完成稳定性门槛，不拼接不同批次的单项成功记录冒充 6/6 |
| GAP-003 | P0 | 当前运行中的 `/api/agents`、`/api/models` 实测返回 HTTP 502 | 清理端口/进程状态后，真实后端和前端代理均返回正确结果 |
| GAP-004 | P1 | README/安装脚本约定 `backend/.runtime`，当前机器主要存在根目录 `.runtime` | 统一运行时位置，`doctor`、README、脚本、服务进程和 CI 使用同一路径 |
| GAP-005 | P1 | 隔离 Python 曾缺少 `httpx`，说明环境可能依赖系统 Python 兜底 | 新机器只执行 setup 后即可运行 CLI、后端和测试，不依赖 Anaconda 隐式兜底 |
| GAP-006 | P1 | CLI 没有 `models`、六 Agent `batch` 和真正的一键 `quickstart` | 增加命令、帮助、退出码、JSON 输出和自动化测试 |
| GAP-007 | P1 | 前端 `package.json` 只有 build/dev/preview，没有单元或 E2E 测试脚本 | 增加 Vitest（组件）与 Playwright（真实浏览器）测试和 CI 命令 |
| GAP-008 | P1 | `/api/models` 可能向普通用户暴露 `*-anthropic`、`*-no-thinking` 等内部路由 | 默认仅展示公共模型；高级诊断中明确标注内部路由，不允许误选 |
| GAP-009 | P1 | `example-marker` 区分度低，曾出现无 Skill 对照也成功 | 替换为不可猜测的结构化任务，证明有 Skill 组对结果有显著增益 |
| GAP-010 | P1 | 现有运行 subagent 多为 0，尚未证明 subagent 采集链完整 | 增加强制 subagent 用例；支持者必须采集，不支持者明确返回 capability |
| GAP-011 | P1 | `llm_judge.required` 当前为 `false` | 诊断运行可选；发布验收模式必须要求 Judge 成功，否则批次不能认证 |
| GAP-012 | P2 | 同一个 GLM 既执行任务又 Judge，存在自评偏差 | 建立人工金标集，校准 Judge；正式比较优先使用独立、更强或多 Judge 组合 |
| GAP-013 | P2 | Windows 评测产物出现超长路径告警 | 缩短归档层级或启用长路径策略，并加入最大深度回归 |

## 5. 测试环境与冻结清单

正式测试前生成 `environment-manifest.json`，至少包含：

- Git commit、分支和工作树状态。
- Windows/Linux 版本、CPU、内存、磁盘余量。
- Python、Node、npm、Go、Skill-Up、Multica 版本。
- 六个 Agent 的版本、可执行文件路径和文件 SHA256。
- `models.yaml`、`scoring.yaml`、目标 Skill 的目录指纹。
- LiteLLM base URL、健康状态和模型目录摘要；不记录 Key。
- PostgreSQL server 版本、只读用户状态、时区和 SpendLogs 可访问性。
- 前后端监听地址、`AGENT_EVAL_WORKERS`、默认超时和并发配置。

在一次认证批次执行期间不得升级 Agent、修改 Profile、修改 Skill 或改变评分配置；变化后必须生成新批次。

## 6. 分阶段验证流程

### 阶段 A：仓库与安装可重复性

| 编号 | 测试 | 方法 | 通过条件 |
|---|---|---|---|
| A-01 | Git 一致性 | 比较 HEAD、`origin/dev`、受控文件和未提交更改 | 代码与目标提交一致；本地配置/产物被明确列出 |
| A-02 | 全新安装 | 在临时目录克隆并运行 Windows/Linux setup | 不使用全局 Anaconda/Go 即可完成安装 |
| A-03 | 运行时路径 | 检查 setup、doctor、README、服务启动脚本 | 全部引用同一个规范路径 |
| A-04 | 依赖完整性 | 在隔离 Python 导入 `httpx`、FastAPI、psycopg 等 | 所有必需依赖存在，版本满足约束 |
| A-05 | Secret 防泄漏 | 扫描 Git、构建产物、日志和报告 | 无明文 Key、密码或 Authorization header |
| A-06 | Windows 长路径 | 运行最长归档组合 | 无无法扫描、清理或读取的路径 |

建议命令：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/dev
Set-Location backend
.\scripts\setup_windows.ps1
.\.runtime\windows\python\Scripts\agent-eval.exe doctor
```

如果最终决定运行时位于项目根目录，应同时修改脚本与文档，不能继续保留两套路径。

### 阶段 B：离线单元测试与静态契约

覆盖以下模块：

- Profile 合并、模型别名、协议选择和 Secret 解析。
- Agent capability 与 evaluation contract。
- trace key 创建、重试、403、429、删除和脱敏。
- 数据库查询、精确 alias 过滤、重试和内容隐私。
- 失败分类：额度耗尽、限流、鉴权、授权、模型不兼容、网络、Agent、Judge。
- Runner 的取消、超时、清理、报告 schema 和失败关闭。
- Job Manager 的队列、批次、并发、重启恢复和排名排除。
- Skill 上传、版本、ZIP Slip/路径穿越、大小限制和保留策略。
- 原理图 pipeline、专项 Judge、API 与产物契约。
- Prism 题库导入、Provider、实验、结果和安全代码开关。

执行：

```powershell
Set-Location backend
python -m pytest -q
```

门槛：0 failed、0 error；任何 skip 必须有负责人和原因。覆盖率首轮目标 80%，核心模块 `runner.py`、`model_config.py`、`database.py`、`litellm_trace.py`、评分模块目标 90%。

### 阶段 C：CLI 验证

#### C.1 当前命令

| 命令 | 正常测试 | 失败测试 |
|---|---|---|
| `doctor` | 完整运行时返回 0 | 缺二进制/依赖时非 0，并指出路径和修复方式 |
| `agents` | 列出支持、本机发现、能力、认证状态 | 不得把未安装 Agent 标成可用 |
| `check-agent` | 指定 Agent/Profile 真实返回探针 | 401/403/429/模型不存在必须准确分类 |
| `run` | 完成 Skill、模型核验、Judge 和报告 | 缺 Skill、坏用例、超时、取消、DB 不可用均有稳定退出码 |

#### C.2 必须补齐的目标命令

```text
agent-eval models [--profile NAME] [--json]
agent-eval batch --agents all --profile NAME --skill PATH [--workers N]
agent-eval quickstart --agents core-six --profile NAME --skill PATH
```

要求：

- 所有命令支持适合自动化的 JSON 输出。
- stdout 只输出结果，诊断写 stderr。
- 参数错误退出 2，环境/配置错误使用稳定非零退出码，任务失败与基础设施失败可区分。
- CLI 与 API 对相同请求生成一致的规范化配置和报告 schema。

### 阶段 D：后端 API 契约与生命周期

至少验证：

1. `/api/health`、`/api/agents`、`/api/models`、`/api/model-config`、`/api/database/health`。
2. Skill 列表、内容、用例、上传、版本和文件读取。
3. `/api/validate` 不发起模型调用、不创建正式结果。
4. `/api/run` 返回 job ID，状态按 `queued → running → terminal` 单向变化。
5. `/api/batches` 正确展开 Agent × 模型组合，不丢任务、不重复任务。
6. 取消 queued/running/terminal 任务的幂等性。
7. 服务重启后 running 任务变为 `interrupted`，历史结果仍可读取。
8. `/api/runs/{id}`、批次详情、原理图和 Prism 接口 schema 稳定。
9. 错误响应包含 `category`、用户可读原因、原始 HTTP 状态、是否可重试和建议动作，但不泄漏 Secret。

API 契约建议导出 OpenAPI 快照并做 breaking-change diff。

### 阶段 E：前端组件、E2E 与可观测性

先增加：

```text
npm run test:unit
npm run test:e2e
npm run build
```

真实浏览器 E2E 场景：

1. 首页加载，不出现 console error、未处理 Promise 或 5xx。
2. “模型与 Agent”页显示公共模型、本机 Agent、能力和测试状态。
3. 创建单 Agent 评测，观察进度、取消并打开结果。
4. 创建六 Agent 批次，可配置顶层 worker 数/执行策略并查看排队顺序。
5. 结果页显示 requested/agent/gateway/actual model、Profile、trace 状态、token、工具、subagent、Judge 和失败原因。
6. 原理图页面加载工程、显示组件/引脚/连线，并能打开专项评分详情。
7. 401、403、429、5xx、DB 断开、Judge 不可用时显示准确中文错误，不显示笼统“运行失败”。
8. 刷新页面或重启后端后任务和结果仍可恢复。
9. 1280×720、1920×1080 和高 DPI 下无关键字段遮挡。
10. 所有 Secret 输入框不回显已有 Key，网络响应不包含 Key。

每个 E2E 保存截图、浏览器 console、网络摘要和关联 job/batch ID。

### 阶段 F：LiteLLM、trace key 与数据库精确归因

#### F.1 trace key 生命周期

1. 使用现有 Master Key 调用 `/key/generate`。
2. alias 必须唯一且绑定 `run_id`，Key 只允许目标模型，有有限有效期。
3. 使用子 Key 调用 `/v1/models` 和一次最小模型推理。
4. 确认非零 token 或明确的 provider usage。
5. 查询 PostgreSQL，按 alias 精确命中本次请求。
6. 运行结束调用 `/key/delete`。
7. 删除后再次调用必须被拒绝；报告不得包含原始 Key。

当前已单独验证“创建 → `/v1/models` HTTP 200 → 删除”成功；仍需把真实推理、SpendLogs 精确命中和删除后拒绝纳入自动化集成测试。

#### F.2 并发隔离

同时创建至少 6 个运行：

- 每个运行具有不同 alias。
- 每个运行只命中自己的 SpendLogs。
- token、模型、费用和错误不能串到其他 Agent。
- Judge 调用应标注为 Judge，不能误记为被评 Agent 请求。

#### F.3 数据库读取

- 使用只读数据库用户。
- 测试无记录、多记录、延迟写入、重复请求、时区偏差和 PostgreSQL 暂时断连。
- 默认不读取或展示原始 prompt/response；需要读取内容时必须单独授权并脱敏。
- 数据库不可用且严格核验开启时必须失败关闭，不能改用不精确时间窗口后仍标为 verified。

### 阶段 G：指定模型与 reasoning 协议验证

每个 Profile 必须通过以下协议测试：

1. `/v1/models` 可发现公共模型。
2. OpenAI Chat Completions 最小请求。
3. OpenAI Responses 最小请求。
4. Anthropic Messages 最小请求。
5. 普通文本 final answer。
6. reasoning + final answer。
7. reasoning + tool call + tool result + final answer。
8. 多轮工具调用、流式增量、Unicode 和长输出。
9. 上游只返回 reasoning、缺 final、流中断、JSON 损坏时必须准确失败。
10. SpendLogs 的 actual model 与映射表一致。

任何 Agent 若只能通过 `no-thinking` 路由完成测试，该 Agent/Profile 组合判定为不兼容，而不是通过。

### 阶段 H：重点六 Agent 认证矩阵

| Agent | 可执行文件 | 指定模型 | Skill 注入 | reasoning/final | 工具轨迹 | subagent 能力 | DB 精确核验 | Judge | 报告 |
|---|---|---|---|---|---|---|---|---|---|
| Claude Code | 待运行填充 | 必测 | 必测 | 必测 | 必测 | 能力感知 | 必测 | 必测 | 必测 |
| CodeBuddy | 待运行填充 | 必测 | 必测 | 必测 | 必测 | 能力感知 | 必测 | 必测 | 必测 |
| Codex | 待运行填充 | 必测 | 必测 | 必测 | 必测 | 能力感知 | 必测 | 必测 | 必测 |
| JustDo | 待运行填充 | 必测 | 必测 | 必测 | 必测 | 能力感知 | 必测 | 必测 | 必测 |
| OpenClaw | 待运行填充 | 必测 | 必测 | 必测 | 必测 | 能力感知 | 必测 | 必测 | 必测 |
| OpenCode | 待运行填充 | 必测 | 必测 | 必测 | 必测 | 能力感知 | 必测 | 必测 | 必测 |

每个 Agent 至少运行以下任务：

1. `CONNECTIVITY`：最小推理，只验证真实模型连通性。
2. `SKILL-DISCRIMINATION`：随机化输入的有 Skill/无 Skill 对照。
3. `TOOL-TRACE`：必须读取文件、执行命令并写出产物。
4. `SUBAGENT-TRACE`：对声明支持 subagent 的 Agent 强制派生子任务；不支持者返回明确 capability。
5. `ERROR-RECOVERY`：制造一次可恢复工具错误，验证轨迹与最终恢复。
6. `SCHEMATIC-E2E`：执行新原理图 Skill 并接受专项 Judge。

稳定性门槛：

- 基础设施成功率：每个 Agent 连续 5 次必须 5/5，无鉴权、路由、适配器或报告失败。
- 指定模型精确核验：100%。
- Judge 完成率：100%。
- Skill 主任务质量通过率：至少 4/5；失败必须归因到模型行为而不是基础设施。
- 六 Agent 正式批次必须来自同一 commit、同一 Profile 指纹、同一用例版本，不能用历史不同批次的单项成功拼接。

### 阶段 I：评分系统正确性

建立人工金标集，至少包含：

- 完全正确结果。
- 表面格式正确但内容错误。
- 最终答案正确但过程有工具错误。
- 使用错误模型得到正确答案。
- Skill 未生效但偶然猜中结果。
- Agent 失败、Judge 失败、数据库失败。
- 工具和 subagent 证据缺失。

验证：

1. 结果、过程、Skill 质量权重计算与 `scoring.yaml` 一致。
2. 失败任务不产生可比较的有效总分。
3. LLM Judge 输入只包含允许的证据，输出满足 schema，解析失败不会使用默认高分。
4. 同一金标重复 Judge 的方差在阈值内。
5. Judge 与人工排序的 Spearman 相关性目标 ≥ 0.8；严重错误样本不得给高分。
6. 对同模型自评与独立 Judge 结果做偏差比较。
7. 修改正确产物中的组件、引脚、网络或 manifest 后，专项分数应按预期下降（mutation testing）。

### 阶段 J：并发、队列与资源控制

分别设置 `AGENT_EVAL_WORKERS=1/2/6`，任务内 `parallelism=1/2/4`：

- worker=1 时验证明确的 FIFO 排队语义；如果 ThreadPoolExecutor 的实现顺序不作为契约，应在文档和 UI 明示。
- worker>1 时验证最大并发不超限。
- Agent × model 批次展开顺序、优先级和用户可配置策略正确。
- 验证取消排队任务、取消运行任务、单任务超时、整个批次超时。
- 记录 CPU、内存、进程数、临时目录和端口，结束后无孤儿进程。
- 并发 trace key、工作区、Agent 配置、输出目录完全隔离。

建议目标调度选项：

```text
strategy: fifo | agent-major | model-major | round-robin
workers: 1..N
per_job_parallelism: 1..16
```

### 阶段 K：故障注入

至少覆盖：

| 故障 | 期望行为 |
|---|---|
| 401 | “模型服务鉴权失败”，不得建议盲目重试 |
| 403 | 区分 trace key 管理权限与模型调用权限；鉴权修改必须请求用户授权 |
| 429 quota | “模型额度已达上限”，展示 provider/request ID（若安全） |
| 429 rate limit | 遵守 `Retry-After`，重试耗尽后准确分类 |
| 500/502/503/504 | 有限重试、指数退避，不无限挂起 |
| DNS/连接超时 | 可取消，清理子进程和临时 Key |
| malformed stream | 不把 reasoning 片段误当 final |
| 数据库断开 | 严格模式失败关闭；诊断模式标为 unverified |
| Judge 不可用 | 发布验收失败；普通诊断保留规则分并明确非排名结果 |
| trace 删除失败 | 报告 cleanup failure，并触发受控清理任务 |
| Agent 崩溃/退出码非 0 | 保存 stderr、退出码和最后阶段 |
| 磁盘不足/超长路径 | 不产生半成品成功报告 |
| 后端重启 | 任务标记 interrupted，历史数据可读取 |

### 阶段 L：报告、隐私与可迁移性

每次正式运行至少生成：

```text
environment-manifest.json
request.json
evaluation-report.json
model-interactions.json
agent-transcript.json
judge-report.json
failure.json（失败时）
report.html
junit.xml
```

报告必须能从 UI、API 和磁盘三种入口读取，并包含 schema version。执行数据保留/清理时先 preview，只有显式确认才能删除。

Windows 和 Linux 迁移分别测试：只复制仓库代码和非敏感配置模板，在目标系统重新运行 setup；不得直接复制平台相关虚拟环境和二进制冒充完成部署。

## 7. 新原理图生成 Skill 专项方案

### 7.1 Skill 进入评测前的最小契约

新 Skill 目录至少包含：

```text
<skill>/
├── SKILL.md
├── evals/cases/
├── references/
├── assets/
└── scripts/（如果需要确定性校验器）
```

`SKILL.md` 必须明确：触发条件、输入格式、步骤、允许工具、禁止行为、输出文件、失败处理和验证方法。Skill 的指令不得包含测试答案或金标数据。

### 7.2 用例分层

| 等级 | 内容 | 建议数量 |
|---|---|---:|
| S0 | 最小合法输入、单组件、单网络 | 3 |
| S1 | 多组件、公共/私有 CBB、不同引脚方向 | 5 |
| S2 | 扇入/扇出、多电源域、差分/总线、重复网络名风险 | 8 |
| S3 | 缺引脚、悬空引用、重复 ID、非法方向、环路等错误输入 | 8 |
| S4 | 大规模真实工程与性能压力 | 3 |
| S5 | 隐藏测试与对抗变体，防止针对样例过拟合 | 5 |

至少 30 个用例，其中公开开发集与隐藏验收集分离。

### 7.3 确定性专项 Judge

优先以输入真值和 JSON/网表结构评分，不只依赖 LLM：

- Schema 与必需文件完整性。
- 组件 ID、类型、数量和属性保持。
- 引脚 ID、名称、方向和所属组件保持。
- 连线端点与网络拓扑正确。
- 网络名、电源网、总线/差分关系正确。
- 公共/私有 CBB 分类正确。
- 无虚构引脚、无静默丢线、无重复/悬空引用。
- manifest、events 和组件级产物相互一致。
- 如果版图坐标属于目标，再增加可读性、重叠、交叉和页面边界规则；否则坐标不进入拓扑主分。

对 Judge 做 mutation testing：从一个 100 分金标产物中分别删除连线、交换引脚、改网络名、重复组件、破坏 manifest，确认对应维度稳定扣分。

### 7.4 有 Skill/无 Skill 对照

每个 Agent、每个用例都运行：

```text
with_skill    = 安装并明确允许使用新 Skill
without_skill = 相同模型、Prompt、预算和工具，但不安装该 Skill
```

输入中使用随机化且可验证的组件/网络标识，避免模型凭固定示例猜中。报告同时给出绝对质量和 Skill lift：

```text
skill_lift = with_skill_score - without_skill_score
```

验收建议：with-skill 平均专项分 ≥ 85，关键拓扑错误率为 0，且相对 without-skill 的平均提升 ≥ 15 分。最终阈值应根据首轮人工标注校准。

### 7.5 六 Agent 原理图全流程

每次运行必须证明：

1. Agent 真实读取新 Skill，而不是仅收到摘要提示。
2. 实际调用指定模型且 reasoning 未关闭。
3. 必需工具调用及结果被采集。
4. 若设计包含组件并行子任务，支持 subagent 的 Agent 必须留下父子关系和子任务结果。
5. 输出所有契约文件并可被前端加载。
6. 确定性 Judge、LLM Judge 和最终综合分完整。
7. trace key 与 PostgreSQL 精确核验通过。
8. HTML/JSON/JUnit 报告可复现本次结论。

### 7.6 当前四 Skill 草稿的专项检查

当前工作区已经出现以下四个未提交 Skill。本方案把它们视为一条组合流水线，但在作者确认前不修改其内容：

| Skill | 关键验证点 |
|---|---|
| `signal-interface-generation` | 自然语言到 `sheets.json` 的完整性；器件库一致性；网络只正向定义一次；`validate_sheets.py` 对缺引脚、重复连接、未知器件的拒绝能力 |
| `schematic-layout-codegen` | `sheets.json` 契约兼容；每批恰好最多 2 个 subagent；所有连接片段确由 subagent 生成；Python AST 校验；`auto_layout` 超时、重试、幂等和布局 JSON schema |
| `schematic-web-apply` | 多布局一图页；左侧切换；`apply_result.json` 和 URL schema；URL 可访问、生命周期、重复 apply 和部分失败行为 |
| `schematic-pipeline` | 三个子 Skill 的发现与版本锁定；跨 Skill 路径传递；失败停止/恢复点；总 manifest；最终 URL 与全部中间产物可追溯 |

组合评测需要额外断言：

- 父 Agent 不能自己伪造步骤二结果来绕过强制 subagent。
- 轨迹中应出现每批最多 2 个并行子任务，并能将每个代码片段关联到对应 subagent。
- 某一 sheet 失败时，不得把缺页网页标为完整成功；报告要指出失败 sheet 和阶段。
- `fetch_catalog.py`、`auto_layout` 和 `apply_schematic` 的外部响应应保存脱敏摘要和版本标识。
- 测试环境应提供确定性的 mock 服务；真实服务另做 live contract test，避免服务波动污染 Skill 质量分。

## 8. 缺陷修复闭环

每个失败必须创建结构化记录：

```yaml
id: DEFECT-YYYYMMDD-NNN
severity: P0|P1|P2|P3
component: cli|frontend|backend|agent|adapter|litellm|database|judge|skill
run_id: ...
batch_id: ...
expected: ...
actual: ...
reproduction: ...
evidence_paths: []
root_cause: ...
fix_commit: ...
regression_tests: []
status: open|fixed|verified|deferred
```

严重度：

- P0：错误模型、关闭 reasoning、Secret 泄漏、假阳性评分、数据损坏、六 Agent 主链不可运行。
- P1：单 Agent/CLI/API/前端核心功能不可用、轨迹或数据库证据缺失。
- P2：边界场景、性能、可用性或错误提示问题。
- P3：文档和视觉优化。

修复后必须先运行最小复现，再运行所属模块测试，最后重跑完整六 Agent 认证批次。只修复报告文本而未修复根因不能关闭缺陷。

## 9. 推荐执行顺序

```text
冻结环境与提交
  → 修复 P0：reasoning 路由、当前 502、运行时路径
  → 离线单元测试和前端测试基础设施
  → CLI/API/前端契约测试
  → LiteLLM trace + 实际推理 + PostgreSQL 精确归因
  → 六 Agent 连通性与工具/subagent 探针
  → 新原理图 Skill 确定性测试与 Judge mutation test
  → 六 Agent × 原理图用例 × with/without Skill
  → 并发、取消、重启和故障注入
  → 汇总缺陷、修复并全量回归
  → 生成最终认证报告
```

任何 P0/P1 未关闭时不得发布“全部功能可用”结论。

## 10. 最终验收门槛

只有同时满足以下条件，才能声明当前版本通过：

- 后端测试、前端单元测试、E2E、生产构建和运行时构建全部通过。
- CLI 能列 Agent、列模型、一键启动单次与六 Agent 批次评测。
- 六 Agent 在同一认证批次中全部完成，基础设施稳定性为 5/5。
- 所有 Agent 使用指定模型，数据库精确核验为 100%，无静默回退。
- reasoning 保持开启，OpenCode 不再依赖 `no-thinking` 路由。
- 工具轨迹验证通过；subagent 按能力声明得到真实证据或明确 unsupported。
- LLM Judge 全部完成，确定性评分与人工金标一致，失败任务不进入排名。
- 前端完整展示 Agent、模型映射、token、工具、subagent、trace、数据库和 Judge 信息。
- 新原理图 Skill 达到约定的拓扑正确性、专项分和 Skill lift。
- 401/403/429/5xx、DB/Judge/Agent 故障均能准确分类和安全清理。
- 无 P0/P1 未解决缺陷，无 Secret 泄漏，报告和证据可复现。

## 11. 新原理图 Skill 待确认问题

在开始为新 Skill 编写正式用例和金标前，需要确认：

1. 正式评测对象是只评 `schematic-pipeline` 的端到端能力，还是四个 Skill 既要分别评分、又要组合评分？现有 `schematic-generation` 是保留作旧版基线，还是最终被四 Skill 流水线替换？
2. 正式输入是否只接受自然语言电路描述？请提供一个最小输入、一个代表性复杂输入及预期 `sheets.json`。
3. `fetch_catalog.py` 的器件目录权威来源、版本机制和网络地址是什么？测试能否提供冻结的离线 catalog？
4. `auto_layout` 与 `apply_schematic` 服务的正式 URL、请求/响应 schema、鉴权方式和可用于自动化的 mock/测试环境是什么？任何鉴权配置变更仍需单独授权。
5. 最终输出只要求网页 URL 和布局 JSON，还是还要兼容 `tianshu-schematic/v1`、网表、KiCad、Altium或其他 EDA 格式？URL 需要保持可访问多久？
6. “电路正确”的权威真值是什么：人工金标、器件库规则、ERC/DRC、仿真结果还是已有工程？
7. `schematic-layout-codegen` 规定“每批 2 个 subagent”。对于不支持 subagent 或无法暴露父子轨迹的 Agent，是判定不支持该 Skill，还是允许适配器提供等价并行执行？
8. 六 Agent 是否都使用同一个 `glm-4.7`，还是要做 Agent × 多模型矩阵？Judge 是否允许使用独立、更强的模型，避免 GLM 自评偏差？
9. 版面布局、美观性、可编辑性分别是否进入评分？短接电源、漏线、引脚错接、虚构器件、缺 sheet、URL 无法打开中，哪些是一票否决项？
10. 原理图和器件数据是否含商业/保密信息？允许 Agent 使用哪些工具、网络和本机目录，测试证据需要怎样脱敏与保留？

这些问题不影响先执行平台基础验证；但在它们确认前，只能完成原理图 Skill 的测试框架，不能给出最终质量验收结论。
