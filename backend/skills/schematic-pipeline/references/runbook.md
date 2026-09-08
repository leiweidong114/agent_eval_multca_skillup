# 端到端操作手册（供主 agent 对照）

## A. 一次性环境准备
```powershell
# 1) 服务启动（位于自动布局算法目录）
python auto_layout_service/main.py
# 2) 健康检查
curl http://127.0.0.1:8631/api/health
# 3) 器件目录（供 skill1 生成与校验）
python <skill1>/scripts/fetch_catalog.py --out out/catalog.json --with-pins
```

## B. skill1 → sheets
1. 主 agent 依据自然语言 + catalog.json 生成 `out/sheets.json` 与 `out/sheets_markdown/*.md`。
2. 校验：`python <skill1>/scripts/validate_sheets.py --input out/sheets.json --catalog out/catalog.json`
3. 通过后再进入下一步。

## C. skill2 → 布局（subagent 并行模式）
对每个 `sheet_id in sheets.sheets`：
```powershell
mkdir out/frags
python <skill2>/scripts/codegen_base.py --input out/sheets.json --sheet <id> --fragdir out/frags/<id>/
```
- 阅读 `out/frags/<id>/slices.json`：把切片两两分组；
- 为每组**并行 spawn 2 个 subagent**，任务模板：
  > “你是电路代码生成 subagent。读取 `out/frags/<id>/slices.json` 中 slice_id=XXXX 的切片，以及 `<skill2>/references/python_codegen_guide.md`。把 connect 代码写入 `out/frags/<id>/XXXX.py`，只写 connect，不写 Template/Net。完成后用 ast.parse 自查。”
- 一批完成后 spawn 下一批，直到清空；
- 布局：`python <skill2>/scripts/layout_sheet.py --input out/sheets.json --sheet <id> --fragdir out/frags/<id>/ --out out/layout/<id>.json --iterations 40`
- 快速拓扑验证建议 `--iterations 25`；最终交付 `>=60`。
- 失败 → 把 HTTP detail 给"新 subagent 修复 <slice_id>.py"。

## D. skill3 → URL
```powershell
python <skill3>/scripts/apply.py --title "<板名>" --layout-dir out/layout --out out/apply_result.json
```
读取 `out/apply_result.json.url`。

## E. 交付检查
对照 SKILL.md 的判定标准逐项自检；给用户网页 URL + 汇总表。

## 常见故障
| 现象 | 处理 |
|---|---|
| fetch/apply HTTP 连不上 | 服务没起；`python auto_layout_service/main.py` |
| 422 器件库外 | 回 skill1 修 sheets（替代器件或扩库）|
| 422 ambiguous pin | 检查是否用了重复 pin name；必须用 number |
| 422 `No feasible non-dominated layout found` | 小功能组过多且并入主域所致。skill2 默认 `subcircuit_min_components=2`；仍失败则合并相近小组 |
| unrouted>0 | 提高 iterations / 放大 canvas / 检查跨页单端网络 |
| subagent 产出占位注释 | 该切片未完成，重启 subagent 补写 |
