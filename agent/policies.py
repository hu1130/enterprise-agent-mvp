from __future__ import annotations

from pathlib import Path

from .config import BLOCKED_EXTENSIONS, BLOCKED_FILE_NAMES, BLOCKED_NAME_PARTS, READABLE_EXTENSIONS


class PolicyError(RuntimeError):
    """Raised when a skill request violates the local policy."""


class FilePolicy:
    def __init__(self, allowed_root: Path, max_file_bytes: int) -> None:
        self.allowed_root = allowed_root.resolve()
        self.max_file_bytes = max_file_bytes

    def validate_read_path(self, requested: str | Path) -> Path:
        candidate = self._resolve_requested_path(requested)
        self._ensure_inside_workspace(candidate)
        self._ensure_not_sensitive(candidate)
        self._ensure_text_file(candidate)

        if not candidate.exists():
            raise PolicyError(f"文件不存在: {requested}")
        if not candidate.is_file():
            raise PolicyError(f"不是文件: {requested}")
        if candidate.stat().st_size > self.max_file_bytes:
            raise PolicyError("文件太大，第一版不直接读取")

        return candidate

    def validate_write_path(self, requested: str | Path) -> Path:
        candidate = self._resolve_requested_path(requested)
        self._ensure_inside_workspace(candidate)
        self._ensure_not_sensitive(candidate)
        self._ensure_text_file(candidate)

        if not candidate.parent.exists() or not candidate.parent.is_dir():
            raise PolicyError("目标目录不存在，第一版不自动创建目录")
        if candidate.exists() and not candidate.is_file():
            raise PolicyError(f"目标不是文件: {requested}")

        return candidate

    def validate_list_root(self, requested: str | Path = ".") -> Path:
        candidate = self._resolve_requested_path(requested)
        self._ensure_inside_workspace(candidate)
        if not candidate.exists():
            raise PolicyError(f"目录不存在: {requested}")
        if not candidate.is_dir():
            raise PolicyError(f"不是目录: {requested}")
        return candidate

    def allowed_file(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            self._ensure_inside_workspace(resolved)
            self._ensure_not_sensitive(resolved)
            self._ensure_text_file(resolved)
            return resolved.is_file() and resolved.stat().st_size <= self.max_file_bytes
        except (OSError, PolicyError):
            return False

    def _resolve_requested_path(self, requested: str | Path) -> Path:
        raw = Path(str(requested).strip() or ".")
        if raw.is_absolute():
            candidate = raw
        else:
            candidate = self.allowed_root / raw
        return candidate.resolve()

    def _ensure_inside_workspace(self, path: Path) -> None:
        try:
            path.relative_to(self.allowed_root)
        except ValueError as exc:
            raise PolicyError("越权路径：只能访问授权 workspace 目录") from exc

    def _ensure_not_sensitive(self, path: Path) -> None:
        lowered = path.name.lower()
        if lowered in BLOCKED_FILE_NAMES:
            raise PolicyError("敏感文件被策略拦截")
        if path.suffix.lower() in BLOCKED_EXTENSIONS:
            raise PolicyError("密钥或证书文件被策略拦截")
        if any(part in lowered for part in BLOCKED_NAME_PARTS):
            raise PolicyError("疑似凭据文件被策略拦截")

    def _ensure_text_file(self, path: Path) -> None:
        if path.suffix.lower() not in READABLE_EXTENSIONS:
            raise PolicyError("第一版只允许读写文本类文件")

