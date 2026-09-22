# 原理图 MongoDB Java 接口

Java 源码位于同级 `原理图_java/src/main/java/com/agent/eval/schematic/`；
评测系统仓库保留新增接口的源码副本于 `backend/patches/schematic-data-java/`，
移植或重建时需将副本放到 Java 项目相应的 `src/main/java` 目录。

## 累计指标：只保留一条

`PUT /schematic/schematicData/aggregate` 只针对
`HDschematicRationalityCollection` 中 `sessionId=汇总结果` 且
`checkType=agent_eval_quality_aggregate` 的记录。使用原子 upsert 和局部唯一索引，
首次创建后每次更新相同 MongoDB `_id`，返回 `matchedCount=1`、`recordId`。
评测后端通过 `SchematicDataClient.upsert_aggregate_metrics()` 调用此接口。

## 删除：默认不启用

`DELETE /schematic/schematicData/delete` 支持两种互斥选择器：

- `id`：MongoDB 24 位十六进制 `_id`，精确删除一条；
- `sessionId`：删除该会话在集合中的**全部记录**，可能不止一条。

只允许集合 `HDschematicRationalityCollection`。缺失、同时传两个选择器、
非法 `_id` 返回 HTTP 400；目标不存在返回 HTTP 404；成功返回
`{"status":"deleted","selector":"id|sessionId","value":"...","deletedCount":1}`。
删除不可恢复，操作前应先用 `/query` 核对目标并做好备份。

当前 Nginx 对外代理 `/schematic/`，且 Java 服务未提供接口鉴权。
因此删除控制器带有 `schematic.data.delete-enabled` 开关，默认关闭；
**未确定访问控制和部署范围之前，不要设置** `SCHEMATIC_DATA_DELETE_ENABLED=true`。
启用方案需要由系统所有者明确确认。下面命令仅用于启用后的受控环境：

```powershell
$base = "http://127.0.0.1:18081/schematic/schematicData"

# 先核对会话及记录数
curl.exe -G "$base/query" --data-urlencode "sessionId=会话ID" `
  --data-urlencode "page=1" --data-urlencode "size=100"

# 精确删除一条
curl.exe -X DELETE -G "$base/delete" --data-urlencode "id=MongoDB的_id"

# 删除该 Session ID 下全部记录
curl.exe -X DELETE -G "$base/delete" --data-urlencode "sessionId=会话ID"
```
