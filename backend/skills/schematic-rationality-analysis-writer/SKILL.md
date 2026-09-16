---
name: schematic-rationality-analysis-writer
description: 生成一份结构化的原理图质量测试结果，并通过 schematicRationalityAnalysis 接口写入 MongoDB。用于验证原理图合理性分析数据链路，不代表真实工程质量结论。
---

# 原理图合理性分析写入

使用本 Skill 时必须真实执行 `scripts/submit_analysis.py`，不能只在回复中描述请求。

1. 优先读取 `AGENT_EVAL_SESSION_ID`；若 Agent 只能在结束后获知会话 ID，则使用运行时提供的 `AGENT_EVAL_RUN_ID`。历史指标 Worker 会通过 LiteLLM metadata 将运行 ID 精确回关联到真实会话。手工运行时必须通过 `--session-id` 明确提供；禁止编造关联 ID。
2. 生成符合 `references/result-text-schema.md` 的质量指标。测试记录必须使用 `checkType=synthetic_skill_test`。
3. 调用脚本写入服务。默认接口为 `http://127.0.0.1:8631/api/schematicRationalityAnalysis`，可用 `SCHEMATIC_RATIONALITY_API_URL` 覆盖。
4. 只有脚本返回 `status=stored` 才能声称写入成功，并在最终回复中给出 `id`、`uuid` 和 `sessionId`。

示例：

```bash
python skills/schematic-rationality-analysis-writer/scripts/submit_analysis.py
```

本 Skill 生成的是链路验证数据。不得把随机测试分数描述成对真实原理图的审计结论。
