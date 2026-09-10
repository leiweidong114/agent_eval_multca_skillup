---
name: schematic-web-apply
description: 将一个或多个布局 JSON 应用到网页：请求 apply_schematic 服务按"每个布局一个图页、左侧栏切换"生成网页，返回可直接打开的 URL。适用于 schematic-pipeline 的步骤三。
---

# 原理图网页应用 (schematic-web-apply)

## 目标
把 skill 二产出的布局 JSON 文件（Service 1 结果，含坐标/连线）渲染成网页并拿到 URL。

## 前置
- apply_schematic 服务运行于 `http://127.0.0.1:8631`（env `AUTOLAYOUT_URL` 可改）。
- 以下命令一律从评测 workspace 根目录执行，不要先 `cd` 到 Skill 或 scripts 目录。将包含本文件的目录记作 `<skill-root>`，脚本使用该目录的绝对路径。

## 用法
```bash
# 方式一：显式列出 图页标题=JSON路径
python <skill-root>/scripts/apply.py --title "STM32 LED 原理图" \
  --sheet "STM32_LED|out/layout/S1.json" \
  --out out/apply_result.json

# 方式二：扫描布局目录（每文件一图页，按文件名排序）
python <skill-root>/scripts/apply.py --title "板级原理图" --layout-dir out/layout --out out/apply_result.json
```
成功输出 `apply_result.json`，含服务返回的 `url` 和 `url_verification.status=ok`。脚本默认实际 GET 该 URL 验证 HTTP 200 后才成功；把 URL 交给用户/主流程。

## 说明
- 每个布局 JSON 是一个 sheet/图页；页面左侧为图页栏，右侧为可缩放拖拽的画布。
- `url` 可直接在浏览器打开，也可作为最终交付链接；服务重启不影响已生成的网页（静态文件）。
- 若同一批文件里有多个主芯片板（多 sheet），每 sheet 调用一次 skill 二后，一并在此渲染成多图页网页。
- 失败时请把 HTTP detail 回传主流程。
- `apply_result.json` 与 URL 只能来自服务响应，禁止手写或猜测 `/api/...` 地址冒充网页结果。
