# Agent Eval 项目目录规范

本项目只保留一套后端源码、运行时、Skill 和评测结果目录。`backend/` 是后端能力的唯一根目录，
仓库根目录不再放置 `.runtime`、`.tools`、`skills` 或 `runs` 的有效副本。

## 规范目录

```text
agent_eval_multca_skillup/
├── backend/
│   ├── app/                    # FastAPI 应用和 REST API
│   ├── config/                 # 非敏感配置；本机密钥文件被 Git 忽略
│   ├── src/agent_eval/         # CLI 与评测核心
│   ├── skills/                 # 唯一的内置/上传 Skill 根目录
│   ├── scripts/                # Windows/Linux 安装和维护脚本
│   ├── tests/                  # Python 测试
│   ├── .runtime/               # 生成：Python、Go、Multica、组合 Skill
│   ├── .tools/                 # 生成：Skill-Up 等工具
│   ├── evaluation_results/     # 生成：正式任务、Job、Batch 和报告
│   ├── model_eval_data/        # 生成：Prism SQLite、密钥和结果
│   └── schematic_projects/     # 生成：原理图演示工程
├── frontend/                   # Vue/Vite 前端
├── docs/                       # 项目文档
├── test/                       # 系统级验证脚本
├── tests_reports/              # 可提交的测试证据和分析报告
├── .env                        # 本机凭据，禁止提交
└── .env.example                # 无真实凭据的模板
```

## 路径规则

- `backend/app/config.py` 定义 `backend/skills` 和 `backend/evaluation_results`。
- `agent_eval.cli.PROJECT_ROOT` 固定解析到 `backend/`。
- Windows/Linux setup 脚本只向 `backend/.runtime` 和 `backend/.tools` 安装。
- 运行时发现不再回退到仓库根目录，避免误用旧二进制。
- 前端不直接访问磁盘路径，只通过后端 API 读取 Skill、任务和结果。
- JustDo 源码位于相邻独立仓库；评测系统通过 `%APPDATA%` 中重新构建的 launcher 发现它。

## 生成目录与源码目录

`backend/.runtime`、`backend/.tools`、`backend/evaluation_results`、
`backend/model_eval_data` 和 `backend/schematic_projects` 都是本机生成数据，已被 Git 忽略。
删除运行时后可执行 setup 脚本重建，但删除评测结果或 Prism 数据会丢失历史证据，清理前必须备份。

`backend/skills` 是源码和注册数据的统一入口，其中仓库内置 Skill 由 Git 管理，网页上传版本位于
被忽略的 `backend/skills/.registry`。不要再在仓库根目录创建 `skills/`。

## 历史目录迁移

2026-09-08 结构归一时执行：

- 根目录 `runs/` → `backend/evaluation_results/legacy-root-runs/`；
- `backend/runs/` → `backend/evaluation_results/legacy-backend-runs/`；
- 根目录旧 `.runtime/`、`.tools/` 移到项目外的同盘备份目录，未直接删除；
- 根目录重复 `skills/example-marker` 已在确认与后端副本哈希一致后移除。

历史报告仍位于 `backend/evaluation_results` 下，API 会递归发现其中的
`evaluation-report.json`。旧运行时备份只用于短期回滚，不应复制到新电脑；新电脑统一运行 setup
重新构建。

## 日常检查

```powershell
git status --short --branch
agent-eval doctor
agent-eval skills
agent-eval results
```

如果仓库根目录再次出现 `.runtime`、`.tools`、`skills` 或 `runs`，通常表示调用了旧脚本、旧版
全局 `agent-eval`，或手工把输出目录指向根目录。先用 `Get-Command agent-eval` 确认入口，再改用
`backend/.runtime/windows/python/Scripts/agent-eval.exe`。
