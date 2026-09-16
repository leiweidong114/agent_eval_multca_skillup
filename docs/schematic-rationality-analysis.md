# 原理图合理性分析数据链路

## 数据流

1. Agent 使用 `schematic-rationality-analysis-writer` Skill 调用自动布局服务的 `POST /api/schematicRationalityAnalysis`。
2. 自动布局服务将记录写入 MongoDB 集合 `HDschematicRationalilyCollection`。
3. “历史会话指标计算” Worker 先按 LiteLLM 根会话 ID 查询；如果 Agent 运行时尚不知道最终会话 ID，则按 LiteLLM metadata 中的 `evaluation_run_ids` 查询。
4. Worker 将最新一条状态为 `completed`、`success` 或 `ok` 的 `resultText` 交给默认 Judge LLM。
5. 结构化数值由规则解析并作为权威值，Judge 只补齐缺失指标、质量等级、中文摘要和问题清单。结果与原有会话指标一并保存到 `session_metrics_latest` 和版本集合。

浏览器不会直连 MongoDB。API、指标 Worker 和自动布局服务使用同一份 Nacos `mongodb.uri` / `mongodb.database` 配置，也可分别通过环境变量覆盖。

## 会话关联

- 首选：`sessionId = LiteLLM root_session_id`。
- 兼容方式：`sessionId = AGENT_EVAL_RUN_ID`。Worker 使用会话里的 `evaluation_run_ids` 回关联，并将 `matched_by` 保存为 `evaluation_run_id`。
- 手工执行 Skill 脚本时，必须显式传入 `--session-id`。

## Judge 输入和合并

Judge 仅接收一条 MongoDB 记录的 `resultText`，最大 64 KiB 进入模型上下文。系统提示明确把内容视为不可信数据，要求只输出 JSON、不得臆造缺失数值。若 `resultText` 本身是 JSON，代码先确定性提取已知数值，并覆盖 Judge 对同名数值的推断。

当前标准指标包括综合质量、电气正确性、器件选型、信号完整性、电源完整性、保护完整性、布局可读性，以及未布线网络、重叠、ERC 错误和告警数量。Judge 调用会写入独立的 Judge 审计记录，purpose 为 `schematic_rationality_judge`。

## Skill 手工验证

```powershell
$env:SCHEMATIC_RATIONALITY_API_URL = "http://127.0.0.1:8631/api/schematicRationalityAnalysis"
python .\backend\skills\schematic-rationality-analysis-writer\scripts\submit_analysis.py --session-id "真实会话ID"
```

生成内容标记为 `synthetic_skill_test`，只用于验证链路，不应被解释为真实工程检查结论。
