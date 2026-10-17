"""Configured connectors — ``data/connectors.json`` (secrets stay on disk)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..config import BASE_DIR
from .catalog import catalog_by_id, load_catalog
from .model import ConnectorConfig

STORE_PATH = BASE_DIR / "data" / "connectors.json"
_lock = threading.RLock()


def store_path() -> Path:
    return STORE_PATH


def load_connectors() -> list[ConnectorConfig]:
    with _lock:
        data = _read()
    items = data.get("connectors")
    if not isinstance(items, list):
        return []
    out: list[ConnectorConfig] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        cfg = ConnectorConfig.from_dict(raw)
        if cfg.id:
            out.append(cfg)
    return out


def enabled_connectors() -> list[ConnectorConfig]:
    return [item for item in load_connectors() if item.enabled]


def get_connector(connector_id: str) -> ConnectorConfig | None:
    needle = str(connector_id or "").strip().lower()
    for item in load_connectors():
        if item.id.lower() == needle:
            return item
    return None


def upsert_connector(config: ConnectorConfig) -> ConnectorConfig:
    if not config.id.strip():
        raise ValueError("Connector id is required.")
    with _lock:
        data = _read()
        items = list(data.get("connectors") or [])
        found = False
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                continue
            if str(raw.get("id") or "").strip().lower() == config.id.lower():
                items[index] = config.to_storage_dict()
                found = True
                break
        if not found:
            items.append(config.to_storage_dict())
        data["connectors"] = items
        _write(data)
    return config


def set_enabled(connector_id: str, enabled: bool) -> ConnectorConfig | None:
    cfg = get_connector(connector_id)
    if cfg is None:
        preset = catalog_by_id(connector_id)
        if preset is None:
            return None
        cfg = ConnectorConfig(
            id=preset.id,
            name=preset.name,
            kind=preset.kind,
            enabled=enabled,
            auth=preset.auth,
            base_url=preset.base_url,
            icon=preset.icon,
            docs_url=preset.docs_url,
            driver=preset.driver,
            header_name=preset.header_name,
            preload_tools=preset.preload_tools,
            catalog_id=preset.id,
        )
    else:
        cfg.enabled = enabled
    return upsert_connector(cfg)


def set_token(connector_id: str, token: str) -> ConnectorConfig | None:
    cfg = get_connector(connector_id) or _from_catalog(connector_id)
    if cfg is None:
        return None
    cfg.token = str(token or "").strip()
    return upsert_connector(cfg)


def ensure_catalog_rows() -> list[ConnectorConfig]:
    """Make sure every catalog preset has a (disabled) row in the store."""
    existing = {item.id.lower(): item for item in load_connectors()}
    changed = False
    for preset in load_catalog():
        if preset.id.lower() in existing:
            continue
        upsert_connector(
            ConnectorConfig(
                id=preset.id,
                name=preset.name,
                kind=preset.kind,
                enabled=False,
                auth=preset.auth,
                base_url=preset.base_url,
                icon=preset.icon,
                docs_url=preset.docs_url,
                driver=preset.driver,
                header_name=preset.header_name,
                preload_tools=preset.preload_tools,
                catalog_id=preset.id,
            )
        )
        changed = True
    return load_connectors() if changed or existing else load_connectors()


def public_snapshot() -> list[dict[str, Any]]:
    ensure_catalog_rows()
    return [item.to_public_dict() for item in load_connectors()]


def _from_catalog(connector_id: str) -> ConnectorConfig | None:
    preset = catalog_by_id(connector_id)
    if preset is None:
        return None
    return ConnectorConfig(
        id=preset.id,
        name=preset.name,
        kind=preset.kind,
        enabled=False,
        auth=preset.auth,
        base_url=preset.base_url,
        icon=preset.icon,
        docs_url=preset.docs_url,
        driver=preset.driver,
        header_name=preset.header_name,
        preload_tools=preset.preload_tools,
        catalog_id=preset.id,
    )


def _read() -> dict[str, Any]:
    path = store_path()
    if not path.is_file():
        return {"connectors": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"connectors": []}
    if not isinstance(data, dict):
        return {"connectors": []}
    data.setdefault("connectors", [])
    return data


def _write(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
