from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import smtplib
import ssl
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from html import unescape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .audit import AuditLogger
from .config import APP_VERSION, AppConfig
from .policies import FilePolicy, PolicyError


SkillHandler = Callable[[dict[str, Any], str], dict[str, Any]]


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    permissions: list[str]
    approval_required: bool
    risk: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class SkillRegistry:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.audit = AuditLogger(config.audit_log)
        self.file_policy = FilePolicy(config.allowed_workspace, config.max_file_bytes)
        self.pending_actions: dict[str, dict[str, Any]] = {}
        self.user_policies = self._load_user_policies()
        self._handlers: dict[str, tuple[SkillSpec, SkillHandler]] = {}
        self._register_defaults()

    @classmethod
    def default(cls, root: Path) -> "SkillRegistry":
        config = AppConfig.from_root(root)
        config.allowed_workspace.mkdir(parents=True, exist_ok=True)
        config.audit_log.parent.mkdir(parents=True, exist_ok=True)
        if config.calendar_export_dir:
            config.calendar_export_dir.mkdir(parents=True, exist_ok=True)
        return cls(config)

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": APP_VERSION,
            "workspace": str(self.config.allowed_workspace),
            "max_file_bytes": self.config.max_file_bytes,
            "api_token_required": bool(self.config.api_token),
            "pending_actions": len(self.pending_actions),
            "skills": len(self._handlers),
            "model_configured": bool(self.config.model_api_url),
            "smtp_configured": bool(self.config.smtp_host and self.config.smtp_from),
            "script_allowlist": list(self.config.script_allowlist),
            "web_allowed_hosts": list(self.config.web_allowed_hosts),
            "internal_allowed_hosts": list(self.config.internal_allowed_hosts),
            "data_policy": "local-first, minimal-context-by-default",
        }

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "permissions": spec.permissions,
                "approval_required": spec.approval_required,
                "risk": spec.risk,
                "input_schema": spec.input_schema,
            }
            for spec, _handler in self._handlers.values()
        ]

    def call(
        self,
        *,
        user_id: str,
        skill_name: str,
        params: dict[str, Any],
        user_message: str,
    ) -> dict[str, Any]:
        if skill_name not in self._handlers:
            result = {"status": "error", "error": f"unknown_skill:{skill_name}"}
            self._audit(user_id, skill_name, params, result, user_message)
            return result

        if not self._skill_allowed(user_id, skill_name):
            result = {"status": "blocked", "error": "user_policy_denied", "risk": "policy"}
            self._audit(user_id, skill_name, params, result, user_message)
            return result

        spec, handler = self._handlers[skill_name]
        try:
            result = handler(params, user_id)
        except PolicyError as exc:
            result = {
                "status": "blocked",
                "error": str(exc),
                "approval_required": False,
                "risk": "policy",
            }
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}

        result.setdefault("skill", spec.name)
        result.setdefault("approval_required", spec.approval_required)
        result.setdefault("risk", spec.risk)
        self._audit(user_id, skill_name, params, result, user_message)
        return result

    def approve_action(self, *, user_id: str, action_id: str, user_message: str) -> dict[str, Any]:
        action = self.pending_actions.get(action_id)
        if not action:
            result = {"status": "error", "error": "pending action not found or already executed"}
            self._audit(user_id, "approval.confirm", {"action_id": action_id}, result, user_message)
            return result

        if action["user_id"] != user_id:
            result = {"status": "blocked", "error": "cannot approve another user's pending action"}
            self._audit(user_id, "approval.confirm", {"action_id": action_id}, result, user_message)
            return result

        dispatch = {
            "file.write": self._execute_file_write,
            "email.send": self._execute_email_send,
            "calendar.export_ics": self._execute_calendar_export,
            "script.run": self._execute_script_run,
        }
        handler = dispatch.get(action["type"])
        if not handler:
            result = {"status": "blocked", "error": "unknown pending action type"}
            self._audit(user_id, "approval.confirm", {"action_id": action_id}, result, user_message)
            return result

        try:
            result = handler(action)
            if result.get("status") == "ok":
                self.pending_actions.pop(action_id, None)
        except PolicyError as exc:
            result = {"status": "blocked", "error": str(exc), "risk": "policy"}
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}

        self._audit(user_id, action["type"], self._redact_params(action), result, user_message)
        return result

    def _register_defaults(self) -> None:
        specs: list[tuple[SkillSpec, SkillHandler]] = [
            (
                SkillSpec("system.status", "Return executor status and safety configuration.", ["system:read"], False, "low"),
                self._system_status,
            ),
            (
                SkillSpec("user.permissions", "Return effective user policy.", ["user:read"], False, "low"),
                self._user_permissions,
            ),
            (
                SkillSpec("model.chat", "Call a configured OpenAI-compatible or local model endpoint.", ["model:call"], False, "medium", {
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}, "messages": {"type": "array"}},
                }),
                self._model_chat,
            ),
            (
                SkillSpec("file.list", "List readable files in the authorized workspace.", ["file:read"], False, "low", {
                    "type": "object",
                    "properties": {"path": {"type": "string", "default": "."}},
                }),
                self._file_list,
            ),
            (
                SkillSpec("file.search", "Search file names and text content. Returns snippets only.", ["file:read"], False, "low", {
                    "type": "object",
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}},
                }),
                self._file_search,
            ),
            (
                SkillSpec("file.summarize", "Summarize authorized text files without returning full content by default.", ["file:read"], False, "low", {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                }),
                self._file_summarize,
            ),
            (
                SkillSpec("file.context", "Return a minimal local context package for the AI terminal.", ["file:read"], False, "low", {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_items": {"type": "integer", "default": 5},
                        "max_chars_per_item": {"type": "integer", "default": 600},
                    },
                }),
                self._file_context,
            ),
            (
                SkillSpec("file.propose_write", "Propose a file write. Approval is required before writing.", ["file:write"], True, "high", {
                    "type": "object",
                    "required": ["path", "content"],
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                }),
                self._file_propose_write,
            ),
            (
                SkillSpec("file.delete", "File deletion is disabled by default.", ["file:delete"], True, "forbidden", {
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                }),
                self._file_delete,
            ),
            (
                SkillSpec("email.draft", "Create an email draft only.", ["email:draft"], True, "medium"),
                self._email_draft,
            ),
            (
                SkillSpec("email.propose_send", "Propose sending an email through SMTP. Approval required.", ["email:send"], True, "high", {
                    "type": "object",
                    "required": ["to", "subject", "body"],
                    "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
                }),
                self._email_propose_send,
            ),
            (
                SkillSpec("calendar.draft", "Create a calendar event draft only.", ["calendar:draft"], True, "medium"),
                self._calendar_draft,
            ),
            (
                SkillSpec("calendar.propose_event", "Propose a calendar event and export an ICS file after approval.", ["calendar:create"], True, "high"),
                self._calendar_propose_event,
            ),
            (
                SkillSpec("web.fetch", "Fetch a web page from an allowlisted host and return text snippet.", ["web:read"], False, "medium"),
                self._web_fetch,
            ),
            (
                SkillSpec("internal.http_get", "GET an allowlisted internal HTTP endpoint.", ["internal:read"], False, "medium"),
                self._internal_http_get,
            ),
            (
                SkillSpec("script.propose_run", "Propose running an allowlisted local command. Approval required.", ["script:run"], True, "high"),
                self._script_propose_run,
            ),
        ]

        for spec, handler in specs:
            self._add(spec, handler)

    def _add(self, spec: SkillSpec, handler: SkillHandler) -> None:
        self._handlers[spec.name] = (spec, handler)

    def _system_status(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        return self.status()

    def _user_permissions(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        policy = self._user_policy(user_id)
        return {
            "status": "ok",
            "user_id": user_id,
            "allowed_paths": policy.get("allowed_paths", ["."]),
            "allowed_skills": policy.get("allowed_skills", ["*"]),
            "blocked_skills": policy.get("blocked_skills", []),
        }

    def _model_chat(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        if not self.config.model_api_url:
            raise PolicyError("model gateway is not configured. Set AGENT_MODEL_API_URL.")

        messages = params.get("messages")
        if not messages:
            prompt = str(params.get("prompt", "")).strip()
            if not prompt:
                raise PolicyError("prompt or messages is required")
            messages = [{"role": "user", "content": prompt}]

        payload = {
            "model": params.get("model") or self.config.model_name,
            "messages": messages,
            "temperature": params.get("temperature", 0.2),
        }
        headers = {"content-type": "application/json"}
        if self.config.model_api_key:
            headers["authorization"] = f"Bearer {self.config.model_api_key}"

        data = self._http_json("POST", self.config.model_api_url, payload, headers=headers, timeout=30)
        content = self._extract_model_content(data)
        return {
            "status": "ok",
            "model": payload["model"],
            "content": content,
            "raw": data if params.get("include_raw") else None,
            "data_policy": "prompt_sent_to_configured_model_endpoint",
        }

    def _file_list(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        root = self.file_policy.validate_list_root(params.get("path", "."))
        files = []
        for path in sorted(root.rglob("*")):
            if self.file_policy.allowed_file(path) and self._user_file_allowed(user_id, path):
                files.append({"path": self._relative_path(path), "bytes": path.stat().st_size})
        return {"status": "ok", "files": files}

    def _file_search(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        query = str(params.get("query", "")).strip().lower()
        if not query:
            return {"status": "ok", "matches": []}

        matches = []
        for path in self._iter_allowed_files(user_id):
            rel = self._relative_path(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            if query in f"{rel}\n{text}".lower():
                matches.append({"path": rel, "snippet": self._snippet(text, query)})

        return {"status": "ok", "query": query, "matches": matches, "data_policy": "snippets_only"}

    def _file_summarize(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        target = str(params.get("path", "")).strip()
        paths = [self.file_policy.validate_read_path(target)] if target else self._iter_allowed_files(user_id)
        summaries = []
        for path in paths[:8]:
            if not self._user_file_allowed(user_id, path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            summaries.append({"path": self._relative_path(path), "summary": self._simple_summary(text), "chars_read": len(text)})
        return {"status": "ok", "summaries": summaries, "data_policy": "summary_only"}

    def _file_context(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        query = str(params.get("query", "")).strip().lower()
        max_items = self._limit_int(params.get("max_items"), default=5, min_value=1, max_value=12)
        max_chars = self._limit_int(params.get("max_chars_per_item"), default=600, min_value=120, max_value=1500)

        items = []
        for path in self._iter_allowed_files(user_id):
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = self._relative_path(path)
            if query and query not in f"{rel}\n{text}".lower():
                continue
            content = self._snippet(text, query) if query else self._simple_summary(text)
            items.append({"path": rel, "kind": "snippet" if query else "summary", "content": content[:max_chars], "source_chars": len(text)})
            if len(items) >= max_items:
                break

        return {
            "status": "ok",
            "query": query,
            "context": items,
            "data_policy": "minimal_context_only",
            "safe_for_cloud_model": True,
            "note": "Returns summaries/snippets only, not full files by default.",
        }

    def _file_propose_write(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        target = str(params.get("path", "agent-output.md")).strip() or "agent-output.md"
        content = str(params.get("content", "")).strip()
        if not content:
            raise PolicyError("content is required")
        if len(content.encode("utf-8")) > self.config.max_file_bytes:
            raise PolicyError("content is too large")

        path = self.file_policy.validate_write_path(target)
        self._ensure_user_file_allowed(user_id, path)
        rel = self._relative_path(path)
        exists = path.exists()
        old_preview = path.read_text(encoding="utf-8", errors="replace")[:1000] if exists else ""
        action_id = self._pending("file.write", user_id, {"path": rel, "content": content})

        return {
            "status": "pending_approval",
            "approval_required": True,
            "approval": {"action_id": action_id, "label": "Confirm file write", "warning": "File will be written only after approval. Existing files are backed up first."},
            "draft": {"type": "file_write", "path": rel, "operation": "overwrite" if exists else "create", "old_preview": old_preview, "new_preview": content[:1000], "bytes": len(content.encode("utf-8"))},
        }

    def _file_delete(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        return {"status": "blocked", "approval_required": False, "risk": "forbidden", "error": "file deletion is disabled by default"}

    def _email_draft(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        recipient = params.get("recipient") or params.get("to") or "recipient@example.com"
        topic = params.get("topic") or "project update"
        body = f"Hello,\n\nThis is a draft about {topic}.\n\nThanks."
        return {"status": "draft", "approval_required": True, "draft": {"type": "email", "to": recipient, "subject": f"About {topic}", "body": body}}

    def _email_propose_send(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        if not self.config.smtp_host or not self.config.smtp_from:
            raise PolicyError("SMTP is not configured. Set AGENT_SMTP_HOST and AGENT_SMTP_FROM/AGENT_SMTP_USER.")
        to = params.get("to")
        subject = str(params.get("subject", "")).strip()
        body = str(params.get("body", "")).strip()
        if not to or not subject or not body:
            raise PolicyError("to, subject and body are required")
        recipients = self._normalize_recipients(to)
        action_id = self._pending("email.send", user_id, {"to": recipients, "subject": subject, "body": body})
        return {
            "status": "pending_approval",
            "approval_required": True,
            "approval": {"action_id": action_id, "label": "Confirm email send", "warning": "Email will be sent through configured SMTP only after approval."},
            "draft": {"type": "email_send", "to": recipients, "subject": subject, "body_preview": body[:1200]},
        }

    def _calendar_draft(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        return {
            "status": "draft",
            "approval_required": True,
            "draft": {
                "type": "calendar_event",
                "title": params.get("title") or "Untitled event",
                "attendees": [params.get("attendee") or "attendee@example.com"],
                "time": params.get("time") or "TBD",
            },
        }

    def _calendar_propose_event(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        title = str(params.get("title", "")).strip() or "Untitled event"
        start = str(params.get("start", "")).strip()
        end = str(params.get("end", "")).strip()
        if not start or not end:
            raise PolicyError("start and end are required. Use ISO-like datetime strings.")
        attendees = self._normalize_recipients(params.get("attendees", []))
        description = str(params.get("description", "")).strip()
        action_id = self._pending("calendar.export_ics", user_id, {"title": title, "start": start, "end": end, "attendees": attendees, "description": description})
        return {
            "status": "pending_approval",
            "approval_required": True,
            "approval": {"action_id": action_id, "label": "Confirm calendar event", "warning": "An ICS event file will be created after approval."},
            "draft": {"type": "calendar_event", "title": title, "start": start, "end": end, "attendees": attendees, "description": description},
        }

    def _web_fetch(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        url = str(params.get("url", "")).strip()
        self._ensure_host_allowed(url, self.config.web_allowed_hosts, "web")
        text = self._fetch_text(url, max_chars=self._limit_int(params.get("max_chars"), default=2500, min_value=200, max_value=8000))
        return {"status": "ok", "url": url, "text": text, "data_policy": "remote_page_snippet"}

    def _internal_http_get(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        url = str(params.get("url", "")).strip()
        self._ensure_host_allowed(url, self.config.internal_allowed_hosts, "internal")
        text = self._fetch_text(url, max_chars=self._limit_int(params.get("max_chars"), default=3000, min_value=200, max_value=10000))
        return {"status": "ok", "url": url, "text": text, "data_policy": "allowlisted_internal_get"}

    def _script_propose_run(self, params: dict[str, Any], user_id: str) -> dict[str, Any]:
        args = self._normalize_command(params.get("command"))
        if not args:
            raise PolicyError("command is required")
        executable = Path(args[0]).name.lower()
        if executable not in self.config.script_allowlist:
            raise PolicyError("script execution is disabled for this command. Set AGENT_SCRIPT_ALLOWLIST.")
        action_id = self._pending("script.run", user_id, {"command": args})
        return {
            "status": "pending_approval",
            "approval_required": True,
            "approval": {"action_id": action_id, "label": "Confirm script run", "warning": "The allowlisted command will run in the authorized workspace after approval."},
            "draft": {"type": "script_run", "command": args, "cwd": str(self.config.allowed_workspace), "timeout_seconds": self.config.script_timeout_seconds},
        }

    def _execute_file_write(self, action: dict[str, Any]) -> dict[str, Any]:
        path = self.file_policy.validate_write_path(action["path"])
        self._ensure_user_file_allowed(action["user_id"], path)
        content = action["content"]
        backup_rel = None
        if path.exists():
            backup_dir = self.config.root / "data" / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            rel_name = self._relative_path(path).replace("\\", "__").replace("/", "__")
            backup_path = backup_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.{rel_name}.bak"
            shutil.copy2(path, backup_path)
            backup_rel = str(backup_path.relative_to(self.config.root)).replace("\\", "/")
        path.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": self._relative_path(path), "backup": backup_rel, "bytes_written": len(content.encode("utf-8")), "message": "File written after approval."}

    def _execute_email_send(self, action: dict[str, Any]) -> dict[str, Any]:
        if not self.config.smtp_host or not self.config.smtp_from:
            raise PolicyError("SMTP is not configured")
        msg = EmailMessage()
        msg["From"] = self.config.smtp_from
        msg["To"] = ", ".join(action["to"])
        msg["Subject"] = action["subject"]
        msg.set_content(action["body"])
        if self.config.smtp_tls:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if self.config.smtp_user and self.config.smtp_password:
                    smtp.login(self.config.smtp_user, self.config.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=20) as smtp:
                if self.config.smtp_user and self.config.smtp_password:
                    smtp.login(self.config.smtp_user, self.config.smtp_password)
                smtp.send_message(msg)
        return {"status": "ok", "message": "Email sent after approval.", "to": action["to"]}

    def _execute_calendar_export(self, action: dict[str, Any]) -> dict[str, Any]:
        if not self.config.calendar_export_dir:
            raise PolicyError("calendar export dir is not configured")
        self.config.calendar_export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{self._slug(action['title'])}.ics"
        path = self.config.calendar_export_dir / filename
        path.write_text(self._ics(action), encoding="utf-8")
        return {"status": "ok", "message": "ICS event created after approval.", "path": str(path)}

    def _execute_script_run(self, action: dict[str, Any]) -> dict[str, Any]:
        args = action["command"]
        executable = Path(args[0]).name.lower()
        if executable not in self.config.script_allowlist:
            raise PolicyError("script command is not allowlisted")
        completed = subprocess.run(
            args,
            cwd=self.config.allowed_workspace,
            capture_output=True,
            text=True,
            timeout=self.config.script_timeout_seconds,
            shell=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
        return {
            "status": "ok" if completed.returncode == 0 else "error",
            "message": "Script finished after approval.",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }

    def _audit(self, user_id: str, skill_name: str, params: dict[str, Any], result: dict[str, Any], user_message: str) -> None:
        self.audit.record(user_id=user_id, skill_name=skill_name, params=self._redact_params(params), result=result, user_message=user_message)

    def _load_user_policies(self) -> dict[str, Any]:
        path = self.config.user_policy_file
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _user_policy(self, user_id: str) -> dict[str, Any]:
        users = self.user_policies.get("users", {})
        return users.get(user_id) or self.user_policies.get("default") or {"allowed_paths": ["."], "allowed_skills": ["*"], "blocked_skills": []}

    def _skill_allowed(self, user_id: str, skill_name: str) -> bool:
        policy = self._user_policy(user_id)
        if self._matches_any(skill_name, policy.get("blocked_skills", [])):
            return False
        allowed = policy.get("allowed_skills", ["*"])
        return self._matches_any(skill_name, allowed)

    def _user_file_allowed(self, user_id: str, path: Path) -> bool:
        try:
            rel = self._relative_path(path)
        except ValueError:
            return False
        allowed_paths = self._user_policy(user_id).get("allowed_paths", ["."])
        return self._relative_allowed(rel, allowed_paths)

    def _ensure_user_file_allowed(self, user_id: str, path: Path) -> None:
        if not self._user_file_allowed(user_id, path):
            raise PolicyError("user is not allowed to access this path")

    def _relative_allowed(self, rel: str, allowed_paths: list[str]) -> bool:
        normalized = rel.replace("\\", "/").strip("/")
        for allowed in allowed_paths:
            allowed_norm = str(allowed).replace("\\", "/").strip("/")
            if allowed_norm in {"", "."}:
                return True
            if normalized == allowed_norm or normalized.startswith(allowed_norm.rstrip("/") + "/"):
                return True
        return False

    def _matches_any(self, value: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            if pattern == "*" or pattern == value:
                return True
            if pattern.endswith("*") and value.startswith(pattern[:-1]):
                return True
        return False

    def _pending(self, action_type: str, user_id: str, payload: dict[str, Any]) -> str:
        action_id = str(uuid4())
        self.pending_actions[action_id] = {"id": action_id, "type": action_type, "user_id": user_id, "created_at": datetime.now(timezone.utc).isoformat(), **payload}
        return action_id

    def _redact_params(self, params: dict[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in params.items():
            if key in {"content", "body", "text", "smtp_password", "model_api_key"}:
                redacted[key] = f"<{len(str(value))} chars>"
            else:
                redacted[key] = value
        return redacted

    def _iter_allowed_files(self, user_id: str) -> list[Path]:
        return [path for path in sorted(self.config.allowed_workspace.rglob("*")) if self.file_policy.allowed_file(path) and self._user_file_allowed(user_id, path)]

    def _relative_path(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.config.allowed_workspace.resolve())).replace("\\", "/")

    def _limit_int(self, value: Any, *, default: int, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(min_value, min(parsed, max_value))

    def _simple_summary(self, text: str) -> str:
        clean = " ".join(line.strip() for line in text.splitlines() if line.strip())
        return clean if len(clean) <= 220 else clean[:220].rstrip() + "..."

    def _snippet(self, text: str, query: str) -> str:
        if not query:
            return self._simple_summary(text)
        index = text.lower().find(query.lower())
        if index < 0:
            return self._simple_summary(text)
        return text[max(0, index - 80) : min(len(text), index + 160)].replace("\n", " ").strip()

    def _normalize_recipients(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(",") if item.strip()]

    def _normalize_command(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item)]
        if isinstance(value, str):
            return shlex.split(value)
        return []

    def _ensure_host_allowed(self, url: str, allowed_hosts: tuple[str, ...], kind: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise PolicyError("valid http(s) url is required")
        host = parsed.hostname.lower()
        if not allowed_hosts:
            raise PolicyError(f"{kind} access is not configured. Set an allowlist env var.")
        if "*" not in allowed_hosts and host not in allowed_hosts:
            raise PolicyError(f"host is not allowlisted: {host}")

    def _fetch_text(self, url: str, *, max_chars: int) -> str:
        req = Request(url, headers={"user-agent": "EnterpriseAgentMVP/0.3"})
        with urlopen(req, timeout=15) as response:
            raw = response.read(min(1_000_000, max_chars * 10))
        text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = unescape(text)
        return " ".join(text.split())[:max_chars]

    def _http_json(self, method: str, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers=headers, method=method)
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _extract_model_content(self, data: dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return json.dumps(data, ensure_ascii=False)

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
        return slug[:60] or "event"

    def _ics(self, action: dict[str, Any]) -> str:
        uid = f"{uuid4()}@enterprise-agent-mvp"
        attendees = "\n".join(f"ATTENDEE:mailto:{item}" for item in action.get("attendees", []))
        return "\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Enterprise Agent MVP//EN",
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART:{self._ics_time(action['start'])}",
                f"DTEND:{self._ics_time(action['end'])}",
                f"SUMMARY:{self._ics_escape(action['title'])}",
                f"DESCRIPTION:{self._ics_escape(action.get('description', ''))}",
                attendees,
                "END:VEVENT",
                "END:VCALENDAR",
                "",
            ]
        )

    def _ics_time(self, value: str) -> str:
        cleaned = value.replace("-", "").replace(":", "")
        if "T" not in cleaned:
            cleaned = cleaned + "T000000"
        return cleaned.replace("+0800", "").replace("Z", "") + "Z"

    def _ics_escape(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

