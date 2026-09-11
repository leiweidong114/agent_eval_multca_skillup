# 本机/内网扩展目录

将不能进入公共仓库的 Skill 放入 `skills/<skill-id>/`，每个目录包含
`SKILL.md`。将评测插件放入 `evaluators/<evaluator-id>/`，每个目录包含并从
`evaluator.py` 导出 `PLUGIN`。

后端和设置页会自动扫描这两个目录，不需要再把固定目录写入 `.env`。目录内容
默认被 Git 忽略；`EXTERNAL_SKILL_PATHS_JSON` 和
`EVALUATOR_PLUGIN_PATHS_JSON` 仅用于加载位于其他位置的附加目录。
