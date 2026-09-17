# 测试脚本说明

本目录把快速回归、真实模型验收和浏览器验收分开。真实模型脚本会产生 LiteLLM 请求，不由 `pytest` 自动收集。

## 推荐执行顺序

1. `python -m pytest backend/tests -q`：后端单元与接口回归。
2. `cd frontend; npm run build`：前端类型检查与生产构建。
3. `python test/verify_system.py --phase connectivity --model <model>`：六 Agent 的真实 Prompt、精确模型和数据库轨迹验收。
4. `python test/run_skill_agent_matrix.py --model <model> --no-judge`：以覆盖模式让每个 Skill 和每个 Agent 至少被运行一次。
5. `python test/run_smart_street_light_e2e.py --all-agents --model <model>`：六 Agent 完整原理图生成与插件评分。
6. `node test/browser_smoke.cjs`：登录、核心页面、结果详情和模型交互弹窗验收。

## 主要脚本

### `verify_system.py`

- `--phase connectivity`：只发最小 Prompt，要求 Agent 成功退出、数据库存在成功调用且实际模型匹配。
- `--phase evaluation`：执行示例 Skill 评测，同时检查评测插件验收结果。
- `--workers` 控制并发；`--output` 保存结构化证据；`--no-judge` 关闭付费 Judge，但保留规则和插件评分。
- 默认先检查前端、后端、自动布局服务；只有明确知道服务状态时才使用 `--skip-service-preflight`。

### `run_skill_agent_matrix.py`

- 默认是覆盖模式：轮转 Agent，使全部选中 Skill 和全部已安装 Agent 至少出现一次。
- `--full-cross-product` 执行完整 Skill × Agent 笛卡尔积，费用和耗时显著增加。
- `--skill`、`--agent` 可重复传入以缩小范围。
- `--resume` 会复用输出目录中已完成的单元，适合长时间验收中断后续跑。
- 每个单元单独保存 JSON，汇总写入 `summary.json`；通过条件包含 Agent 执行、精确模型、评测插件以及可选 Judge。

### `run_smart_street_light_e2e.py`

- 默认单 Agent；`--all-agents` 覆盖全部已安装 Agent。
- 验收四个原理图 Skill、必要文件、布局结果、网页 URL、模型轨迹与 `schematic-default` 插件结果。
- `--validate-only` 只校验 Skill 和评测配置，不产生模型费用。

### `browser_smoke.cjs`

- 自动登录测试账号（若出现登录页）。
- 检查首页、模型与 Agent、Skill、新建评测、结果列表、结果详情和原理图总览。
- 打开真实会话交互弹窗，并检查 `Request & Response`、`Input`、`Output` 结构。
- 默认前端地址为 `http://127.0.0.1:5173`，可用 `FRONTEND_URL` 覆盖。

## 结果判定原则

- `connected` 只证明 Prompt 调用和模型归因成功，不代表 Agent 能完成复杂 Skill。
- Skill/原理图任务必须同时满足产物规则、Skill 轨迹和模型验证；不能用最终回复中的自述代替真实文件或数据库证据。
- Agent 没按已正确注入的 Skill 执行，应保留为 Agent/模型能力失败，不能修改评分器把它伪装成系统成功。
- LiteLLM 429、网络错误和数据库不可达属于外部/基础设施失败，应和 Agent 输出不合格分开统计。
