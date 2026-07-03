from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_VERSION = "0.3.0"


def _csv_env(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    root: Path
    allowed_workspace: Path
    audit_log: Path
    api_token: str | None
    allowed_origins: tuple[str, ...]
    user_policy_file: Path | None
    max_file_bytes: int = 200_000

    model_api_url: str | None = None
    model_api_key: str | None = None
    model_name: str = "gpt-4o-mini"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_tls: bool = True

    calendar_export_dir: Path | None = None

    script_allowlist: tuple[str, ...] = ()
    script_timeout_seconds: int = 15

    web_allowed_hosts: tuple[str, ...] = ()
    internal_allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def from_root(cls, root: Path) -> "AppConfig":
        workspace_raw = os.environ.get("AGENT_ALLOWED_WORKSPACE", "").strip()
        if workspace_raw:
            workspace = Path(workspace_raw)
            if not workspace.is_absolute():
                workspace = root / workspace
        else:
            workspace = root / "data" / "workspace"

        policy_raw = os.environ.get("AGENT_USER_POLICY_FILE", "").strip()
        user_policy_file = Path(policy_raw) if policy_raw else None
        if user_policy_file and not user_policy_file.is_absolute():
            user_policy_file = root / user_policy_file

        calendar_raw = os.environ.get("AGENT_CALENDAR_EXPORT_DIR", "").strip()
        calendar_dir = Path(calendar_raw) if calendar_raw else root / "data" / "calendar"
        if not calendar_dir.is_absolute():
            calendar_dir = root / calendar_dir

        return cls(
            root=root,
            allowed_workspace=workspace,
            audit_log=root / "data" / "logs" / "audit.jsonl",
            api_token=os.environ.get("AGENT_API_TOKEN") or None,
            allowed_origins=_csv_env(
                "AGENT_ALLOWED_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8788,http://127.0.0.1:8788",
            ),
            user_policy_file=user_policy_file,
            max_file_bytes=int(os.environ.get("AGENT_MAX_FILE_BYTES", "200000")),
            model_api_url=os.environ.get("AGENT_MODEL_API_URL") or None,
            model_api_key=os.environ.get("AGENT_MODEL_API_KEY") or None,
            model_name=os.environ.get("AGENT_MODEL_NAME", "gpt-4o-mini"),
            smtp_host=os.environ.get("AGENT_SMTP_HOST") or None,
            smtp_port=int(os.environ.get("AGENT_SMTP_PORT", "587")),
            smtp_user=os.environ.get("AGENT_SMTP_USER") or None,
            smtp_password=os.environ.get("AGENT_SMTP_PASSWORD") or None,
            smtp_from=os.environ.get("AGENT_SMTP_FROM") or os.environ.get("AGENT_SMTP_USER") or None,
            smtp_tls=_bool_env("AGENT_SMTP_TLS", True),
            calendar_export_dir=calendar_dir,
            script_allowlist=tuple(item.lower() for item in _csv_env("AGENT_SCRIPT_ALLOWLIST")),
            script_timeout_seconds=int(os.environ.get("AGENT_SCRIPT_TIMEOUT_SECONDS", "15")),
            web_allowed_hosts=tuple(item.lower() for item in _csv_env("AGENT_WEB_ALLOWED_HOSTS")),
            internal_allowed_hosts=tuple(item.lower() for item in _csv_env("AGENT_INTERNAL_ALLOWED_HOSTS")),
        )


BLOCKED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_dsa",
    "credentials.json",
}

BLOCKED_EXTENSIONS = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".crt",
}

BLOCKED_NAME_PARTS = {
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
}

READABLE_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".log",
    ".ics",
}

