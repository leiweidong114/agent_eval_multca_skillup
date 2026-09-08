# 原理图生成评测流程与产物说明

## 1. 前端发起的真实评测流程

“新建评测 → 原理图评测”不是 `schematic_demo` 的固定脚本演示，而是一次真实的 Agent + 指定模型 + 四 Skill 联合评测：

```text
用户 Prompt
  → schematic-pipeline（总编排）
  → signal-interface-generation（主 Agent 生成并校验 sheets.json）
  → schematic-layout-codegen（每批并行 2 个 subagent 生成切片并自动布局）
  → schematic-web-apply（布局 JSON 应用为多图页网页）
  → 规则评分 + 过程评分 + Skill 质量评分 + LiteLLM LLM Judge
  → evaluation-report.json + 模型交互 + 前端结果页
```

提交时系统固定装载以下四个 Skill：

1. `schematic-pipeline`
2. `signal-interface-generation`
3. `schematic-layout-codegen`
4. `schematic-web-apply`

前端提供“填入示例 Prompt”按钮，可直接生成 STM32F103C8T6 最小系统测试任务。

## 2. 已预置的 YAML 用例

每个 YAML 都依据对应 `SKILL.md` 的输入、产物契约和质量门槛编写：

| Skill | YAML | 主要验证内容 |
|---|---|---|
| schematic-pipeline | `backend/skills/schematic-pipeline/evals/cases/stm32-minimum-system-e2e.yaml` | 四阶段完整链路、2 个 subagent/批、布局指标、网页 URL |
| signal-interface-generation | `backend/skills/signal-interface-generation/evals/cases/stm32-led-interface.yaml` | 实时器件目录、sheets.json、Markdown、schema 校验 |
| schematic-layout-codegen | `backend/skills/schematic-layout-codegen/evals/cases/stm32-led-layout.yaml` | base/slices、subagent 切片代码、自动布局质量门槛 |
| schematic-web-apply | `backend/skills/schematic-web-apply/evals/cases/single-sheet-web-apply.yaml` | 多图页应用接口、project_id、URL、页数 |

这些用例依赖 `http://127.0.0.1:8631` 的 auto_layout/apply_schematic 服务。服务不通时应报告基础设施失败，不能把它计为 Agent 能力失败或伪造成功。

## 3. 中间文件与最终报告位置

一次真实原理图评测的根目录为：

```text
backend/evaluation_results/<user_id>/<task_name>/<timestamp>__<run_id>/
```

关键内容包括：

```text
evaluation-report.json          最终评分与运行证据
model-interactions.json         从 LiteLLM 数据库关联的完整模型请求/响应
staging/skill/                  本次运行使用的只读 Skill 快照与 eval.yaml
runtime/                        Agent 隔离配置和运行期文件
skill-up/iteration-*/           每次迭代的结果、transcript 与 Agent 工作产物
```

Agent 按 pipeline 契约生成的 `out/` 一般位于对应 iteration 的隔离工作区内，内容应包括：

```text
out/catalog.json
out/sheets.json
out/sheets_markdown/
out/frags/<sheet_id>/base.txt
out/frags/<sheet_id>/slices.json
out/frags/<sheet_id>/*.py
out/layout/<sheet_id>.json
out/apply_result.json
```

结果详情页提供“一键打开原理图项目文件夹”，直接打开本次 `<timestamp>__<run_id>` 目录。运行结束后，“模型交互记录”读取持久化的 `model-interactions.json`，逐次展示完整请求、响应、思考内容、工具定义、状态、耗时与 Token；运行期间则显示实时 transcript/事件流。

## 4. 与确定性预览接口的区别

`POST /api/schematic/generate` 是不调用 Agent/LLM 的确定性预览工具，产物在：

```text
backend/schematic_projects/<project_id>/
```

它适合快速演示和固定 JSON 判分，不等同于四 Skill 全流程评测，也不能用它的成功替代指定 Agent、指定模型、subagent 和 LLM Judge 的验证。
