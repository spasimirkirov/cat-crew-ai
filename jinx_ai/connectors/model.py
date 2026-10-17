"""Connector data model — catalog presets vs configured instances."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

AuthType = Literal["none", "header", "oauth_token", "bearer"]
ConnectorKind = Literal["mcp_http", "python", "builtin_http"]


@dataclass
class CatalogEntry:
    """Discovery preset (no secrets)."""

    id: str
    name: str
    description: str = ""
    kind: ConnectorKind = "mcp_http"
    auth: AuthType = "none"
    icon: str = "plug"
    base_url: str = ""
    docs_url: str = ""
    driver: str = ""
    header_name: str = "Authorization"
    preload_tools: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CatalogEntry:
        return cls(
            id=str(raw.get("id") or "").strip(),
            name=str(raw.get("name") or raw.get("id") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            kind=_kind(raw.get("kind")),
            auth=_auth(raw.get("auth")),
            icon=str(raw.get("icon") or "plug").strip() or "plug",
            base_url=str(raw.get("base_url") or "").strip(),
            docs_url=str(raw.get("docs_url") or "").strip(),
            driver=str(raw.get("driver") or "").strip(),
            header_name=str(raw.get("header_name") or "Authorization").strip()
            or "Authorization",
            preload_tools=bool(raw.get("preload_tools")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectorConfig:
    """Configured connector — may hold secrets; redacted on public read."""

    id: str
    name: str = ""
    kind: ConnectorKind = "mcp_http"
    enabled: bool = False
    auth: AuthType = "none"
    base_url: str = ""
    icon: str = "plug"
    docs_url: str = ""
    driver: str = ""
    header_name: str = "Authorization"
    token: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    preload_tools: bool = False
    tool_allowlist: list[str] = field(default_factory=list)
    tool_denylist: list[str] = field(default_factory=list)
    catalog_id: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ConnectorConfig:
        headers_raw = raw.get("headers")
        headers: dict[str, str] = {}
        if isinstance(headers_raw, dict):
            headers = {str(k): str(v) for k, v in headers_raw.items() if str(v)}
        allow = raw.get("tool_allowlist") or raw.get("allowlist") or []
        deny = raw.get("tool_denylist") or raw.get("denylist") or []
        return cls(
            id=str(raw.get("id") or "").strip(),
            name=str(raw.get("name") or "").strip(),
            kind=_kind(raw.get("kind")),
            enabled=bool(raw.get("enabled")),
            auth=_auth(raw.get("auth")),
            base_url=str(raw.get("base_url") or "").strip(),
            icon=str(raw.get("icon") or "plug").strip() or "plug",
            docs_url=str(raw.get("docs_url") or "").strip(),
            driver=str(raw.get("driver") or "").strip(),
            header_name=str(raw.get("header_name") or "Authorization").strip()
            or "Authorization",
            token=str(raw.get("token") or "").strip(),
            headers=headers,
            preload_tools=bool(raw.get("preload_tools")),
            tool_allowlist=[str(x) for x in allow if str(x).strip()],
            tool_denylist=[str(x) for x in deny if str(x).strip()],
            catalog_id=str(raw.get("catalog_id") or raw.get("id") or "").strip(),
        )

    def display_name(self) -> str:
        return self.name or self.id

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize without leaking secrets (UI / prompts)."""
        data = asdict(self)
        if data.get("token"):
            data["token"] = _redact(str(data["token"]))
        headers = data.get("headers") or {}
        data["headers"] = {k: _redact(str(v)) for k, v in headers.items()}
        data["has_secret"] = bool(self.token) or bool(self.headers)
        return data

    def to_storage_dict(self) -> dict[str, Any]:
        return asdict(self)


def _kind(raw: Any) -> ConnectorKind:
    text = str(raw or "mcp_http").strip().lower()
    if text in {"mcp_http", "python", "builtin_http"}:
        return text  # type: ignore[return-value]
    return "mcp_http"


def _auth(raw: Any) -> AuthType:
    text = str(raw or "none").strip().lower()
    if text in {"none", "header", "oauth_token", "bearer"}:
        return text  # type: ignore[return-value]
    return "none"


def _redact(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "••••"
    return text[:3] + "••••" + text[-2:]
