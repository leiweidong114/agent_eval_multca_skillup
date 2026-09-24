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

## 查询、插入和删除的 curl 调用

Java 接口可以直接调用，Python 后端同时提供同源代理。查询使用 URL 参数；插入和
删除使用 JSON body。`collectionName` 必须放在插入、删除请求的 body 中。

```powershell
$java = "http://127.0.0.1:18081/schematic/schematicData"
$python = "http://127.0.0.1:8000/api/schematic-data"

# 1. 直接查询 Java
curl.exe -G "$java/query" `
  --data-urlencode "collectionName=HDschematicRationalityCollection" `
  --data-urlencode "page=1" --data-urlencode "size=20" `
  --data-urlencode "sessionId=实际会话ID"

# 2. 通过 Python 查询 Java
curl.exe -G "$python/query" `
  --data-urlencode "collectionName=HDschematicRationalityCollection" `
  --data-urlencode "page=1" --data-urlencode "size=20" `
  --data-urlencode "sessionId=实际会话ID" --data-urlencode "refresh=true"

# 将下面 JSON 保存为 insert.json 后，可直接插入 Java
curl.exe -X POST "$java/insert" -H "Content-Type: application/json" `
  --data-binary "@insert.json"

# 同一个 insert.json 也可以通过 Python 插入
curl.exe -X POST "$python/insert" -H "Content-Type: application/json" `
  --data-binary "@insert.json"

# Python 删除代理只允许按 _id 精确删除
curl.exe -X DELETE "$python/delete" -H "Content-Type: application/json" `
  -d '{"collectionName":"HDschematicRationalityCollection","_id":"MongoDB的24位_id"}'
```

`insert.json` 必须包含 `collectionName` 和完整业务记录，例如：

```json
{
  "collectionName": "HDschematicRationalityCollection",
  "uuid": "唯一UUID",
  "status": "completed",
  "createUser": "100001",
  "createTime": "2026-09-24T08:00:00Z",
  "checkType": "hscope_diagram_lint",
  "checkMessage": "原理图质量分析",
  "userName": "测试用户",
  "hscopeProjectId": "project-demo",
  "boardNum": "BOARD-001",
  "sessionId": "实际会话ID",
  "resultText": "{}"
}
```

## Java 删除接口

`DELETE /schematic/schematicData/delete` 的 JSON body 支持三种互斥选择器：

- `_id`：MongoDB 24 位十六进制 `_id`，精确删除一条；
- `sessionId`：删除该会话在集合中的**全部记录**，可能不止一条。
- `uuid`：删除具有该业务 UUID 的记录。

`collectionName` 是目标集合，不是删除选择器，不能单独用于清空集合。只允许集合
`HDschematicRationalityCollection`。缺失选择器、同时传多个选择器、
非法 `_id` 返回 HTTP 400；目标不存在返回 HTTP 404；成功返回
`{"status":"deleted","selector":"_id|sessionId|uuid","value":"...","deletedCount":1}`。
删除不可恢复，操作前应先用 `/query` 核对目标并做好备份。

Java 删除 curl 示例：

```powershell
$base = "http://127.0.0.1:18081/schematic/schematicData"

# 精确删除一条
curl.exe -X DELETE "$base/delete" -H "Content-Type: application/json" `
  -d '{"collectionName":"HDschematicRationalityCollection","_id":"MongoDB的24位_id"}'

# 删除该 Session ID 下全部记录
curl.exe -X DELETE "$base/delete" -H "Content-Type: application/json" `
  -d '{"collectionName":"HDschematicRationalityCollection","sessionId":"会话ID"}'

# 按 UUID 删除
curl.exe -X DELETE "$base/delete" -H "Content-Type: application/json" `
  -d '{"collectionName":"HDschematicRationalityCollection","uuid":"业务UUID"}'
```
