"""Optional external connectors (MCP / Python drivers) — catalog + config store."""

from .catalog import catalog_by_id, load_catalog, reload_catalog
from .model import CatalogEntry, ConnectorConfig
from .store import (
    enabled_connectors,
    ensure_catalog_rows,
    get_connector,
    load_connectors,
    public_snapshot,
    set_enabled,
    set_token,
    upsert_connector,
)

__all__ = [
    "CatalogEntry",
    "ConnectorConfig",
    "catalog_by_id",
    "enabled_connectors",
    "ensure_catalog_rows",
    "get_connector",
    "load_catalog",
    "load_connectors",
    "public_snapshot",
    "reload_catalog",
    "set_enabled",
    "set_token",
    "upsert_connector",
]
