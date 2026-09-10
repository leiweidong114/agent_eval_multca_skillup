# agent_eval_multca_skillup

一个本地、无 Multica 登录的 Agent Skill 评测工具。支持 Agent 原生模型认证或远程 LiteLLM，并可直接读取 LiteLLM PostgreSQL 交互数据参与过程评测：

- Skill-Up 负责隔离 Skill、执行用例、断言、基准对照和生成 JSON/HTML/JUnit 报告。
- Multica 的开源 Agent backend 负责统一调用不同 Agent CLI。
- 本项目不启动 Multica Server，不调用 Multica 登录、Issue 或数据库服务。
- 发给 Agent 的系统提示词固定为空；单条用例 Prompt 按原始字节内容传递，不注入 Multica 默认提示词。
- PostgreSQL 作为指定模型硬校验的数据源；默认要求数据库精确确认当前任务实际调用了指定模型。
- 内嵌 Prism Eval 模型与题库评测子系统，支持标准/私有题库、模型与 Agent 接入、实验对比、逐题证据和导出。

## 项目结构（前后端分离 + 本地评测运行时）

```text
agent_eval_multca_skillup/
├── backend/                  # 后端：FastAPI Web 服务 + 评测核心 + 运行时
│   ├── app/                  # Web 层（路由装配、Skill 注册、任务管理、评测 API）
│   │   ├── main.py           # FastAPI 入口（含 CORS、路由装配）
│   │   ├── config.py         # 后端路径配置
│   │   ├── skill_registry.py # Skill 上传/ZIP 校验/版本与组合
│   │   ├── job_manager.py / retention.py / model_eval.py
│   │   └── api/              # REST 路由（routes_eval/routes_skill/routes_runs/routes_schematic）
│   ├── src/                  # 原有 agent_eval 评测核心逻辑（CLI 引擎、评分、报告）
│   ├── tests/                # 后端/评测核心 Python 单元测试
│   ├── skills/               # 评测用 Skill 仓库（SKILL.md + scripts/references/evals）
│   │   ├── api-test-suite-builder/  webapp-testing/   # Web/API 自动化测试类技能
│   │   ├── docx/  xlsx/  example-marker/              # 文档/表格/标记样例技能
│   │   ├── signal-interface-generation/                # 自然语言 → 多 sheet 信号接口表
│   │   ├── schematic-layout-codegen/                   # sheets → Python DSL → auto_layout 布局
│   │   ├── schematic-web-apply/                        # 布局 JSON → 网页 URL
│   │   └── schematic-pipeline/                         # 上述三步的总编排（subagent 并行）
│   ├── config/               # 内置模型路由、数据库与评分默认值（本机配置统一放根 .env）
│   ├── scripts/              # 运维脚本（setup_windows.ps1、setup_linux.sh、install_skillup_windows.ps1 等）
│   ├── patches/              # 对第三方运行时的 Windows 补丁（skill-up custom-engine patch）
│   ├── runtime/              # Windows/Linux 本地运行时（Go、Multica、Skill-Up、venv）
│   ├── fixtures/             # 评测/测试夹具
│   ├── model_eval_data/      # Prism 题库引擎独立数据（maeval.db、密钥、结果）
│   ├── evaluation_results/   # Skill 评测结果归档（用户/任务/时间__run_id/…）
│   ├── runs/                 # 运行历史（job/日志/报告）
│   ├── schematic_projects/   # 原理图生成工程产物（每工程 JSON）
│   ├── schematic_demo/       # 原理图示例工程
│   ├── run_server.py         # 后端启动脚本
│   ├── pyproject.toml / requirements.txt
├── frontend/                 # 前端（Vue 3 + Vite + Element Plus）
│   ├── src/views/            # 首页、新建评测、题库/Skill/结果/运行环境等页面
│   ├── src/components/ api/ router/ styles
│   ├── vite.config.js        # dev 代理 /api -> http://127.0.0.1:8000
│   └── dist/                 # 构建产物
├── docs/                     # 设计/契约/验证文档
│   ├── full-system-test-validation-plan.md / evaluation-scoring-and-agent-contract.md
│   ├── cli-complete-guide.md / cli-model-probe.md / 20260907-system-verification-report.md
├── figures/                  # 架构图/流程图与图源（png/mmd/md）
├── runs/                     # 评测运行归档（汇总报告/产物）
├── skills/                   # 顶层示例技能（example-marker），便于外部直接引用
├── test/                     # 系统级验证脚本（verify_system.py 等）
├── tests_reports/            # 各阶段测试报告（每子目录一次测试/一轮矩阵/一次 demo）
├── README.md                 # 项目说明（本文档）
└── VERSION.md                # 版本记录
```

### 各文件夹作用速查

| 文件夹 | 作用 |
|---|---|
| `backend/` | Python 后端：评测 API、Skill 平台、Prism 题库、本地运行时管理、评测核心逻辑与归档。 |
| `backend/app/` | FastAPI Web 应用层：路由、Skill 注册/组合、任务队列、保留策略、模型评测装配。 |
| `backend/app/api/` | REST 路由：`routes_eval`(评测)、`routes_skill`(技能/代理)、`routes_runs`(历史)、`routes_schematic`(原理图工程/Judge)。 |
| `backend/src/` | 评测核心引擎（agent-eval CLI 逻辑），被 Web 层复用。 |
| `backend/skills/` | 评测用技能库；每个目录是一个可被评测隔离复制的 Skill（`SKILL.md` 描述流程与产物）。新增技能即放这里。 |
| `backend/config/` | 仓库内置的模型路由、数据库和评分默认值；可变部署配置统一读取根目录 `.env`。 |
| `backend/scripts/` | 安装/维护脚本：`setup_windows.ps1`、`setup_linux.sh`、`install_skillup_windows.ps1` 等。 |
| `backend/patches/` | Windows 平台对第三方组件（如 Skill-Up）的补丁。 |
| `backend/runtime/` | 项目专属运行时：独立 Go、Multica 源码与编译产物、Skill-Up、Python venv（Windows/Linux 分离）。 |
| `backend/tests/`、`backend/fixtures/` | 单元测试与测试夹具。 |
| `backend/evaluation_results/`、`backend/runs/` | 评测产物/历史归档（隔离副本、报告、交互记录）。 |
| `backend/schematic_projects/`、`backend/schematic_demo/` | 原理图工程与示例产物（供网页打开）。 |
| `backend/model_eval_data/` | Prism 题库子系统独立数据（题库、加密密钥、结果证据）。 |
| `frontend/` | Vue3+Vite 前端：评测/题库/Skill/结果等页面，`/api` 代理到 8000。 |
| `docs/` | 架构、评分契约、CLI 指南与系统验证文档。 |
| `figures/` | 架构/流程配图（mmd 源与 PNG）。 |
| `runs/`（根） | 运行历史/汇总产物归档。 |
| `skills/`（根） | 顶层示例技能 `example-marker`，供外部用户直接引用。 |
| `test/` | 系统级验证脚本（`verify_system.py`、`browser_smoke.cjs` 等）。 |
| `tests_reports/` | 各阶段测试报告，每子目录一次测试/评测矩阵/端到端 demo（含原理图 4-Skill demo、TX 射频 demo）。 |
| `README.md` / `VERSION.md` | 项目说明与版本记录。 |

### 后端接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/health | 健康检查 |
| GET | /api/agents | 支持的 Agent 列表 |
| GET | /api/skills | 可用 Skill 列表 |
| GET | /api/skills/{name} | Skill 指令内容、文件和用例数 |
| GET | /api/skills/{name}/cases | 某 Skill 的用例列表 |
| GET | /api/model-config | 非敏感模型配置 |
| GET | /api/models | 从 LiteLLM 发现模型并合并本地回退配置 |
| GET | /api/database/health | PostgreSQL 直连状态与交互记录数 |
| POST | /api/run | 创建后台评测任务，立即返回 job_id |
| GET/POST | /api/jobs、/api/jobs/{id}/cancel | 进度查询与取消 |
| GET | /api/capacity | 顶层任务池与单任务 case 并发容量 |
| POST | /api/validate | 仅校验配置不完整运行 |
| GET | /api/runs | 历史评测记录 |
| GET | /api/runs/{run_id} | 单次评测详情 |
| POST/GET | /api/skills/upload、/api/skills/versions | Skill ZIP 上传与内容版本管理 |
| GET/POST | /api/privacy/retention、/api/privacy/retention/cleanup | 保留策略预览与显式清理 |
| GET/POST | /api/schematic/example、/api/schematic/generate | 框图示例与完整原理图流水线 |
| POST | /api/schematic/judge | 对外部生成的原理图 JSON 做专项评分 |
| GET | /api/schematic/projects/{id} | 读取可在网页打开的原理图工程 |
| GET/POST | /prism/api/benchmarks、/prism/api/benchmarks/import | Prism 题库目录与离线导入 |
| GET/POST | /prism/api/providers、/prism/api/experiments | 模型/Agent 接入与题库实验 |
| POST | /prism/api/providers/auto | 按模型 Profile 和 Agent 自动创建题库 Provider |
| GET | /prism/api/experiments/{id}/results、/comparison | 逐题证据与统计比较 |

### 统一评测前端

网页提供首页、新建评测、题库管理、Skill 管理、评测结果、模型与 Agent 六个一级页面。新建评测可选择原理图评测、题库评测或 Skill 评测；Skill 评测支持一次选择 1–8 个 Skill，并通过 Prompt 组织联合任务。评测结果按三种类型进入各自的详情子页。

题库能力由内嵌 Prism Eval 引擎提供，与 Skill 评测共享同一个 FastAPI 服务，但使用独立的 SQLite 数据、加密密钥、题库快照和结果证据目录：

```text
backend/model_eval_data/
  maeval.db
  secret.key
  evaluation-results/
```

本地集成模式继承当前项目的无登录边界，并以本地管理员身份运行。题库管理页可查看所有题库和题目内容，也可导入 JSON 题库；统一“新建评测”页会根据所选 Agent、模型 Profile 和题库类型自动创建或复用加密 Provider。可通过 `MODEL_AGENT_EVAL_DATA_DIR` 把数据目录迁移到其他受控位置。

从旧 `model-agent-eval` 数据库迁移已安装的公开题库时，可以运行下面的脚本。它只复制公开题库、题目与版本指纹，不复制用户、密码、API Key、模型配置、实验或历史结果：

```powershell
python backend/scripts/import_model_eval_benchmarks.py `
  --source-db D:\AI_FOR_WORLD\14_AI_workspace\common_tools\model-agent-eval\data\maeval.db
```

### 启动方式

后端（需先运行 `backend/scripts/setup_windows.ps1` 生成运行时）：

```powershell
cd backend
python run_server.py --port 8000
```

前端：

```powershell
cd frontend
npm install
npm run dev   # 打开 http://127.0.0.1:5173
```

前端 dev 服务器会把 `/api` 请求代理到后端 `http://127.0.0.1:8000`。

## 架构

```text
agent-eval CLI
  -> Skill-Up（用例、隔离、断言、报告）
    -> multica-eval-runtime（本地自定义引擎）
      -> Multica Agent backend
        -> 指定的 Agent CLI + 指定模型
```

Agent CLI 自身可能需要本地安装和配置；这属于 Agent 运行环境，不是 Multica 登录。评测会为每个任务创建独立的 LiteLLM 虚拟 Key，并按 Key 别名关联 PostgreSQL `LiteLLM_SpendLogs`，原始记录写入任务目录的 `model-interactions.json`。

评测任务由后端工作线程执行，任务状态写入 `backend/evaluation_results/_jobs`，前端可实时查询进度和取消。所有任务产物按 `用户/任务/时间__run_id` 集中归档；服务重启后未完成任务会标记为 `interrupted`。

并发、三维评分、Skill 安装语义、可观测字段限制和新 Agent 上线门槛见
[`docs/evaluation-scoring-and-agent-contract.md`](docs/evaluation-scoring-and-agent-contract.md)。
完整的前端、后端、CLI、六 Agent、LiteLLM、数据库、评分以及原理图 Skill 验证闭环见
[`docs/full-system-test-validation-plan.md`](docs/full-system-test-validation-plan.md)。

## 模型配置

默认配置位于 `backend/config/models.yaml`：

```yaml
default_profile: native_codex
profiles:
  native_codex:
    type: native
    model: gpt-5.4
  litellm_deepseek_flash:
    model: deepseek-v4-flash
    api_base: http://8.137.196.46/v1
    api_key_env: LITELLM_API_KEY
  litellm_minimax:
    model: MiniMax-M3
    api_base: http://8.137.196.46/v1
    api_key_env: LITELLM_API_KEY
  litellm_opencode_go:
    model: opencode-go/minimax-m3
    api_base: http://8.137.196.46/v1
    api_key_env: LITELLM_API_KEY
  litellm_opencode_go_minimax_2_7:
    model: opencode-go/minimax-m2.7
    api_base: http://8.137.196.46/v1
    api_key_env: LITELLM_API_KEY
```

所有本机配置统一写在被 Git 忽略的根目录 `.env`：

```dotenv
LITELLM_API_BASE=http://127.0.0.1:4000/v1
LITELLM_API_KEY=sk-your-virtual-key
LITELLM_MODEL=glm-4.5-air
LITELLM_JUDGE_MODEL=glm-4.5-air
```

运行时会为不同 Agent CLI 同时提供 OpenAI 兼容变量
`OPENAI_BASE_URL`/`OPENAI_API_KEY` 和 Anthropic 兼容变量
`ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`。虚拟 Key 不会写入生成的
`eval.yaml`、评测报告或 Git。可通过修改 `models.yaml` 添加多个 profile，运行时用
`--profile <name>` 选择；`--model` 仅用于临时覆盖该 profile 的默认模型。
Codex 还会自动获得 `model_provider=litellm` 的命令行配置，避免已有 ChatGPT 登录覆盖
LiteLLM 地址。OpenClaw/JustDo 会继续使用 Agent ID `main`，并由评测端生成临时
LiteLLM provider 配置；配置只引用 `${LITELLM_API_KEY}`，不会包含真实 Key。

“模型与 Agent”页面也可以创建 CC Switch 风格的自定义 Provider。页面配置与 API Key
都写入根目录 `.env`，查询接口只返回 `api_key_configured`，不会回显 Key。Profile
支持 `openai_compatible`、`openai_chat`、`openai_responses` 和
`anthropic_messages` 协议、上下文窗口、最大输出 Token，以及 `agent_models` /
`gateway_models` 两级模型别名。要让同一 Provider 覆盖全部 21 个评测 Agent，应使用能
同时提供 OpenAI Responses、Chat Completions 和 Anthropic Messages 兼容入口的网关，并
选择 `openai_compatible`；单协议直连 Profile 只会允许协议匹配的 Agent。

本阶段的统一模型适配范围为 21 个
`specified_model_and_skill_evaluation=true` 的 Agent。`mcode`、`qwenpaw`、
`zeroclaw` 仍由运行时管理模型；`dim`、`hermes` 仍缺少直接 Skill 注入，因此五者不在
该范围内。`GET /api/agents` 的 `capabilities.model_adapter` 会返回每个 Agent 的模型选择、
Provider 注入和客户端协议方式。

OpenCode Go 模型在 LiteLLM UI 中使用 `opencode-go/<model-id>` 名称管理。无需启动
Skill-Up 评测即可先做 Agent/模型连通性检查：

```powershell
agent-eval check-agent --agent codex --profile litellm_opencode_go
agent-eval check-agent --agent claude --profile litellm_opencode_go
agent-eval check-agent --agent codebuddy --profile litellm_opencode_go
agent-eval check-agent --agent openclaw --profile litellm_opencode_go
agent-eval check-agent --agent opencode --profile litellm_opencode_go
```

例如统一验证 MiniMax 2.7：

```powershell
agent-eval check-agent --agent codex --profile litellm_opencode_go_minimax_2_7
```

各 Agent 的 CLI 模型名可以不同，但底层 LiteLLM deployment 必须是同一个。例如
CodeBuddy 只接受 `custom-local:MiniMax-M2.7`，评测系统会将它确定性映射到
`opencode-go/minimax-m2.7`；报告中保留统一模型名和 Agent 实际参数，避免把别名误当成
另一个模型。

Claude、CodeBuddy 和 OpenClaw 的 LiteLLM 请求经过每次任务独立的本地弹性代理。
代理强制写入 profile 对应的 gateway model，并对连接超时、断连以及 HTTP
429/500/502/503/504 最多尝试 4 次，遵守 `Retry-After` 并使用指数退避。CodeBuddy 的
CLI 模型别名配置在 profile 的 `agent_models.codebuddy`，不会再退回账号 Token Plan；
OpenClaw 同时使用隔离的 `OPENCLAW_CONFIG_PATH`、`OPENCLAW_STATE_DIR` 和 workspace，
不会读取或迁移用户的旧工作区。

该命令只发送一次 `CONNECTIVITY_OK` 探针，不创建 Skill-Up 运行、评分或评测结果目录。
`agent-eval agents` 可列出 Multica 支持的后端及当前机器实际安装的 CLI；只有探测到
可执行文件的 Agent 才能在本机完成连通性验证。

## PostgreSQL 配置

复制 `.env.example` 为根目录 `.env`，填写数据库连接：

```dotenv
DATABASE_ENABLED=true
DATABASE_HOST=127.0.0.1
DATABASE_PORT=5432
DATABASE_NAME=litellm
DATABASE_USER=litellm
DATABASE_PASSWORD=your-database-password
```

如果运行环境已经提供完整 `DATABASE_URL`，它优先于分项配置。数据库用户只需对 `LiteLLM_SpendLogs` 具有只读权限。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/database/health
```

如需把一次运行和数据库记录严格一一对应，在同一个 `.env` 中设置
`LITELLM_MASTER_KEY`。旧版本升级可运行
`python backend/scripts/migrate_config_to_env.py --remove-legacy`，脚本会保留已有 `.env`
值，并迁移旧的 `local.yaml`、`secrets.env`、运行设置和 Agent 路径。

后端优先为每个任务创建一小时有效的临时虚拟 Key，并按 `key_alias` 精确读取 SpendLogs，运行结束后删除。仅当显式关闭模型硬校验时，网关禁止管理接口才允许退化为任务时间窗口匹配，并在报告中标记较弱的 Agent 归因。默认开启模型硬校验：没有成功调用或实际模型不匹配都会令任务失败。只有显式使用 `--no-require-model-verification` 才允许保留“未确认”的诊断结果。默认不读取 messages/response。

模型硬校验现在是 fail-closed：必须同时满足 LiteLLM profile、可用 PostgreSQL、
`LITELLM_MASTER_KEY`、成功创建 run-scoped virtual key 和按 alias 命中的 SpendLogs。
任一预检失败都会在调用 Agent 前停止，不再消耗模型额度；不会退化为时间窗口证据。
PostgreSQL 暂态连接错误会自动重试 4 次。任务报告中的 `gateway_resilience`、`failure`
和 `trace_key_cleanup` 分别记录网关重试、失败分类/可重试性和虚拟 Key 清理结果。

## 原理图能力现状

Web 后端仍保留 `/api/schematic/*` 与 `/schematic` 前端页面（工程读取、专项 Judge），接口实现见 `backend/app/api/routes_schematic.py`。注意：旧的 `backend/skills/schematic-generation` 技能目录当前已不在仓库，凡引用该路径的 CLI/脚本需先恢复对应技能目录才能运行。

当前仓库内置的原理图相关技能为下方"原理图整版生成"流水线的 4 个 Skill（`signal-interface-generation` → `schematic-layout-codegen` → `schematic-web-apply`，由 `schematic-pipeline` 编排），它们对接外部 `auto_layout_service` 双服务。

## 原理图整版生成（auto_layout 服务 + 4 Skills）

外部服务与自动布局算法位于 `D:\AI_FOR_WORLD\14_AI_workspace\common_tools\自动布局算法\auto_layout_service`（FastAPI，端口 8631）：

- **Service 1 auto_layout**：body 提交电路 DSL Python 代码 → 返回带坐标的布局 JSON。
- **Service 2 apply_schematic**：布局 JSON 集 → 多图页网页（左栏切换），返回 URL。

本仓库新增 4 个编排 Skill（`backend/skills/`），把自然语言电路描述端到端变成网页：

```text
signal-interface-generation/   需求 → 信号接口列表 sheets.json（每主芯片一个 sheet）
schematic-layout-codegen/      sheets → Python DSL → 调 auto_layout 布局（subagent 并行 2/批）
schematic-web-apply/           布局 JSON → apply_schematic → URL
schematic-pipeline/            以上三步的总编排 SKILL
```

端到端 demo：
- `tests_reports/20260903_schematic_pipeline_demo/report.md` —— STM32F103C8Tx + 8×LED（流水线全链路）。
- `tests_reports/20260903_tx_simple_demo/report.md` —— 简化 TX 射频（控制/电源/功放/滤波四块；器件库已由 17 扩至 20，新增 RFM95W-868S2、Filter_EMI_CLC、Antenna）。

## Windows 安装

Windows 使用独立离线 Runtime Release。新电脑只需要 PowerShell 和用于克隆仓库的 Git；Go、Python、Node.js、Python wheel、npm cache、Skill-Up、Multica、JustDo 和公开题库均由离线包提供。

下载当前完整包：[`agent-eval-runtime-windows-x64-portable.zip`](https://github.com/leiweidong114/agent_eval_multca_skillup/releases/download/windows-runtime-20260910/agent-eval-runtime-windows-x64-portable.zip)

```powershell
Set-Location D:\workspace\agent_eval_multca_skillup
.\install_windows.ps1 -ReleaseRoot D:\Downloads\agent-eval-runtime-windows-x64
notepad .env
.\start.ps1
```

安装内容保存在项目的 `backend/.runtime/windows`、`backend/.tools/windows` 和 `backend/.offline-cache/windows`，安装完成后不再依赖 Release 解压目录。根目录 `.env` 是唯一部署配置入口。停止、重启和修改代码后重新构建：

```powershell
.\stop.ps1
.\restart.ps1
.\rebuild.ps1 -Target Frontend
.\rebuild.ps1 -Target SkillUp
.\rebuild.ps1 -Target Multica
```

Skill-Up 0.9.1 的自定义本地引擎硬编码了 POSIX 命令语法。本项目构建时自动应用 [`backend/patches/skill-up-v0.9.1-windows-custom-engine.patch`](backend/patches/skill-up-v0.9.1-windows-custom-engine.patch)，仅修复 Windows `cmd.exe` 的路径引用和旧输出清理，不改变评分逻辑。

Release 目录规范、离线依赖要求和完整操作说明见 [`docs/windows-offline-migration.md`](docs/windows-offline-migration.md)。安装过程按部署目录直接读取文件，不校验 manifest/SHA256，也不管理 Release 版本。

## Linux 安装

要求：x86_64/arm64 Linux、`sh`、Git、curl、Python 3.10+：

```sh
cd /path/to/agent_eval_multca_skillup
sh backend/scripts/setup_linux.sh
```

Linux 使用同版本 Multica、Skill-Up 和 Go，产物保存在 `backend/.runtime/linux`、`backend/.tools/linux`。整个项目目录可迁移，但 Windows 与 Linux 的本地二进制目录彼此独立；在目标系统首次运行对应的 setup 脚本即可。

## CLI 使用

检查运行层：

```powershell
.\backend\.runtime\windows\python\Scripts\agent-eval.exe doctor
.\backend\.runtime\windows\python\Scripts\agent-eval.exe agents
```

指定 Agent、模型、Skill 和已有用例：

```powershell
.\backend\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill .\backend\skills\example-marker `
  --user wedax `
  --task-name marker-regression `
  --agent codex `
  --profile litellm_deepseek_flash `
  --case .\backend\skills\example-marker\evals\cases\marker.yaml `
  --agent-executable C:\path\to\codex.exe `
  --parallelism 2 `
  --iterations 1 `
  --benchmark
```

直接用 Prompt 和确定性字符串约束生成临时用例：

```powershell
.\backend\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill C:\skills\my-skill `
  --agent claude `
  --profile litellm_deepseek_flash `
  --prompt "执行这个任务" `
  --must-contain "expected marker" `
  --must-not-contain "forbidden text" `
  --agent-executable C:\path\to\claude.exe
```

Linux 将入口替换为 `backend/.runtime/linux/python/bin/agent-eval`，参数完全相同。`--agent-executable` 可省略，此时从 `PATH` 查找该 Agent 的默认命令。模型字符串直接传给所选 Agent；模型是否可用由该 Agent 的本地配置和服务端权限决定。

当前可选 Agent 包含 Multica 原生 backend，以及映射到 OpenClaw backend 的 `justdo` 入口。运行 `agent-eval agents` 可查看当前机器实际探测到的可执行文件。

`agent-eval agents` 和 `GET /api/agents` 同时返回 `capabilities`。其中
`specified_model_and_skill_evaluation=true` 才表示本地评测链路能同时注入指定 Skill 和选择
指定模型。Pinned Multica v0.4.36 的已知限制如下：

- `mcode`、`qwenpaw`、`zeroclaw` 的模型由 Agent 自身配置管理，不能按评测任务覆盖；默认的模型硬校验会提前拒绝。只有明确接受该限制时才使用 `--no-require-model-verification`。
- `dim`、`hermes`、`zeroclaw` 尚无本地直连评测运行时可用的 Skill 注入适配器，不能用于 Skill 评测。
- `detected_executable=null` 表示当前机器未安装对应 CLI，不能据“支持列表中有名称”宣称已经通过在线评测。

这些限制在创建运行目录和调用模型前检查，避免产出模型或 Skill 实际未生效的假阳性报告。

### 使用 JustDo Agent

JustDo 提供兼容 OpenClaw CLI 的本地 Agent launcher。保持 JustDo 运行或驻留托盘，
使用 `--agent justdo` 和模型 profile；评测系统会自动使用 OpenClaw backend，并生成只对
当前任务有效的模型配置。Windows 会自动发现开发 launcher，也可用
`JUSTDO_AGENT_EXECUTABLE` 或 `--agent-executable` 显式指定。

Windows 开发模式：

```powershell
Set-Location D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo
npm run multica:dev-agent
npm run electron:dev:openclaw

Set-Location D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup
.\backend\.runtime\windows\python\Scripts\agent-eval.exe run `
  --skill .\backend\skills\example-marker `
  --agent justdo `
  --profile litellm_opencode_go_minimax_2_7 `
  --agent-executable "$env:APPDATA\JustDo\multica\development\JustDo-agent.exe" `
  --case .\backend\skills\example-marker\evals\cases\marker.yaml `
  --parallelism 1 `
  --iterations 1 `
  --benchmark
```

Linux：

```sh
./backend/.runtime/linux/python/bin/agent-eval run \
  --skill ./backend/skills/example-marker \
  --agent justdo \
  --profile litellm_opencode_go_minimax_2_7 \
  --agent-executable "$HOME/.local/bin/JustDo-agent" \
  --case ./backend/skills/example-marker/evals/cases/marker.yaml \
  --parallelism 1 \
  --iterations 1 \
  --benchmark
```

## 用例和输出

最小用例：

```yaml
id: json-output
title: Generate expected JSON
input:
  prompt: Generate the requested artifact.
expect:
  must_contain:
    - '"schema_version"'
  must_not_contain:
    - traceback
```

每次运行会把源 Skill 复制到隔离目录：

```text
backend/evaluation_results/<用户>/<任务>/<时间>__<run_id>/
  staging/skill/            # 隔离副本与本次 eval.yaml
  skill-up/                 # Skill-Up JSON、HTML、JUnit、日志和 transcript
  model-interactions.json   # PostgreSQL 中与本次运行匹配的模型交互
  evaluation-report.json    # 本项目统一汇总
```

`evaluation-report.json` 的 `scores` 字段均为透明的确定性统计：

- `task_score`：有 Skill 时断言通过率，0–100。
- `baseline_score`：无 Skill 基准的断言通过率；未启用 benchmark 时为 `null`。
- `skill_gain`：`task_score - baseline_score`，衡量 Skill 带来的净提升。
- `execution_stability`：有 Skill 用例中成功完成评测流程的比例；PASS 和普通断言 FAIL 都算完成，运行错误/超时不算。
- `skill_quality_score`：对 Skill 名称、描述、流程、约束、产物、异常处理和验证说明的透明结构评分。
- `model_trace_score`：数据库匹配模型调用的成功率；未走 LiteLLM 或没有匹配记录时为 `null`。
- `model_verification_score`：数据库精确确认指定模型时为 100，否则为 0；详情见 `model_verification`。
- `total_tokens`、`total_duration_ms`：所有本次执行的资源统计。

默认 `--benchmark` 会同时运行有 Skill 和无 Skill 两组。只验证任务结果、不做基线时使用 `--no-benchmark`。增加 `--iterations N` 可用于稳定性评测。

## 无登录与无默认提示词保证

本地运行层只导入 `server/pkg/agent`，不会启动或引用 Multica 的 Web、daemon、auth、issue、数据库和任务提示词模块。执行时明确传入 `SystemPrompt: ""`。对应行为由 Go 单元测试覆盖，可运行：

```powershell
.\.runtime\windows\go\bin\go.exe test .\cmd\multica-eval-runtime
```

该命令需要在 `.runtime/windows/src/multica/server` 下执行。项目 Python 测试：

```powershell
.\.runtime\windows\python\Scripts\python.exe -m pytest
```
