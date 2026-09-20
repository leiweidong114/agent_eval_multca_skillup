# 历史会话指标计算与缓存

## 1. 数据来源与存储

“历史会话指标计算”默认查询最近 24 小时的 LiteLLM 普通会话。LiteLLM PostgreSQL
仍是模型交互事实源。评测系统产生的衍生指标和计算任务保存在项目本地 SQLite：

```text
backend/data/session_metrics.sqlite3
```

原理图合理性分析记录不由评测后端直连 MongoDB。后端通过 HTTP 查询接口读取
`HDschematicRationalityCollection`，并按真实 LiteLLM 根 `sessionId` 或评测运行 ID
关联到会话。

系统不再依赖 Nacos、Redis、MongoDB 驱动或基础设施 SSH 隧道。

## 2. `.env` 配置

```dotenv
# 只填写协议、目标服务器 IP/主机名和端口，不要包含查询路径。
SCHEMATIC_DATA_API_BASE_URL=http://10.0.0.8:8080
SCHEMATIC_DATA_QUERY_PATH=/schematic/schematicData/query
SCHEMATIC_DATA_API_TIMEOUT_SECONDS=15
SCHEMATIC_DATA_QUERY_PAGE_SIZE=20
SCHEMATIC_DATA_QUERY_MAX_PAGES=50

# 第二个写接口的契约确认后填写；当前查询功能不使用此项。
SCHEMATIC_DATA_WRITE_PATH=

SESSION_METRICS_SQLITE_PATH=backend/data/session_metrics.sqlite3
AGENT_EVAL_CACHE_TTL_SECONDS=300
AGENT_EVAL_CACHE_MAX_SIZE=1000
```

后端发出的查询等价于：

```powershell
curl.exe -G "http://10.0.0.8:8080/schematic/schematicData/query" `
  --data-urlencode "collectionName=HDschematicRationalityCollection" `
  --data-urlencode "page=1" `
  --data-urlencode "size=20"
```

评测后端还提供只读同源代理接口。它调用上面的 Java 接口并原样返回 Java 的 JSON
响应；该接口不要求 UI 登录 Cookie，因此浏览器和内网服务都可以直接调用：

```powershell
Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8000/api/schematic-data/query?collectionName=HDschematicRationalityCollection&page=1&size=20&refresh=true"
```

`refresh=false`（默认）使用进程内 TTL/LRU 缓存；`refresh=true` 强制调用 Java 接口。
Java 服务 IP 和端口仍只由根目录 `.env` 的 `SCHEMATIC_DATA_API_BASE_URL` 配置。

客户端兼容单条对象、JSON 数组和常见分页结构（`records/items/list/rows/content`，
可包在 `data` 或 `result` 内）。查询接口尚未提供 `sessionId` 服务端筛选，因此当前会
逐页读取后在本地匹配；可用 TTL/LRU 避免短时间重复请求。数据量增大后，建议接口
增加 `sessionId` 和按 `createTime` 倒序能力。

## 3. TTL/LRU 缓存

缓存位于后端 Python 进程内，线程安全：

- 每条数据独立 TTL，到期后自动回源。
- 命中时更新最近使用顺序。
- 超过 `AGENT_EVAL_CACHE_MAX_SIZE` 后淘汰最久未使用数据。
- 重启后端会清空缓存，不影响 SQLite 指标结果。
- 多后端进程不共享缓存，各进程独立回源。

## 4. 指标计算

规则代码计算工具/脚本调用成功率、Skill 步骤完成度、错误与重试。启用 LLM Judge
时，每条会话首先把时间最早请求中的第一条 `user` Prompt 单独送入分类 Judge，分类
只依据用户原始意图，不读取 Agent 后续执行结果。分类结果保存到 SQLite 的
`task_type`、`task_category`、`task_subtype` 和完整指标 JSON 中。

分类枚举如下：

- 原理图生成任务：框图生成原理图、框图生成信号接口列表、信号接口列表生成原理图、
  原理图应用到天枢。
- 原理图调整任务。
- 其他原理图任务。
- 其他任务。

随后完整会话按分片送入指标 Judge，分析语义错误恢复及疑似伪造输出，最终由代码
去重、校验真实 request ID 并合并。任一 Judge 不可用时仍保存能够确定的规则指标和
错误原因。分类 Judge 的请求与响应使用 `session_task_classification` 用途记录，可在
“Judge 交互记录”页面单独筛选。

自动任务配置保持不变：

```dotenv
SESSION_METRICS_AUTO_ENABLED=true
SESSION_METRICS_AUTO_INTERVAL_SECONDS=3600
SESSION_METRICS_AUTO_INITIAL_DELAY_SECONDS=60
SESSION_METRICS_AUTO_MAX_SESSIONS=100
SESSION_METRICS_AUTO_USE_LLM_JUDGE=true
SESSION_METRICS_AUTO_USER_ID=system
```

## 5. 后端接口

以下示例假设后端为 `http://127.0.0.1:8000`，`cookies.txt` 已保存登录 Cookie。

```powershell
# 健康状态：SQLite、TTL/LRU、外部查询接口、自动调度器
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/health"

# 最近 24 小时会话
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/sessions?limit=20&offset=0"

# 批量计算
curl.exe -b cookies.txt -X POST "http://127.0.0.1:8000/api/session-metrics/jobs" `
  -H "Content-Type: application/json" `
  -d '{"session_ids":["会话ID"],"start_time":"2026-09-16T12:00:00+08:00","end_time":"2026-09-17T12:00:00+08:00","use_llm_judge":true}'

# 任务进度、列表、汇总、单会话详情
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/jobs/任务ID"
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics?limit=20&offset=0"
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/summary"
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/会话ID"

# 原理图生成总览按分类筛选；也可传具体子类 block_to_schematic 等
curl.exe -b cookies.txt -G "http://127.0.0.1:8000/api/schematic/conversations" `
  --data-urlencode "task_classification=schematic_generation" `
  --data-urlencode "limit=20" `
  --data-urlencode "offset=0"
```

Java 运营系统应调用这些只读 API，不直接读取 SQLite 或 MongoDB，以便复用登录鉴权、
字段兼容、默认 24 小时时间窗和指标版本逻辑。
