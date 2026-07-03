# Enterprise Agent MVP

这是一个企业 AI 超级员工的本地受控执行器原型。

它的定位不是替代你的 AI 终端控制台，而是给 AI 终端提供安全的本地工具能力：

- 只读授权目录里的文件
- 搜索、总结、生成最小上下文包
- 写文件前先生成草稿
- 用户确认后才真正写入
- 覆盖文件前自动备份
- 删除文件默认禁止
- 可接真实模型网关
- 可接 SMTP 真实发邮件，确认后发送
- 可生成 ICS 日程文件，确认后落盘
- 可抓取白名单网页
- 可调用白名单内部 HTTP GET
- 可执行 allowlist 脚本，确认后运行
- 支持用户级路径/skill 权限策略
- 每次调用写审计日志
- 支持 CORS 和可选 API Token，方便接入本地/内网 AI 终端

## 运行

```powershell
cd C:\Users\HYF05\Documents\Codex\2026-07-01\o-p\outputs\enterprise-agent-mvp
python server.py
```

打开 demo 页面：

```text
http://127.0.0.1:8788
```

健康检查：

```text
GET http://127.0.0.1:8788/api/health
```

## 环境变量

```powershell
$env:AGENT_API_TOKEN="your-local-token"
$env:AGENT_ALLOWED_WORKSPACE="D:\CompanyDocs"
$env:AGENT_ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
$env:AGENT_MAX_FILE_BYTES="200000"

# Optional model gateway, OpenAI-compatible / local compatible endpoint.
$env:AGENT_MODEL_API_URL="http://127.0.0.1:11434/v1/chat/completions"
$env:AGENT_MODEL_NAME="local-model"
$env:AGENT_MODEL_API_KEY=""

# Optional SMTP real email sending.
$env:AGENT_SMTP_HOST="smtp.example.com"
$env:AGENT_SMTP_PORT="587"
$env:AGENT_SMTP_USER="bot@example.com"
$env:AGENT_SMTP_PASSWORD="app-password"
$env:AGENT_SMTP_FROM="bot@example.com"

# Optional integrations.
$env:AGENT_WEB_ALLOWED_HOSTS="example.com,docs.example.com"
$env:AGENT_INTERNAL_ALLOWED_HOSTS="intranet.company.local"
$env:AGENT_SCRIPT_ALLOWLIST="python.exe,node.exe"
$env:AGENT_USER_POLICY_FILE="data\users.example.json"

python server.py
```

说明：

- `AGENT_API_TOKEN` 设置后，API 请求需要 `Authorization: Bearer <token>` 或 `X-Agent-Token`。
- `AGENT_ALLOWED_WORKSPACE` 可以把授权目录从 demo workspace 换成你们的真实目录。
- 默认服务只监听 `127.0.0.1`。
- `AGENT_USER_POLICY_FILE` 可以按用户限制目录和 skill。
- `AGENT_SCRIPT_ALLOWLIST` 不配置时脚本执行默认关闭。
- `AGENT_WEB_ALLOWED_HOSTS` / `AGENT_INTERNAL_ALLOWED_HOSTS` 不配置时网页/内部系统调用默认关闭。

## 给 AI 终端接入

工具清单：

```text
GET /api/tool/manifest
```

调用工具：

```text
POST /api/tool/call
```

确认动作：

```text
POST /api/tool/approve
```

推荐 AI 终端优先用 `file.context`，它默认只返回摘要或必要片段，不返回整份文件。

```json
{
  "user_id": "u123",
  "skill_name": "file.context",
  "params": {
    "query": "合同",
    "max_items": 5,
    "max_chars_per_item": 700
  },
  "message": "用户要了解合同相关信息"
}
```

写文件必须走确认流：

```json
{
  "user_id": "u123",
  "skill_name": "file.propose_write",
  "params": {
    "path": "report.md",
    "content": "# 报告草稿\n\n这里是内容。"
  },
  "message": "帮我生成 report.md"
}
```

返回 `pending_approval` 后，终端必须让用户确认，再调用 `/api/tool/approve`。

## 当前 skill

```text
system.status
user.permissions
model.chat
file.list
file.search
file.summarize
file.context
file.propose_write
file.delete
email.draft
email.propose_send
calendar.draft
calendar.propose_event
web.fetch
internal.http_get
script.propose_run
```

其中：

- `file.context`：推荐给 AI 终端用，返回最小必要上下文。
- `file.propose_write`：只生成写入草稿。
- `file.delete`：默认 blocked。
- `email.propose_send`：SMTP 配置后可真实发送，但必须确认。
- `calendar.propose_event`：确认后生成 ICS 文件。
- `script.propose_run`：必须配置 allowlist，且必须确认。
- `web.fetch` / `internal.http_get`：必须配置 host allowlist。

## 示例

JS 接入示例：

[examples/terminal-client.js](</C:/Users/HYF05/Documents/Codex/2026-07-01/o-p/outputs/enterprise-agent-mvp/examples/terminal-client.js>)

详细接入说明：

[INTEGRATION.md](</C:/Users/HYF05/Documents/Codex/2026-07-01/o-p/outputs/enterprise-agent-mvp/INTEGRATION.md>)
