# Schematic Data Service

查询和写入 MongoDB 中的原理图合理性分析记录。使用 Maven 构建，运行时要求 JDK 21。

## 接口

```text
GET /schematic/schematicData/health
GET /schematic/schematicData/query?collectionName=HDschematicRationalityCollection&page=1&size=20
POST /schematic/schematicData/insert?collectionName=HDschematicRationalityCollection
PUT /schematic/schematicData/update?collectionName=HDschematicRationalityCollection&sessionId=<session>&checkType=<analysis-type>
```
写入接口接收 JSON，请求必须包含 `uuid`、`status`、`createUser`、`createTime`、
`checkType`、`checkMessage`、`userName`、`hscopeProjectId`、`boardNum`、`sessionId`
和 `resultText`。`_id` 由 MongoDB 生成，`createTime` 使用 ISO-8601。

分页响应包含 `records`、`total`、`page`、`size`、`collectionName` 和
`resolvedCollectionName`。服务只允许查询规范集合
`HDschematicRationalityCollection`，不再回退到历史拼写错误的集合。

更新接口只允许更新已有文档的 `agentEvalMetrics` 或 `agentEvalProcess` 字段，
请求体为 `{"field":"agentEvalMetrics","value":{...}}`。按 `sessionId` 和分析类型
`checkType` 定位该类型最新的原始文档，不要求源文档有 `uuid`；匹配不到返回 404，
绝不插入新文档，也不覆盖原始 `resultText`。

## 构建

```bash
export JAVA_HOME=/path/to/jdk-21
mvn clean package
```

## 运行

```bash
export MONGODB_USERNAME='user'
export MONGODB_PASSWORD='password'
# 可选：MONGODB_HOST、MONGODB_PORT、MONGODB_DATABASE、MONGODB_AUTH_DATABASE
java -jar target/schematic-data-service-1.0.0.jar
```
