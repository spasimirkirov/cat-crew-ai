"""Session message tree: branches live in one file, head is the current tip."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

Message = dict[str, Any]


def stamp_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_node_id() -> str:
    return uuid.uuid4().hex[:10]


def ensure_tree(session: dict[str, Any]) -> dict[str, Any]:
    messages = list(session.get("messages") or [])
    prev: str | None = None
    known: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        node_id = str(message.get("id") or "").strip() or new_node_id()
        message["id"] = node_id
        if node_id in known:
            node_id = new_node_id()
            message["id"] = node_id
        known.add(node_id)
        parent = message.get("parent")
        if parent == "":
            parent = None
        if "parent" not in message:
            message["parent"] = prev
        else:
            message["parent"] = parent
        if message.get("role") in ("user", "assistant"):
            prev = node_id
    session["messages"] = messages
    head = str(session.get("head") or "").strip()
    if head not in known:
        session["head"] = prev
    return session


def node_map(session: dict[str, Any]) -> dict[str, Message]:
    out: dict[str, Message] = {}
    for message in session.get("messages") or []:
        if isinstance(message, dict) and message.get("id"):
            out[str(message["id"])] = message
    return out


def children_map(session: dict[str, Any]) -> dict[str | None, list[Message]]:
    groups: dict[str | None, list[Message]] = {}
    for message in session.get("messages") or []:
        if not isinstance(message, dict) or not message.get("id"):
            continue
        parent = message.get("parent")
        key = None if parent in (None, "") else str(parent)
        groups.setdefault(key, []).append(message)
    return groups


def path_to_head(session: dict[str, Any], head: str | None = None) -> list[Message]:
    nodes = node_map(session)
    tip = head if head is not None else session.get("head")
    if not tip or tip not in nodes:
        return [
            msg
            for msg in (session.get("messages") or [])
            if isinstance(msg, dict) and msg.get("role") in ("user", "assistant")
        ]
    chain: list[Message] = []
    seen: set[str] = set()
    current = str(tip)
    while current and current in nodes and current not in seen:
        seen.add(current)
        chain.append(nodes[current])
        parent = nodes[current].get("parent")
        current = str(parent) if parent else ""
    chain.reverse()
    return chain


def _history_rows(
    session: dict[str, Any],
    *,
    include_hidden: bool,
) -> list[Message]:
    out: list[Message] = []
    for message in path_to_head(session):
        if message.get("role") not in ("user", "assistant"):
            continue
        if not include_hidden and message.get("hidden"):
            continue
        content = message.get("content")
        if not content:
            continue
        row: Message = {
            "id": message.get("id"),
            "role": message["role"],
            "content": content,
        }
        created = str(message.get("created_at") or "").strip()
        if created:
            row["created_at"] = created
        if message.get("hidden"):
            row["hidden"] = True
        activity = message.get("activity")
        if isinstance(activity, dict) and activity.get("steps"):
            row["activity"] = activity
        out.append(row)
    return out


def visible_history(session: dict[str, Any]) -> list[Message]:
    """UI transcript path — skips hidden system/resume turns."""
    return _history_rows(session, include_hidden=False)


def provider_history(session: dict[str, Any]) -> list[Message]:
    """Model context — includes hidden internal turns."""
    return [
        {"role": msg["role"], "content": msg.get("content", "")}
        for msg in _history_rows(session, include_hidden=True)
    ]


def set_head(session: dict[str, Any], message_id: str) -> bool:
    nodes = node_map(session)
    if message_id not in nodes:
        return False
    session["head"] = message_id
    return True


def append_turn(
    session: dict[str, Any],
    role: str,
    content: str,
    *,
    hidden: bool = False,
) -> Message:
    ensure_tree(session)
    node: Message = {
        "id": new_node_id(),
        "parent": session.get("head") or None,
        "role": role,
        "content": content,
        "created_at": stamp_now(),
    }
    if hidden:
        node["hidden"] = True
    session.setdefault("messages", []).append(node)
    session["head"] = node["id"]
    return node


def tree_outline(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Flat outline for UI: depth, node, on_current_path, is_head."""
    ensure_tree(session)
    groups = children_map(session)
    current_ids = {str(msg.get("id")) for msg in path_to_head(session) if msg.get("id")}
    head = session.get("head")
    rows: list[dict[str, Any]] = []

    def walk(parent: str | None, depth: int) -> None:
        for node in groups.get(parent, []):
            node_id = str(node.get("id"))
            if node.get("hidden"):
                walk(node_id, depth)
                continue
            rows.append(
                {
                    "id": node_id,
                    "depth": depth,
                    "role": node.get("role"),
                    "content": str(node.get("content") or ""),
                    "on_path": node_id in current_ids,
                    "is_head": node_id == head,
                }
            )
            walk(node_id, depth + 1)

    walk(None, 0)
    return rows
