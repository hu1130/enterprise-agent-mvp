# AI Terminal Integration

目标架构：

```text
用户 Web 对话
  -> AI 超级员工理解任务
  -> 需要本地能力时调用本地执行器
  -> 本地执行器只在授权目录内操作
  -> 默认只返回摘要或必要片段
  -> 高风险动作返回 pending_approval
  -> 用户确认后再执行
```

## 推荐接入方式

AI 终端负责“想和指挥”，本地执行器负责“受控执行”。

不要让云端 AI 终端直接读写用户电脑文件。所有本地文件能力都通过：

```text
/api/tool/call
/api/tool/approve
```

优先用：

```text
file.context
```

原因：它默认返回最小必要上下文，适合给云端或内网模型继续回答问题。

## JavaScript 示例

可以直接复用：

[examples/terminal-client.js](</C:/Users/HYF05/Documents/Codex/2026-07-01/o-p/outputs/enterprise-agent-mvp/examples/terminal-client.js>)

核心调用：

```js
import { callLocalTool, approveLocalAction, getLocalContext } from "./examples/terminal-client.js";

const context = await getLocalContext("合同", "current-user");

const draft = await callLocalTool(
  "file.propose_write",
  {
    path: "report.md",
    content: "# 报告\n\n这里是内容。",
  },
  "生成报告",
  "current-user"
);

if (draft.status === "pending_approval") {
  // 这里必须让用户点确认，不能自动调用 approve。
  const actionId = draft.approval.action_id;
  // 用户确认后：
  await approveLocalAction(actionId, "current-user");
}
```

## API Token

本地执行器可以设置 token：

```powershell
$env:AGENT_API_TOKEN="your-local-token"
python server.py
```

请求头：

```text
Authorization: Bearer your-local-token
```

或：

```text
X-Agent-Token: your-local-token
```

## pending_approval 处理

当返回：

```json
{
  "status": "pending_approval",
  "approval_required": true,
  "approval": {
    "action_id": "xxx",
    "label": "确认写入文件"
  }
}
```

终端必须停下来，让用户确认。不能自动继续。

确认后：

```json
{
  "user_id": "current-user",
  "action_id": "xxx"
}
```

POST 到：

```text
/api/tool/approve
```

## 数据策略

默认返回：

```text
文件名
搜索结果
摘要
必要片段
结构化字段
```

默认不要返回：

```text
整份合同
整份客户资料
整个文件夹内容
敏感目录内容
凭据、密钥、token
```

如果确实要把完整文件发给云模型，应该让用户看到明确提示并确认。

## 新增真实能力

这些能力已经有接口，但默认安全关闭，需要配置后使用：

```text
model.chat              调配置好的模型接口
email.propose_send      通过 SMTP 真实发邮件，必须确认
calendar.propose_event  生成 ICS 日程文件，必须确认
web.fetch               抓白名单网页
internal.http_get       调白名单内部 GET 接口
script.propose_run      执行 allowlist 命令，必须确认
user.permissions        查看当前用户权限
```

AI 终端处理原则：

```text
读类能力：可以直接调用，但只拿摘要/片段
写类能力：必须 pending_approval -> 用户确认 -> approve
外部/内部 HTTP：必须 host allowlist
脚本执行：必须 command allowlist
```

