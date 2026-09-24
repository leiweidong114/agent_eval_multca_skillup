# 历史会话指标计算与缓存

## 1. 数据来源与存储

“历史会话指标计算”默认查询最近 24 小时的 LiteLLM 普通会话。LiteLLM PostgreSQL
仍是模型交互事实源。计算完成的衍生指标通过 Java HTTP 接口写入 MongoDB 的
`HDschematicRationalityCollection`，使用原会话 `sessionId` 关联。原始质量报告保持不变；
每次成功计算只保留一条 `agent_eval_metric` 记录。写入前按 MongoDB `_id`
精确删除同一 Session 下旧的 `agent_eval_metric`、`agent_eval_session_metrics`
和 `agent_eval_metric_process`，不会删除四类原始质量记录。该记录的
`resultText` 为空，`agentEvalMetrics` 保存四类质量检查汇总 JSON，
`agentEvalProcess` 保存可回看的计算过程。失败且尚未产生最终指标时，才单独保存
一条 `agent_eval_metric_process` 故障过程记录。
项目本地不再保存 SQLite 指标数据库；旧版本完成的计算若没有过程记录，需重新计算才能回看。

原理图合理性分析记录不由评测后端直连 MongoDB。后端通过 HTTP 查询接口读取
`HDschematicRationalityCollection`，并按真实 LiteLLM 根 `sessionId` 或评测运行 ID
关联到会话。

系统不再依赖 Nacos、Redis、MongoDB 驱动或基础设施 SSH 隧道。

## 2. `.env` 配置

```dotenv
# 只填写协议、目标服务器 IP/主机名和端口，不要包含查询路径。
SCHEMATIC_DATA_API_BASE_URL=http://10.0.0.8:8080
SCHEMATIC_DATA_QUERY_PATH=/schematic/schematicData/query
SCHEMATIC_DATA_API_COOKIE=JSESSIONID=请替换为实际值
SCHEMATIC_DATA_WRITE_PATH=/schematic/schematicData/insert
SCHEMATIC_DATA_UPDATE_PATH=/schematic/schematicData/update
SCHEMATIC_DATA_API_TIMEOUT_SECONDS=15
SCHEMATIC_DATA_QUERY_PAGE_SIZE=20
SCHEMATIC_DATA_QUERY_MAX_PAGES=50

AGENT_EVAL_CACHE_TTL_SECONDS=300
AGENT_EVAL_CACHE_MAX_SIZE=1000
```

后端发出的查询等价于：

```powershell
curl.exe -G "http://10.0.0.8:8080/schematic/schematicData/query" `
  -k `
  --data-urlencode "collectionName=HDschematicRationalityCollection" `
  --data-urlencode "page=1" `
  --data-urlencode "size=20"
```

评测后端还提供只读同源代理接口。它调用上面的 Java 接口并原样返回 Java 的 JSON
响应。当前内网接口不要求 Cookie，`.env` 中的 `SCHEMATIC_DATA_API_COOKIE` 保持为空：

```powershell
Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8000/api/schematic-data/query?collectionName=HDschematicRationalityCollection&page=1&size=20&refresh=true"
```

`refresh=false`（默认）使用进程内 TTL/LRU 缓存；`refresh=true` 强制调用 Java 接口。
Java 服务 IP 和端口仍只由根目录 `.env` 的 `SCHEMATIC_DATA_API_BASE_URL` 配置。
Python 转发客户端对查询和插入都显式使用 `verify=False`，因此也可以访问使用内网
自签名证书的 HTTPS Java 服务。

写入接口通过 Python 客户端调用。下面的端到端脚本会生成一条
`hscope_diagram_lint` 测试数据，写入 MongoDB，再按同一会话 ID 回读校验：

```powershell
.\backend\.runtime\windows\python\Scripts\python.exe `
  .\test\schematic_data_insert_e2e.py `
  --session-id "替换为LiteLLM真实会话ID" `
  --employee-no "100001"
```

Python 插入代理接口不要求 UI 登录 Cookie：

```text
POST /api/schematic-data/insert
```

完整 Python 代理接口为：

```powershell
# 查询（Python 到 Java 的 HTTPS 请求固定 verify=False）
curl.exe -G "http://127.0.0.1:8000/api/schematic-data/query" `
  --data-urlencode "collectionName=HDschematicRationalityCollection" `
  --data-urlencode "page=1" `
  --data-urlencode "size=20" `
  --data-urlencode "sessionId=实际会话ID" `
  --data-urlencode "refresh=true"

# 插入（无需登录 Cookie；Python 到 Java 固定 verify=False）
curl.exe -X POST "http://127.0.0.1:8000/api/schematic-data/insert" `
  -H "Content-Type: application/json" `
  -d '{"collectionName":"HDschematicRationalityCollection","documents":[{"uuid":"唯一UUID","status":"completed","createUser":"100001","createTime":"2026-09-21T08:00:00Z","checkType":"hscope_diagram_lint","checkMessage":"原理图质量分析","userName":"测试用户","hscopeProjectId":"project-demo","boardNum":"BOARD-001","sessionId":"实际会话ID","resultText":"{}"}]}'

# 删除（Python 代理只接受 MongoDB _id）
curl.exe -X POST "http://127.0.0.1:8000/api/schematic-data/delete" `
  -H "Content-Type: application/json" `
  -d '{"collectionName":"HDschematicRationalityCollection","_id":"MongoDB的24位_id"}'
```

该接口校验11个业务字段并转发到 Java `/schematic/schematicData/insert`；成功写入后会
清除进程内查询缓存，使下一次历史会话指标计算立即读取新记录。

客户端兼容单条对象、JSON 数组和常见分页结构（`records/items/list/rows/content`，
可包在 `data` 或 `result` 内）。查询接口会把 `sessionId` 直接交给 Java/MongoDB
进行精确筛选，并按 `createTime` 倒序返回；TTL/LRU 用于避免短时间重复请求。

## 3. TTL/LRU 缓存

缓存位于后端 Python 进程内，线程安全：

- 每条数据独立 TTL，到期后自动回源。
- 命中时更新最近使用顺序。
- 超过 `AGENT_EVAL_CACHE_MAX_SIZE` 后淘汰最久未使用数据。
- 重启后端会清空缓存，不影响 MongoDB 中已经保存的指标结果。
- 多后端进程不共享缓存，各进程独立回源。

## 4. 指标计算

规则代码计算工具/脚本调用成功率、Skill 步骤完成度、错误与重试。Worker 还会按根
`sessionId`（兼容评测运行 ID）从
`HDschematicRationalityCollection` 按 `sessionId` 精确匹配，排除平台自身的指标与过程记录后读取四类质量报告的全部记录；不以原始记录的 `status` 作为筛选条件。查不到时，页面提示
“当前会话暂无统计原理图生成轨迹指标”。

`resultText` 按 `checkType` 路由分析：

- `hscope_diagram_lint`：JSON 报告提取总通过率和六项通过率；Markdown 报告按图页和检查项提取
  通过数、总数及加权通过率。若原文在上游被截取，则按实际读到的 `resultText` 计算，并在逐条结果保留截取标志。
- `hscope_block_corpus_check`（容忍末尾空格）：提取图页、Block、编码数量、语料库覆盖率及缺失编码。
  覆盖率优先由已覆盖数 / 总数计算。
- `signal-interface-checker`：提取检查通过率；`tianshu-drc-review`：提取 DRC 审查通过率。
  支持显式 JSON 百分比和文本百分比；有通过数/总数时以计数为准。

同一类型出现多条报告时，只有具备分子的报告参与加权汇总；缺失分母的多个百分比不会被简单平均。
逐条结果仍保存在汇总 JSON 的 `by_check_type.*.records` 内。未发现某类型时标记 `no_record`。
`agentEvalMetrics.rates` 使用中文检查名称作键、带 `%` 的字符串作值（例如
`"总检查通过率": "47.14%"`、`"block缺少器件标识检查通过率": "0.22%"`），
避免 Spring Data MongoDB 拒绝包含句点的映射键。历史会话列表显示主要累计指标；
“查看指标”弹窗显示完整指标卡片和汇总 JSON。

固定数值不直接交给 LLM 提取，是为了保证重复计算结果一致，并避免模型漏项、改值或
把 `0.9` 与 `90%` 混淆。提取不满总计7项时，详情页会保留已提取结果并显示结构告警。
启用 LLM Judge 时，每条会话首先把时间最早请求中的第一条 `user` Prompt 单独送入分类 Judge，分类
只依据用户原始意图，不读取 Agent 后续执行结果。分类结果随完整指标 JSON 写回 MongoDB。
会话级汇总及其嵌入过程使用 Java `POST /insert` 写成唯一 `agent_eval_metric`，
不修改四类原始 `resultText` 或原始记录；
跨会话累计的唯一“汇总结果”则使用 Java `PUT /aggregate` 原位刷新。
Java 服务源码在同级 `原理图_java/`；新环境需要部署支持查询和插入的版本。
重复计算会产生新的汇总版本，详情接口以最新 `createTime` 为准。

可在 `backend/` 目录用下列脚本对指定 Session 先预览，确认后加 `--apply` 写入并回读：

```powershell
.\.runtime\windows\python\Scripts\python.exe tests\live\rebuild_quality_summaries.py 会话ID
.\.runtime\windows\python\Scripts\python.exe tests\live\rebuild_quality_summaries.py 会话ID --apply
```

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

“指标计算过程”会记录 MongoDB Java 代理的查询与写入过程，包括 HTTP 方法、接口
地址、集合、Session ID、HTTP 状态、缓存命中、耗时、返回记录数和字段列表。查询命中
后还会显示所选记录的业务字段及最多 4000 字符的 `resultText` 摘要；Cookie 等请求头
不会进入过程日志。接口失败时会显示证书、超时或 HTTP 错误，查询失败不会伪装成
“未找到记录”，写入失败则明确标记指标没有保存成功。
计算结束后，在会话列表点击“查看过程”可从 MongoDB 回读这条会话最新的过程记录；
“查看指标”的弹窗底部也提供同一入口。过程记录同时保存失败会话的错误事件。
为控制单条 MongoDB 文档大小，每个过长事件会截断详情，并在记录中标明是否截断。

自动任务配置保持不变：

```dotenv
SESSION_METRICS_AUTO_ENABLED=true
SESSION_METRICS_AUTO_INTERVAL_SECONDS=3600
SESSION_METRICS_AUTO_INITIAL_DELAY_SECONDS=60
SESSION_METRICS_AUTO_MAX_SESSIONS=100
SESSION_METRICS_AUTO_USE_LLM_JUDGE=true
SESSION_METRICS_CLASSIFICATION_JUDGE_ENABLED=false
SESSION_METRICS_CONVERSATION_JUDGE_ENABLED=false
SESSION_METRICS_AUTO_USER_ID=system
```

任务分类 Judge 和会话 Judge 当前默认关闭；规则分类、规则指标、MongoDB 质量报告提取与
质量分析 Judge 保持运行。前端的“使用质量分析 Judge”只控制质量分析 Judge。
需要恢复两项会话 Judge 时，将上述两个开关分别设为 `true` 并重启后端；
它们仅影响新提交的计算任务。

## 5. 后端接口

以下示例假设后端为 `http://127.0.0.1:8000`，`cookies.txt` 已保存登录 Cookie。

```powershell
# 健康状态：MongoDB HTTP 存储、TTL/LRU、外部查询接口、自动调度器
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
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/会话ID/process"

# 原理图生成总览按分类筛选；也可传具体子类 block_to_schematic 等
curl.exe -b cookies.txt -G "http://127.0.0.1:8000/api/schematic/conversations" `
  --data-urlencode "task_classification=schematic_generation" `
  --data-urlencode "limit=20" `
  --data-urlencode "offset=0"
```

Java 运营系统可以调用这些只读 API，以便复用登录鉴权、字段兼容、默认 24 小时时间窗
和指标版本逻辑；指标事实数据实际保存在 MongoDB 中。
