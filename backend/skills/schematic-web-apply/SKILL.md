---
name: schematic-web-apply
description: 将一个或多个布局 JSON 应用到网页：请求 apply_schematic 服务按"每个布局一个图页、左侧栏切换"生成网页，返回可直接打开的 URL。适用于 schematic-pipeline 的步骤三。
---

# 原理图网页应用 (schematic-web-apply)

## 目标
把 skill 二产出的布局 JSON 文件（Service 1 结果，含坐标/连线）渲染成网页并拿到 URL。

## 前置
- apply_schematic 服务运行于 `http://127.0.0.1:8631`（env `AUTOLAYOUT_URL` 可改）。

## 用法
```bash
# 方式一：显式列出 图页标题=JSON路径
python scripts/apply.py --title "STM32 LED 原理图" \
  --sheet "STM32_LED|out/layout/S1.json" \
  --out out/apply_result.json

# 方式二：扫描布局目录（每文件一图页，按文件名排序）
python scripts/apply.py --title "板级原理图" --layout-dir out/layout --out out/apply_result.json
```
成功输出 `apply_result.json`，含 `url`。把 URL 交给用户/主流程。

## 说明
- 每个布局 JSON 是一个 sheet/图页；页面左侧为图页栏，右侧为可缩放拖拽的画布。
- `url` 可直接在浏览器打开，也可作为最终交付链接；服务重启不影响已生成的网页（静态文件）。
- 若同一批文件里有多个主芯片板（多 sheet），每 sheet 调用一次 skill 二后，一并在此渲染成多图页网页。
- 失败时请把 HTTP detail 回传主流程。
