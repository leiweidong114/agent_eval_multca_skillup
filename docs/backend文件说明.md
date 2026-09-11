当前 backend 里有两套评测子系统：
- agent_eval：Agent 调用、模型适配、Skill 评测、原理图生成评测。
- maeval：题库/模型能力评测，通过 /prism 挂载到同一个后端服务。
整体调用关系是：
命令行 agent-eval ───────────────┐
                                 ├─> agent_eval.runner.run_evaluation()
前端 /api/run → JobManager ─────┘
                                      │
                         模型配置 + Agent 适配
                                      │
                    Skill-Up → Multica 本地运行时
                                      │
                           Codex/OpenClaw/JustDo 等
                                      │
                    LiteLLM Trace + 规则评分 + LLM Judge
                                      │
                           evaluation_results/
四个核心功能分别在哪里
功能	主处理文件	作用
模型适配	[model_config.py (line 245)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/model_config.py:245)	解析模型、LiteLLM 地址、密钥、模型别名，生成不同 Agent 的临时配置
Agent 适配声明	[agent_adapters.py (line 1)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/agent_adapters.py:1)	声明每个 Agent 的模型选择方式、协议、Skill 安装目录、是否支持评测
Agent 路径与命令	[runtime.py (line 1)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/runtime.py:1)	查找可执行文件、构造 Agent 命令、处理 JustDo/OpenClaw 映射、声明 subagent 能力
模型协议转换	[protocol_adapter.py (line 105)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/protocol_adapter.py:105)	将 Codex Responses、Claude Messages 等协议转换为 LiteLLM Chat Completions；也处理 additional_tools
兼容代理	[codebuddy_proxy.py (line 82)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/codebuddy_proxy.py:82)	本地代理、模型名强制转换、重试和协议转换
Agent 实际调用	[runner.py (line 372)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/runner.py:372)	完整评测总调度：准备工作区、注入 Skill、启动 Agent、采集结果、评分
Multica 启动层	[main.go (line 308)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/runtime/multica-local-runner/main.go:308)	编译成 multica-eval-runtime，真正调用 Multica Agent backend，并采集工具、subagent、artifact 信息
Skill 评测	[runner.py (line 372)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/runner.py:372)	Skill 评测的主执行流程
Skill 管理/组合	[skill_registry.py (line 48)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/skill_registry.py:48)	上传、删除、版本管理、多个 Skill 合并
Skill 质量评分	[skill_quality.py](D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/skill_quality.py)	检查 SKILL.md、目录、脚本、评测用例质量
过程和规则评分	[scoring.py (line 121)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/scoring.py:121)	工具调用、subagent、时长、token、Skill 读取证据等评分
评测器插件接口	[evaluators/protocol.py](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/evaluators/protocol.py)	固定插件输入、输出和 API 版本
评测器注册与加载	[evaluators/registry.py](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/evaluators/registry.py)	按 `.env` 注册内置或内网外部评测器
LLM Judge	[llm_judge.py (line 94)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/llm_judge.py:94)	使用配置的 LiteLLM 模型对结果、过程和 Skill 质量进行 Judge
原理图评测入口	[routes_eval.py (line 61)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/api/routes_eval.py:61)	evaluation_type=schematic 时加载设置中指定的原理图 Skills
原理图四 Skill 组合	[cli_catalog.py (line 11)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/src/agent_eval/cli_catalog.py:11)	定义四个原理图 Skill，并生成组合评测 Bundle
原理图流程定义	[schematic-pipeline/SKILL.md (line 1)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/skills/schematic-pipeline/SKILL.md:1)	规定三个阶段的执行顺序、产物和验收条件


原理图四个 Skill 的职责
1. [schematic-pipeline (line 9)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/skills/schematic-pipeline/SKILL.md:9)
   总编排 Skill，要求 Agent 依次执行下面三个子 Skill。
2. [signal-interface-generation (line 1)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/skills/signal-interface-generation/SKILL.md:1)
   获取器件库、选择器件、生成和校验 sheets.json，再生成信号接口表。
3. [schematic-layout-codegen (line 1)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/skills/schematic-layout-codegen/SKILL.md:1)
   让 subagent 分片生成电路 DSL/Python 代码，随后调用自动布局服务生成布局 JSON。
4. [schematic-web-apply (line 1)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/skills/schematic-web-apply/SKILL.md:1)
   把布局 JSON 提交给网页应用服务，生成最终可打开的原理图 URL。
完整原理图评测并不是由 routes_schematic.py /generate 完成，而是：
routes_eval.py
  → 选择四个原理图 Skill
  → skill_registry.compose_skills()
  → runner.run_evaluation()
  → Skill-Up
  → Multica
  → 指定 Agent
  → 执行四 Skill
  → 规则评分 + LLM Judge
backend/app 的作用
这是当前主 FastAPI 服务层。
- [main.py (line 8)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/main.py:8)：创建 FastAPI，注册所有接口。
- [routes_eval.py (line 134)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/api/routes_eval.py:134)：启动单个/批量 Skill 或原理图评测。
- [job_manager.py (line 21)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/job_manager.py:21)：评测排队、线程池、进度事件、取消任务、实时输出。
- [routes_runs.py (line 56)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/api/routes_runs.py:56)：结果列表、详情、交互记录、打开任务文件夹。
- [routes_skill.py (line 119)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/api/routes_skill.py:119)：Agent、模型、数据库、设置和 Skill 管理接口。
- [routes_schematic.py (line 34)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/api/routes_schematic.py:34)：原理图会话筛选，以及独立的演示生成/评分接口。
- [model_eval.py (line 11)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/app/model_eval.py:11)：把题库评测系统挂载到 /prism。
backend/src/agent_eval 的作用
这是当前 Agent/Skill 评测的核心代码。
- cli.py：agent-eval 命令行入口。
- runner.py：完整评测执行器。
- runtime.py：Agent 可执行文件发现和命令构造。
- model_config.py：LiteLLM 模型配置和 Agent 模型映射。
- agent_adapters.py：各 Agent 的适配能力声明。
- protocol_adapter.py：不同模型 API 协议转换。
- codebuddy_proxy.py：运行级兼容代理。
- database.py：读取 LiteLLM PostgreSQL 日志、交互和 token。
- litellm_trace.py：创建/删除运行级 Trace Key。
- scoring.py：规则、过程和工具/subagent 指标。
- llm_judge.py：LLM Judge。
- skill_quality.py：Skill 本身质量评分。
- evaluators/：稳定评测器协议、默认兼容实现及外部插件注册器。
- skill_sources.py：从 `.env` 配置的外部目录发现内网私有 Skill。
- failure.py：错误分类和用户可读提示。
- cli_catalog.py：Skill/结果查询以及原理图 Skill Bundle。
backend/src/maeval 的作用
这是题库评测子系统，不是当前 agent-eval check-agent 的模型适配层。
- webapp/api.py：题库、Provider、实验、结果等 /prism/api/* 接口。
- webapp/engine.py：题库实验调度和逐题执行。
- adapters.py：题库评测所用的 HTTP/CLI Agent Adapter。
- scoring.py：题库答案评分。
- webapp/benchmarks.py：ARC 等题库安装与导入。
- webapp/db.py：题库评测自身的 SQLite 数据库。
- reporting.py：题库评测报告。
因此，项目里看到两个 scoring.py 和两个 Agent Adapter 是正常的：
- agent_eval/*：Skill/原理图评测。
- maeval/*：ARC 等题库评测。
其他顶层目录
目录	作用
config/	模型、LiteLLM、数据库、评分、运行设置；私密配置不应提交 Git
skills/	已安装和内置 Skill
.runtime/	已构建运行时、组合 Skill、删除 Skill 暂存等运行资源
.tools/	skill-up.exe 等工具
runtime/	multica-eval-runtime 的 Go 源码
evaluation_results/	Skill 和原理图评测结果、日志、交互记录
model_eval_data/	/prism 题库评测数据库、题库和结果
schematic_projects/	routes_schematic.py 独立演示接口生成的项目
schematic_demo/	独立的示例原理图生成器和 Judge，不等同于 Agent 四 Skill 全流程
fixtures/	测试样例
tests/	后端自动化测试
scripts/	安装、构建、验证和维护脚本
patches/	Skill-Up 定制补丁
.pytest_cache/、__pycache__/	可删除的测试和 Python 缓存


后端真正启动入口是 [run_server.py (line 17)](/D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/run_server.py:17)，它加载的是 app.main:app。最核心的业务文件则是 runner.py：模型适配、Agent 调用、Skill-Up、Trace、评分和报告最终都在这里汇合。
