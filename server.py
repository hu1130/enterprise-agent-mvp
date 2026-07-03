from __future__ import annotations

import json
import mimetypes
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agent.orchestrator import AgentOrchestrator
from agent.skills import SkillRegistry


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

registry = SkillRegistry.default(ROOT)
orchestrator = AgentOrchestrator(registry)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "EnterpriseAgentMVP/0.2"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_common_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_file(STATIC_DIR / "index.html")
            return

        if path == "/api/health":
            self._send_json(registry.status())
            return

        if path.startswith("/api/") and not self._authorized():
            self._send_json({"error": "unauthorized"}, status=401)
            return

        if path in {"/api/skills", "/api/tool/manifest"}:
            self._send_json({"skills": registry.manifest()})
            return

        if path == "/api/files":
            result = registry.call(
                user_id="demo-user",
                skill_name="file.list",
                params={},
                user_message="list files",
            )
            self._send_json(result)
            return

        if path == "/api/audit":
            self._send_json({"events": registry.audit.tail(limit=100)})
            return

        if path.startswith("/static/"):
            rel = path.removeprefix("/static/")
            self._send_file(STATIC_DIR / rel)
            return

        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path.startswith("/api/") and not self._authorized():
            self._send_json({"error": "unauthorized"}, status=401)
            return

        if parsed.path == "/api/chat":
            self._handle_chat()
            return

        if parsed.path == "/api/tool/call":
            self._handle_tool_call()
            return

        if parsed.path in {"/api/approve", "/api/tool/approve"}:
            self._handle_approve()
            return

        self._send_json({"error": "not_found"}, status=404)

    def _handle_chat(self) -> None:
        try:
            body = self._read_json()
            message = str(body.get("message", "")).strip()
            user_id = str(body.get("user_id", "demo-user")).strip() or "demo-user"

            if not message:
                self._send_json({"error": "message_required"}, status=400)
                return

            result = orchestrator.handle(user_id=user_id, message=message)
            self._send_json(result)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": "internal_error", "detail": str(exc)}, status=500)

    def _handle_tool_call(self) -> None:
        try:
            body = self._read_json()
            user_id = str(body.get("user_id", "demo-user")).strip() or "demo-user"
            skill_name = str(body.get("skill_name") or body.get("name") or "").strip()
            user_message = str(body.get("message", "")).strip() or f"tool_call:{skill_name}"
            params = body.get("params", {})

            if not skill_name:
                self._send_json({"error": "skill_name_required"}, status=400)
                return
            if not isinstance(params, dict):
                self._send_json({"error": "params_must_be_object"}, status=400)
                return

            skill_result = registry.call(
                user_id=user_id,
                skill_name=skill_name,
                params=params,
                user_message=user_message,
            )
            status = skill_result.get("status")
            self._send_json(
                {
                    "ok": status not in {"error", "blocked"},
                    "skill_name": skill_name,
                    "status": status,
                    "approval_required": bool(skill_result.get("approval_required", False)),
                    "approval": skill_result.get("approval"),
                    "result": skill_result,
                }
            )
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": "internal_error", "detail": str(exc)}, status=500)

    def _handle_approve(self) -> None:
        try:
            body = self._read_json()
            action_id = str(body.get("action_id", "")).strip()
            user_id = str(body.get("user_id", "demo-user")).strip() or "demo-user"

            if not action_id:
                self._send_json({"error": "action_id_required"}, status=400)
                return

            skill_result = registry.approve_action(
                user_id=user_id,
                action_id=action_id,
                user_message=f"approve:{action_id}",
            )
            self._send_json({"message": self._approval_message(skill_result), "skill_result": skill_result})
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": "internal_error", "detail": str(exc)}, status=500)

    def _approval_message(self, result: dict) -> str:
        if result.get("message"):
            return str(result.get("message"))
        if result.get("status") == "ok":
            backup = result.get("backup")
            if backup:
                return f"已确认并写入文件：{result.get('path')}。原文件已备份到：{backup}"
            return f"已确认并写入文件：{result.get('path')}"
        if result.get("status") == "blocked":
            return f"确认失败，策略拦截：{result.get('error')}"
        return f"确认失败：{result.get('error', 'unknown_error')}"

    def _authorized(self) -> bool:
        token = registry.config.api_token
        if not token:
            return True

        auth = self.headers.get("authorization", "")
        if auth == f"Bearer {token}":
            return True
        return self.headers.get("x-agent-token", "") == token

    def _origin_allowed(self) -> str | None:
        origin = self.headers.get("origin")
        allowed = registry.config.allowed_origins
        if not origin:
            return None
        if "*" in allowed or origin in allowed:
            return origin
        return None

    def _send_common_headers(self) -> None:
        origin = self._origin_allowed()
        if origin:
            self.send_header("access-control-allow-origin", origin)
            self.send_header("vary", "Origin")
        self.send_header("access-control-allow-methods", "GET,POST,OPTIONS")
        self.send_header("access-control-allow-headers", "content-type,authorization,x-agent-token")

    def log_message(self, fmt: str, *args: object) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _read_json(self) -> dict:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)

    def _send_json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self._send_common_headers()
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path) -> None:
        resolved = path.resolve()
        if not self._is_static_path(resolved) or not resolved.is_file():
            self._send_json({"error": "not_found"}, status=404)
            return

        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        data = resolved.read_bytes()
        self.send_response(200)
        self._send_common_headers()
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _is_static_path(self, path: Path) -> bool:
        try:
            path.relative_to(STATIC_DIR.resolve())
            return True
        except ValueError:
            return False


def main() -> None:
    host = os.environ.get("AGENT_HOST", "127.0.0.1")
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PORT", "8788"))
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Enterprise Agent MVP running at http://{host}:{port}")
    print(f"Workspace: {registry.config.allowed_workspace}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
