# 私有评测器模板

将 `schematic-custom` 整个目录复制到内网目录，例如
`D:/private_eval_plugins/schematic-large`，然后修改其中的 `evaluator.py`。

根目录 `.env` 配置示例：

```dotenv
EVALUATOR_PLUGIN_PATHS_JSON=["D:/private_eval_plugins"]
DEFAULT_SCHEMATIC_EVALUATOR=schematic-large
```

每个评测器目录必须包含 `evaluator.py` 并导出一个 `PLUGIN` 对象。模板已经把
轨迹规则、结果规则和 Judge 提示词拆到了三个独立文件，它们通过相对导入组合。
插件只接收
标准化的上下文和证据，不应自行读取数据库、修改运行目录或管理 LiteLLM 密钥。
API 版本不兼容、ID 重复或评测类型不匹配时，系统会在执行前拒绝运行。

`evidence.artifact_manifest` 列出 Skill-Up 收集的产物；需要读取原理图 JSON、
网表或 ERC 报告时，使用 `evidence.resolve_artifact(relative_path)`。该方法只允许
解析 `artifact_root` 内的相对文件路径。插件应只读这些文件。

模板中的 `trace_rules.py` 和 `result_rules.py` 默认只产生结果页扩展信息。若专业
规则需要影响最终得分，应在 `evaluator.py` 中用规则返回的 `score` 替换
`dimensions["process"]` 或 `dimensions["result"]`。

插件还可以用 `schematic_task_types` 限定兼容任务：

```python
schematic_task_types = ("block_to_signal_list",)
```

未声明该字段的旧插件可用于全部三种原理图任务。

可通过 `GET /api/evaluators` 或 `agent-eval ... --evaluator schematic-large`
确认选择结果。
