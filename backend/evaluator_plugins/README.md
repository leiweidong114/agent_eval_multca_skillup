# 项目自带评测插件

本目录保存随项目发布并默认启用的正式评测插件。每个子目录必须包含
`evaluator.py`，并从该文件导出 `PLUGIN` 对象。插件由与内网扩展相同的注册器
扫描和校验。

- `schematic-default/`：三类原理图任务的公共默认评测插件。
- `../evaluator_plugins.example/`：只供复制参考，不会自动加载。
- `../extensions/evaluators/`：本机或内网私有插件，默认不提交 Git。
