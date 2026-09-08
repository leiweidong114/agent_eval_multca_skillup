# 2026-09-07 系统测试、问题分析与修复报告

## 结论与范围

本机六个 Agent 的严格链路冒烟验收 **6/6 通过**：Claude Code、CodeBuddy、Codex、JustDo、OpenClaw、OpenCode。
Agent 和 Judge 均使用 LiteLLM 的 `glm-4.5-air`，没有关闭推理，没有用模拟回答代替真实调用。
最终结果不是一次无失败测试：保留了失败轮次，逐项修复后复测；最终审计按每个 Agent 最新修复后的运行选取证据。

这是系统链路验收，不是“全部模型 × 全部 Agent × 全部业务功能永久可用”的证明。
四个新原理图 Skill 的真实业务评测、subagent 专项、长时间并发稳定性、内网新机迁移仍有未验证项。
未修改 LiteLLM、数据库或 JustDo 的鉴权规则；未推送远程仓库。

统一评测代码：`D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup`。
JustDo 必要修复位于相邻源码：`D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo`。
不能只移动开发版 JustDo-agent.exe；迁移步骤见该项目 `docs/features/multica-headless-cli.md`。

## 验收任务与标准

使用小型 `example-marker` Skill；提示词不包含正确 marker，只指定各 Agent 的 Skill 相对路径。
要求读取 Skill，创建 `artifacts/verification.txt`，回复 marker；如果 Skill 不存在则回复 SKILL_NOT_FOUND 并停止。
每个 Agent 各运行一次 with_skill 和 without_skill，分别在独立用例目录执行。

通过必须同时满足：

1. Agent 执行完成、非空回答。
2. 本次独立 trace key 对应的数据库成功调用确实属于指定模型。
3. with_skill 规则得分 100，without_skill 对照组规则得分 0。
4. 有真实工具调用证据。
5. 报告目录已保存 marker 文件，读取实际文件内容核验，不只检查回答文本。
6. LLM Judge 状态 completed，存在实际 Judge token 用量。
7. 总分与三维度权重计算一致。

## 最终结果

| Agent | 有/无 Skill 得分 | 工具调用数 | 数据库模型调用数 | 综合分 | 完整验收 |
|---|---:|---:|---:|---:|---|
| Claude Code | 100 / 0 | 5 | 7 | 88.00 | 通过 |
| CodeBuddy | 100 / 0 | 4 | 6 | 87.40 | 通过 |
| Codex | 100 / 0 | 8 | 10 | 88.00 | 通过 |
| JustDo | 100 / 0 | 3 | 5 | 88.00 | 通过 |
| OpenClaw | 100 / 0 | 3 | 5 | 88.00 | 通过 |
| OpenCode | 100 / 0 | 3 | 7 | 88.00 | 通过 |

工具调用数为该 Agent 两个用例的合计。JustDo/OpenClaw 来自 LiteLLM 完整会话的去重补充统计，
其余来自 Multica 归一化 Agent transcript。数据库调用数不含单独 Judge 请求。
所有最终条目的 `exact_model`、`saved_artifact`、`judge`、`score_math` 等审计字段均为 true。

最终审计：`.runtime/verification/20260907-final-audit.json`。
六 Agent 主轮次：`.runtime/verification/20260907-acceptance/`。
JustDo/OpenClaw 工具 ID 关联修复后复测：`.runtime/verification/20260907-acceptance-final-tools/`。
正式运行报告及产物：`backend/evaluation_results/local/system-verification/`。
浏览器证据：`.runtime/verification/browser/summary.json` 和 `schematic-overview.png`。

## 各功能验证状态

| 功能 | 测试结果 |
|---|---|
| 根目录 .env 与优先级 | 单测验证 BOM、引号、export、注释、环境变量覆盖、数据库 URL；真实命令通过 |
| LiteLLM API / 推理连通性 | check-litellm 与指定 glm-4.5-air 的 HI 请求通过 |
| PostgreSQL 连通性 | check-database 成功连接并读取 LiteLLM_SpendLogs |
| 模型列表 / 连通性缓存 | 当日快照 40 个可见模型，28 个通过文本探针，12 个未通过/被策略拒绝 |
| 模型 ID 统一投影 | 39 个允许的模型 × 6 Agent = 234/234；显式 no-thinking 部署按原有推理策略排除 6 个组合 |
| 本机 Agent 枚举 | agents 只列出可执行文件存在且版本探测成功的六个 Agent |
| JustDo 自动启动 | 实际执行 check-agent --agent justdo，默认模型 glm-4.5-air，返回 CONNECTIVITY_OK，数据库 verified |
| Multica Runtime | 新增逐用例 workspace 隔离测试，Go test 和重新构建通过 |
| 后端回归 | 109 项通过；有既有 FastAPI lifespan 弃用告警 |
| 前端构建 | Vite production build 通过；有大 bundle 告警 |
| 浏览器集成 | 首页、模型与 Agent、Skill、新建评测、结果列表、结果详情、原理图总览均加载成功；无 pageerror |
| 原理图总览日志 | 默认最新请求、用户/会话检索、完整原始内容展开、分页到 100 条均通过 |
| JustDo 编译与测试 | 产品 build、Electron TypeScript 检查通过；相关单测 30 通过、2 跳过；另 3 条 SQLite 单测受 ABI 问题阻塞，见下文 |

模型可见性、探针成功、Agent 执行、完整工具能力是不同级别的证据。234 项投影测试是离线配置验证，
没有对 234 个 Agent/模型组合全部付费执行真实任务。28 个可用模型也是当日文本探针快照，不是长期 SLA。

## 发现的问题与已实施修复

| 问题 | 原因 | 修复 |
|---|---|---|
| JustDo 已开桌面仍报 exit 69 | 旧安装版与开发 CLI relay 不匹配；桥接客户端依赖现有桌面 metadata | 在 JustDo 源码增加独立 CLI 主进程，复用 EngineManager/存储/认证 relay，每次运行独立目录 |
| Claude/Codex 接口不兼容 | 上游 GLM Chat Completions 与客户端 Messages/Responses 协议不同 | 统一协议转换文本、函数工具、Codex namespace/custom tool；明确拒绝不支持的模态 |
| CodeBuddy 中途不再执行工具 | 旧代理在收到第一次 tool result 后移除 tools | 默认保留工具定义，六 Agent 验收复测 |
| 模型名选对却被旧 alias 重定向 | 默认模型仍保留历史 gateway_models 映射 | 统一模式只将客户端别名转换到用户指定的完整 LiteLLM 模型 ID |
| 成功回答却提示数据库无记录 | LiteLLM 异步日志延迟 | 最长 60 秒有界等待，成功后至少观察 10 秒及稳定记录，避免首条记录一到就结束 |
| 对照组 FAIL 导致 Judge 不运行 | Skill-Up 非零退出码也用于规则断言失败 | 区分 PASS/FAIL 的已完成用例与 ERROR/TIMEOUT；低分结果正常进入 Judge |
| “有回答”但没有产物也能通过 smoke | 原测试只检查 marker 文本 | test 脚本增加真实保存文件内容核验；工作目录预建 artifacts，避免模型混用 shell mkdir 语法 |
| OpenClaw 产物未进入报告 | 固定 runtime workspace 与 Skill-Up 用例目录不一致 | Multica wrapper 为每个用例生成独立 OpenClaw 配置，指向准确 case workspace，不改原配置 |
| JustDo/OpenClaw 工具事件为 0 | 非流式 CLI 只返回最终消息，且数据库正文采集默认关闭 | 按本次完整轨迹需求开启正文采集；补充数据库会话工具事件，标明来源与未知错误状态 |
| 工具调用重复 / 完成率偏低 | OpenClaw 将 call_abc 改成 callabc，响应与后续历史被算成两次 | 仅在工具名及规范化 ID 唯一匹配时关联，不合并歧义 ID |
| 前端 Judge 显示旧模型 | 展示 YAML 默认值，实际运行已读取 .env | 展示同一份有效配置；移除 undefined Profile 显示 |
| 前端隐藏部分模型 | API 中存在固定模型排除名单 | 移除排除名单；新模型无需增加前端白名单 |
| 旧原理图预览依赖已删除 Skill | 原 preview API/test 指向 schematic-generation/scripts | 将纯确定性示例放到 backend/schematic_demo；不恢复用户已删除的旧 Skill |

协议转换仍调用真实 LLM。为了支持 Chat-only 部署，Codex hosted web_search 在此模式下禁用；
本地工具仍启用，没有关闭模型推理或更改用户鉴权。转换器目前先缓冲上游响应再输出 SSE，存在首字延迟。

## 评分结果分析

规则评分与 LLM Judge 都在运行。最终总分是 result 50% + process 30% + skill_quality 20%；
各维度再按 scoring.yaml 中的 rule_weight / llm_weight 混合。
对本例，任务维度和过程维度接近 100，而极简 marker Skill 的结构规则得分只有 40，
因此综合分约 88，符合当前规则的计算结果，不应要求简单任务必须综合 100。
CodeBuddy 本次 LLM 过程维度略低，综合 87.4；不是模型调用失败。

无 Skill=0、有 Skill=100 是刻意构造的小型正负对照，证明 Skill 注入和规则计分链路起作用，
不能据此声称真实原理图生成提升 100 分。当前结构规则对关键词/章节较敏感，LLM 也可能受规则分锚定，
需为原理图任务补充连接拓扑、端口、布局、导入可用性和图片/文件验收规则。
Judge 不可用时仍可保留规则诊断分，但本次新增评分 partial 标记，不作为完整 Judge 排名结果。

## 尚未完成、限制及解决方案

1. **全模型能力不是通用协议转换能保证的。** 文本、视觉、工具、长上下文、reasoning、Responses hosted tools 的能力由模型/上游决定。
   本轮没有验证全部可用模型的工具能力。建议增加 model × protocol × task 能力矩阵，不兼容时明确报错。
2. **JustDo 新机迁移未现场验证。** 新增 `npm run multica:build-agent`，重新构建产品与本机 launcher。
   新环境必须有目标平台 Electron/OpenClaw/native dependencies；内网需预先准备依赖与缓存或完整安装产物。
3. **JustDo SQLite 单测环境问题。** 当前 PATH 的 Node 是 v26.1.0 / ABI147，better-sqlite3 为 Electron ABI146。
   首次相关全组测试 3 条 SQLite 测试失败，不代表这 3 条通过；实际 Electron CLI 与数据库会话链路已通过。
   项目要求 Node24；建议在独立测试环境按 Node ABI rebuild，再在产品构建环境 rebuild:electron-native，避免运行中切换原生模块。
4. **subagent 未专项触发。** 当前 0 只能说明本任务未观测到，现有检测是工具名启发式，不能证明完整父子树采集能力。
   下一轮应强制创建子任务并核验 parent/session/run 关联及工具结果。
5. **日志完整性有上游边界。** 前端展示数据库已有的完整记录；上游未记录/截断内容无法恢复。
   Agent 原始 token 与 LiteLLM token 口径可能不同，报告保留两类证据，不强行相等。
   数据库等待有界，不保证极端延迟下记录永不遗漏；分页加载时前端按 request_id 去重。
6. **安全部署边界保持原样。** 服务原本是受信任本机无登录模式，未新增鉴权、未改服务器账号权限。
   全文日志含用户任务内容，不应直接暴露到公网；多人内网部署的权限、跨用户隔离需先经用户批准设计。
7. **四个新原理图 Skill 未做真实业务验收。** 当前只验证页面/接口及确定性 preview，不能拿 demo judge=100 冒充 LLM 原理图评测。
   需要确认四个 Skill 的可运行版本、真实输入、外部工具/页面依赖、参考输出与通过阈值。
8. **长稳态/负载未验收。** 此次包含多轮修复后重测，但不是长时间压测。后续可做 20+ 轮、并发1/2/4、取消/重启/日志延迟故障注入。

## 复现、文件与清理原则

完整命令见 [cli-complete-guide.md](cli-complete-guide.md) 第 16–17 节。

```powershell
agent-eval check-litellm --model glm-4.5-air
agent-eval check-database
agent-eval models --list
agent-eval models --refresh --timeout 30 --workers 3
agent-eval agents
agent-eval check-agent --agent justdo
python test/verify_system.py --phase evaluation --model glm-4.5-air --workers 2 --timeout 240
python test/verify_model_adapters.py
python test/analyze_verification.py .runtime/verification/20260907-acceptance .runtime/verification/20260907-acceptance-final-tools --output .runtime/verification/20260907-final-audit.json
```

保留 test/ 下的测试/审计脚本、docs 报告、用户新 Skill、历史 tests_reports 和原始运行证据。
计划清理仅针对 `.pytest_cache` / Python 字节码等可重新生成的缓存，但删除命令被当前执行环境策略拒绝，
因此本轮缓存清理未完成，没有改用其他方式绕过限制。旧报告和源文件没有删除。
`.env`、运行时凭据与正文证据不上传 Git；没有删除运行必需的 .runtime/.tools、没有撤销用户原有改动。
