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

可通过 `GET /api/evaluators` 或 `agent-eval ... --evaluator schematic-large`
确认选择结果。
