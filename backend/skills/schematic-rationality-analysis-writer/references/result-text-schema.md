# resultText 数据结构

`resultText` 使用 JSON 对象，分数范围为 0～100，数量为非负整数：

```json
{
  "schema_version": "1.0",
  "overall_score": 82,
  "electrical_correctness": 85,
  "component_selection": 78,
  "signal_integrity": 75,
  "power_integrity": 88,
  "protection_completeness": 80,
  "layout_readability": 86,
  "unrouted_net_count": 0,
  "overlap_count": 0,
  "erc_error_count": 2,
  "warning_count": 3,
  "quality_level": "good",
  "summary": "测试生成的原理图质量数据",
  "issues": []
}
```

允许为了测试在合法范围内改变数值，但必须保留 `schema_version`，并明确说明数据为 synthetic test。
