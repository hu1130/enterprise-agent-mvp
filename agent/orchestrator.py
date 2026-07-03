from __future__ import annotations

import re
from typing import Any

from .skills import SkillRegistry


class AgentOrchestrator:
    """A small rule-based agent that can later be replaced by a real model gateway."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def handle(self, *, user_id: str, message: str) -> dict[str, Any]:
        decision = self._decide(message)
        skill_result = self.registry.call(
            user_id=user_id,
            skill_name=decision["skill"],
            params=decision["params"],
            user_message=message,
        )

        return {
            "message": self._render_reply(decision, skill_result),
            "decision": decision,
            "skill_result": skill_result,
        }

    def _decide(self, message: str) -> dict[str, Any]:
        lowered = message.lower()

        if self._is_delete_intent(message):
            return {
                "skill": "file.delete",
                "params": {"path": self._extract_path(message) or ""},
                "reason": "删除文件是高风险动作，第一版默认禁止",
            }

        if self._is_write_intent(message):
            path = self._extract_path(message) or "agent-output.md"
            return {
                "skill": "file.propose_write",
                "params": {"path": path, "content": self._draft_file_content(message)},
                "reason": "写文件必须先生成草稿并等待用户确认",
            }

        if any(word in message for word in ["列出文件", "有哪些文件", "文件列表"]) or "list files" in lowered:
            return {"skill": "file.list", "params": {}, "reason": "用户想查看授权目录里的文件"}

        if any(word in message for word in ["总结", "概括", "摘要"]):
            path = self._extract_path(message)
            params = {"path": path} if path else {}
            return {"skill": "file.summarize", "params": params, "reason": "用户想读取并总结文件"}

        if any(word in message for word in ["查找", "搜索", "找一下", "合同", "资料", "文档"]):
            query = self._extract_keyword(message)
            return {"skill": "file.search", "params": {"query": query}, "reason": "用户想在授权文件里搜索信息"}

        if any(word in message for word in ["邮件", "email", "发给", "写给"]):
            return {
                "skill": "email.draft",
                "params": {"recipient": self._extract_person(message), "topic": self._extract_topic(message)},
                "reason": "发邮件属于可写动作，第一版只生成草稿",
            }

        if any(word in message for word in ["会议", "日程", "开会", "约", "安排"]):
            return {
                "skill": "calendar.draft",
                "params": {
                    "title": self._extract_topic(message) or "沟通会议",
                    "attendee": self._extract_person(message),
                    "time": self._extract_time_text(message),
                },
                "reason": "创建日程属于可写动作，第一版只生成草稿",
            }

        return {"skill": "file.list", "params": {}, "reason": "没有识别到具体动作，先展示可读文件"}

    def _render_reply(self, decision: dict[str, Any], result: dict[str, Any]) -> str:
        if result.get("status") == "blocked":
            return f"这个请求被权限策略拦截了：{result.get('error')}"

        skill = decision["skill"]
        if skill == "file.list":
            files = result.get("files", [])
            if not files:
                return "授权 workspace 里现在没有可读文件。"
            lines = [f"- {item['path']} ({item['bytes']} bytes)" for item in files]
            return "我只能看到授权 workspace 里的这些文件：\n" + "\n".join(lines)

        if skill == "file.search":
            matches = result.get("matches", [])
            if not matches:
                return f"没有在授权 workspace 里找到关键词：{result.get('query')}"
            lines = [f"- {item['path']}: {item['snippet']}" for item in matches]
            return "找到这些匹配：\n" + "\n".join(lines)

        if skill == "file.summarize":
            summaries = result.get("summaries", [])
            if not summaries:
                return "没有可总结的授权文件。"
            lines = [f"- {item['path']}: {item['summary']}" for item in summaries]
            return "文件摘要如下：\n" + "\n".join(lines)

        if skill == "file.propose_write":
            draft = result.get("draft", {})
            return (
                "我已经生成文件写入草稿，但还没有改文件。\n"
                f"目标文件：{draft.get('path')}\n"
                f"动作：{draft.get('operation')}\n"
                "需要你点击确认后才会写入；如果是覆盖文件，会先自动备份。"
            )

        if skill == "file.delete":
            return "删除文件默认禁止。企业版应该接单独审批流，不能靠一句对话直接删除。"

        if skill in {"email.draft", "calendar.draft"}:
            return "我已经生成草稿，但不会直接执行。请在右侧结果里检查，确认后再接真实执行 skill。"

        return "已处理。"

    def _is_delete_intent(self, message: str) -> bool:
        return any(word in message for word in ["删除", "删掉", "移除"])

    def _is_write_intent(self, message: str) -> bool:
        strong_words = ["写入", "保存", "新建文件", "创建文件", "覆盖", "写到"]
        edit_words = ["修改", "更新", "改写", "改成"]
        if any(word in message for word in strong_words):
            return True
        if any(word in message for word in edit_words) and (self._extract_path(message) or "文件" in message or "文档" in message):
            return True
        return False

    def _draft_file_content(self, message: str) -> str:
        topic = self._extract_topic(message)
        return (
            "# AI 生成草稿\n\n"
            f"来源请求：{message}\n\n"
            f"主题：{topic}\n\n"
            "建议内容：\n"
            "- 这是一个待确认的文件写入草稿。\n"
            "- 系统不会直接写入，必须用户点击确认。\n"
            "- 如果覆盖已有文件，系统会先保留备份。\n"
        )

    def _extract_path(self, message: str) -> str | None:
        match = re.search(r"([\w\-.]+(?:/[\w\-.]+)+|[\w\-.]+\.(?:txt|md|json|csv|log))", message)
        return match.group(1) if match else None

    def _extract_keyword(self, message: str) -> str:
        for marker in ["查找", "搜索", "找一下"]:
            if marker in message:
                return message.split(marker, 1)[1].strip(" ：:，。") or "合同"
        if "合同" in message:
            return "合同"
        if "资料" in message:
            return "资料"
        if "文档" in message:
            return "文档"
        return message.strip()[:20]

    def _extract_person(self, message: str) -> str:
        match = re.search(r"(张三|李四|王五|[\u4e00-\u9fa5]{2,4})", message)
        if match:
            return match.group(1)
        return "待填写对象"

    def _extract_topic(self, message: str) -> str:
        if "项目" in message:
            return "项目进度"
        if "合同" in message:
            return "合同事项"
        if "客户" in message:
            return "客户跟进"
        if "会议" in message or "开会" in message:
            return "沟通会议"
        return "待确认事项"

    def _extract_time_text(self, message: str) -> str:
        for phrase in ["明天下午", "明天上午", "今天下午", "今天上午", "下周一", "下周二", "下周三", "下周四", "下周五"]:
            if phrase in message:
                return phrase
        return "待确认时间"

