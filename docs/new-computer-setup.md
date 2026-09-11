# 新电脑适配与内网 LiteLLM 接入

## 结论

评测系统不需要新建或迁移 PostgreSQL 表。它继续使用 LiteLLM 的
`LiteLLM_SpendLogs` 与虚拟 Key 表；每次评测申请独立虚拟 Key，唯一
`key_alias=agent-eval-<run_id>`，结束后删除 Key，并按 alias 分页读取本次任务的全部记录。

登录是“声明身份登录”：密码只用于满足登录表单，不校验、不保存、不写日志；工号被写入
签名 HttpOnly Cookie。后端以 Cookie 中的工号覆盖请求体里的 `user_id`。

## 1. 准备软件

- Git、Node.js 24.15.x、npm 11、Python 3.10 以上。
- 新电脑能访问内网 LiteLLM HTTP 地址与 PostgreSQL 地址。
- 安装评测后端依赖：

```powershell
cd <评测仓库>\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[web,database,dev]"
```

- 安装前端依赖：

```powershell
cd <评测仓库>\frontend
npm ci
npm run build
```

## 2. 配置评测系统

复制仓库根目录 `.env.example` 为 `.env`，至少填写：

```dotenv
LITELLM_API_KEY=<普通调用 Key>
LITELLM_MASTER_KEY=<可调用 /key/generate 和 /key/delete 的管理 Key>
DATABASE_URL=postgresql://<只读用户>:<密码>@<数据库主机>:5432/litellm
AGENT_EVAL_SESSION_SECRET=<至少 32 字节的随机值>
LITELLM_USER_AGENT=OpenAI/Python
LITELLM_X_COOKIE=11
```

生产环境使用 HTTPS 时设置 `AGENT_EVAL_SECURE_COOKIE=true`。不要提交 `.env`。

`backend/config/models.yaml` 中只有 `internal_gateway: true` 的 Profile 会收到：

```text
User-Agent: OpenAI/Python
x-cookie: 11
x-user-account: <登录工号>
```

新增外网模型 Profile 时不要设置该标志；因此工号和内网 Header 不会发送到外网模型。
如果内网地址、模型 ID 或数据库地址变化，只改 `.env` 和 `models.yaml`，无需改代码。

## 3. LiteLLM 与数据库要求

内网 LiteLLM 需要满足以下能力：

- 管理 Key 可创建和删除虚拟 Key；创建结果会由 LiteLLM 自己写入其数据库。
- 模型调用会写入 `LiteLLM_SpendLogs`，并保留请求 `metadata`、模型、状态、Token 与时间字段。
- 若要抓取完整 messages/response，LiteLLM 部署必须启用其“将模型请求/响应写入数据库”的设置；
  具体配置名以所部署 LiteLLM 版本为准。关闭时仍可区分任务、工号、模型和 Agent，但内容列为空。
- 评测系统数据库账号只需要读取 `LiteLLM_SpendLogs`，不需要建表或写表权限。

建议在数据库侧为高数据量环境建立表达式索引（由 DBA 在确认现有索引后执行）：

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_spendlogs_key_alias
ON "LiteLLM_SpendLogs" ((metadata->>'user_api_key_alias'), "startTime");
```

若当前 LiteLLM 把 alias 放在 `metadata.spend_logs_metadata`，可按同样方式为该 JSON 路径建索引。
这只是性能优化，不是功能前提，也没有数据库结构迁移。

## 4. 构建带子 Agent 标记的 JustDo

不能只复制旧的 JustDo 安装包；必须使用包含本次 `027/028 request metadata` 补丁的源码重建：

```powershell
cd <JustDo仓库>
npm ci
$env:OPENCLAW_FORCE_INSTALL = "1"
npm run openclaw:runtime:host
npm run openclaw:patches:verify
npm test -- --run tests/openclaw/patches/v2026.7.1-2/request-metadata.test.ts
npm run dist:win
```

安装生成的新包。评测临时 Provider 名为 `agent_eval_*`；新 Runtime 会为主/子 Agent 请求写入
`session_id`、`parent_session_id`、`request_purpose`。有 `parent_session_id` 的记录被精确判定为
`subagent`，否则为 `main`。其他 Agent 若原生提供同名字段也会自动识别；完全不提供会话字段的
Agent 使用评测器默认主 Agent 标记，并在导出记录的 `agent_role_detection` 中注明。

## 5. 启动与验收

如果使用 Windows 完整发布包，首次安装依赖并填写 `.env` 后可直接运行：

```powershell
.\start.ps1 -OpenBrowser
```

`start.ps1` 是兼容入口，只接收 `-OpenBrowser`，内部直接调用 `start-all.ps1`；
`start-all.ps1` 才是实际的服务管理脚本，会加载 `.env`、自动寻找空闲端口、后台启动后端与前端、
等待健康检查，并把 PID、端口和日志路径写入 `backend/.runtime/service-manager/services.json`。
需要自定义起始端口时应直接运行：

```powershell
.\start-all.ps1 -BackendStartPort 8000 -FrontendStartPort 5173 -OpenBrowser
```

停止完整发布包中的服务使用 `.\stop-all.ps1`。源码仓库没有发布包顶层的服务管理脚本时，
按下面方式分别启动后端和前端。

```powershell
cd <评测仓库>\backend
.\.venv\Scripts\Activate.ps1
python run_server.py
```

另开终端启动前端：

```powershell
cd <评测仓库>\frontend
npm run dev
```

浏览器登录后执行一条包含 JustDo 子 Agent 的评测，检查：

1. `/api/auth/me` 返回当前工号，响应中没有密码。
2. LiteLLM 日志能看到三项内网 Header。
3. 结果目录 `evaluation-report.json` 的 `user_id` 是登录工号。
4. `model-interactions.json` 每条记录都有 `evaluation_task_id`、`employee_no`、
   `key_alias`、`top_level_agent`、`requested_model`、`agent_role`。
5. JustDo 子 Agent 记录包含 `parent_session_id` 且 `agent_role=subagent`。
6. 构造超过 500 次调用的任务，导出数量仍超过 500；`page_size: 500` 只是数据库分页大小。

## 6. 常见故障

- `/key/generate` 为 401/403：`LITELLM_MASTER_KEY` 权限不足。
- 模型为 401/403：检查 Profile 是否标记 `internal_gateway: true`，以及登录工号格式。
- 数据库无记录：检查 LiteLLM 是否写 SpendLogs、数据库账号权限和异步落库延迟。
- 能看到调用但没有正文：在 LiteLLM 侧启用请求/响应内容落库。
- JustDo 子 Agent 显示为 main：确认使用新构建安装包，并运行 `openclaw:patches:verify`。
- 电脑配置了系统 HTTP/HTTPS 代理但内网网关不可达：当前关键 `httpx` 调用已设置
  `trust_env=False`，不会自动继承 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`；应直接检查
  内网路由、DNS 和 LiteLLM 地址，而不是修改系统代理。
