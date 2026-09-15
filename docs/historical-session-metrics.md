# 历史会话指标计算与缓存

## 1. 当前实现

前端侧边栏“新建评测”之后新增“历史会话指标计算”。页面默认只查询当前时刻向前 24 小时的 LiteLLM 普通会话，不包含 Agent Eval 自身产生的评测会话。用户可以按 End User、Session ID 和模型筛选，分页大小支持 20、50、100，并批量选择最多 100 个会话异步计算。

指标结果存入独立 MongoDB：

- `session_metrics_latest`：每个会话一条最新结果，Mongo `_id` 等于 LiteLLM 根 `session_id`。
- `session_metrics_versions`：按会话、指标定义版本、源数据指纹保留历史版本。
- `metric_calculation_jobs`：批量计算任务及其进度。

MongoDB 不替代 LiteLLM PostgreSQL。PostgreSQL 是模型交互事实源，MongoDB 只保存可重复计算的衍生指标。

服务端支持每小时自动增量计算。它只扫描最近 24 小时的普通会话，并比较会话结束时间与 MongoDB 中的 `calculated_at`；没有新增交互的会话不会重复请求 Judge。默认每轮最多处理 100 个变化会话。

## 2. 默认 24 小时规则

以下接口不传 `start_time` 和 `end_time` 时，都使用 `[当前时间-24小时, 当前时间)`：

- `GET /api/schematic/conversations`
- `GET /api/session-metrics/sessions`
- `GET /api/session-metrics`
- `GET /api/session-metrics/summary`

前端会明确传入同样的 24 小时时间范围，用户可以手动修改。时间均使用带时区的 ISO 8601 格式。

## 3. 指标计算方法

规则代码负责可确定事实：

- 工具调用次数、成功/失败/未知次数和成功率。
- 脚本调用次数、成功/失败/未知次数和成功率。
- 已知 Skill 标记与脚本对应的步骤完成度。
- Agent/模型、起止时间、错误签名、重复操作、错误恢复。

LLM Judge 负责需要语义理解的内容：

- 四类任务判定的补充判断。
- 无法由固定标记识别的 Skill 步骤证据。
- 错误、重试和恢复行为的语义证据。
- “声称工具或产物成功，但没有对应证据”的疑似伪造输出。

长会话不会一次性提交给 Judge。服务端按约 42,000 字符分块，每个超长单轮本身也会截断到约 16,000 字符。每块独立分析，最后由代码执行确定性合并：

1. 只接受引用了数据库真实 `request_id` 的事件。
2. 按 `event_id` 去重。
3. 疑似伪造置信度低于 0.7 的事件不计数。
4. 有明确 Skill/脚本标记时，规则任务分类优先；只有规则回退为 `other` 时才由 Judge 补充。
5. Judge 不可用不会丢失规则结果，状态会显示为“规则已完成、Judge 不可用”。

`confirmed_fabrication_count` 默认保持空值。没有产物校验或工具执行证据时，系统不会把“疑似”误报为“确认”。

## 4. Nacos 配置

本地 `.env` 只保存如何连接 Nacos 的引导信息；MongoDB 和 Redis 地址优先从 Nacos Data ID 读取。

```dotenv
NACOS_SERVER_ADDR=http://服务器地址:8848
NACOS_NAMESPACE=
NACOS_GROUP=AGENT_EVAL
NACOS_DATA_ID=agent-eval-infrastructure.yaml
NACOS_USERNAME=
NACOS_PASSWORD=
```

Nacos 配置内容：

```yaml
mongodb:
  uri: mongodb://指标服务账号:密码@服务器地址:27017/agent_eval_metrics?authSource=agent_eval_metrics
  database: agent_eval_metrics
redis:
  url: redis://:密码@服务器地址:6379/0
```

开发/故障恢复时可以直接使用环境变量覆盖 Nacos：

```dotenv
METRICS_MONGODB_URI=
METRICS_MONGODB_DATABASE=agent_eval_metrics
AGENT_EVAL_REDIS_URL=
```

优先级为：环境变量直接值 > Nacos 内容 > 默认数据库名。任何健康接口都不会返回密码或完整连接串。

自动任务配置：

```dotenv
SESSION_METRICS_AUTO_ENABLED=true
SESSION_METRICS_AUTO_INTERVAL_SECONDS=3600
SESSION_METRICS_AUTO_INITIAL_DELAY_SECONDS=60
SESSION_METRICS_AUTO_MAX_SESSIONS=100
SESSION_METRICS_AUTO_USE_LLM_JUDGE=true
SESSION_METRICS_AUTO_USER_ID=system
```

后端启动 60 秒后执行第一轮，以后每小时执行。关闭自动计算时，页面批量计算和 API 手动计算仍可使用。

当前部署把 MongoDB、Redis 和 Nacos 都绑定在阿里云主机的 `127.0.0.1`，不对公网开放。Windows 评测机通过 SSH 隧道访问：

```powershell
.\scripts\start-infrastructure-tunnel.ps1
.\scripts\stop-infrastructure-tunnel.ps1
```

`start-all.ps1` 和 `stop-all.ps1` 已分别集成上述启动/停止操作。Nacos 使用只读服务账号拉取配置；发布配置仍需管理员账号，运行时服务账号不能修改配置。

## 5. Redis 缓存策略

Redis 是可选加速层，不是事实源：

- 原理图总览列表：30 秒 TTL。
- 会话轻量详情：30 秒 TTL。
- 展开的单轮完整请求/响应：300 秒 TTL。
- Redis 连接失败时自动查询 PostgreSQL，不会阻止页面使用。
- 已完成评测结果的 `model-interactions.json` 同时使用文件路径、修改时间、大小作为进程内 LRU 缓存键；文件变化会自动失效。

模型交互正文采用延迟加载：列表和会话骨架不再把全部 `messages`、`response` 一次传到浏览器，用户展开某一轮时才调用单轮详情接口。

## 6. 后端接口

下列示例假设后端为 `http://127.0.0.1:8000`，并使用系统已有的登录 Cookie。没有登录 Cookie 时先按 `cli-complete-guide.md` 的登录接口获取。

健康状态：

```powershell
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/health"
```

列出最近 24 小时可计算会话：

```powershell
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/sessions?limit=20&offset=0"
```

批量提交指标计算：

```powershell
curl.exe -b cookies.txt -X POST "http://127.0.0.1:8000/api/session-metrics/jobs" `
  -H "Content-Type: application/json" `
  -d '{"session_ids":["会话ID"],"start_time":"2026-09-14T12:00:00+08:00","end_time":"2026-09-15T12:00:00+08:00","use_llm_judge":true}'
```

查看任务进度：

```powershell
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/jobs/任务ID"
```

立即触发一次与每小时任务相同的增量扫描：

```powershell
curl.exe -b cookies.txt -X POST "http://127.0.0.1:8000/api/session-metrics/scheduler/run"
```

读取最近 24 小时指标列表、汇总和单会话结果：

```powershell
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics?limit=20&offset=0"
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/summary"
curl.exe -b cookies.txt "http://127.0.0.1:8000/api/session-metrics/会话ID"
```

## 7. Java 系统接入

推荐 Java 服务调用 Agent Eval 的只读 API，不直接读取 MongoDB。这样可以继续复用登录鉴权、24 小时默认窗口、字段兼容和指标版本逻辑。运营前端先请求 `/api/session-metrics/summary` 显示聚合卡片，再按需请求 `/api/session-metrics` 加载分页明细。

如果内网架构必须直连 MongoDB，Java 只读取 `session_metrics_latest`，必须同时检查 `metric_definition_version` 和 `status`；不要依赖 `session_metrics_versions` 作为在线查询表。

## 8. 部署前的权限边界

在服务器创建 MongoDB 用户、Nacos 用户、Redis 密码，或修改防火墙/公网监听均属于鉴权或访问控制变更。实施前必须取得明确授权。默认建议仅允许内网/容器网络访问 MongoDB、Redis 和 Nacos，公网只暴露经过 TLS 和认证的 Agent Eval API。
