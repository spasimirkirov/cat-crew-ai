"""Long-term memory via mem0ai (fully local, lazy import)."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

os.environ.setdefault("MEM0_TELEMETRY", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from backend.config import MEMORY_COLLECTION_NAME, MEMORY_EMBEDDER_MODEL, MEMORY_HISTORY_DB_PATH, MEMORY_LLM_MODEL, MEMORY_QDRANT_PATH, MEMORY_USER_ID


def _hf_model_cached() -> bool:
    """all-MiniLM is already in ~/.cache — no Hugging Face round-trip needed."""
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    slug = MEMORY_EMBEDDER_MODEL.replace("/", "--")
    names = (
        f"models--sentence-transformers--{slug}",
        f"models--{slug}",
    )
    return any((cache / name).exists() for name in names)


def _use_local_embedder() -> bool:
    """MiniLM only, from disk. Do not set HF_HUB_OFFLINE — Whisper needs the hub."""
    return _hf_model_cached()


def _role_brain(role: str, project_id: str | None = None) -> tuple[str, str]:
    """``(host, model)`` for ``role``: the project's override (Project Settings) when this turn runs in
    a project, else the global pick. ``project_id`` defaults to the current turn's project."""
    if project_id is None:
        try:
            from backend.platform.runtime import current_project_id

            project_id = current_project_id()
        except Exception:  # noqa: BLE001
            project_id = ""
    if project_id:
        try:
            from backend.services.model_resolver import get_effective_model

            eff = get_effective_model(project_id, role)
            return eff.host, eff.model
        except Exception:  # noqa: BLE001 - fall back to the global pick
            pass
    from backend.services.settings_store import resolve_role_brain

    return resolve_role_brain(role)


def resolve_memory_llm(project_id: str | None = None) -> str:
    """The model mem0 uses for fact extraction. Follows the Memory role, per project."""
    if MEMORY_LLM_MODEL:
        return MEMORY_LLM_MODEL
    _host, model = _role_brain("memory", project_id)
    return model or ""


def _build_mem0_config(llm_model: str = "") -> dict[str, Any]:
    local_only = _use_local_embedder()
    embedder_cfg: dict[str, Any] = {"model": MEMORY_EMBEDDER_MODEL}
    if local_only:
        embedder_cfg["model_kwargs"] = {"local_files_only": True}
    model = (llm_model or resolve_memory_llm()).strip()
    from backend.services.api_access import compat_root, resolved_key, spec_for

    host, _model = _role_brain("memory")
    spec = spec_for(host)
    root = compat_root(spec.base_url) if spec is not None else ""
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": MEMORY_COLLECTION_NAME,
                "embedding_model_dims": 384,
                "path": str(MEMORY_QDRANT_PATH),
                "on_disk": True,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": model,
                "api_key": resolved_key(host) if spec is not None else "",
                "openai_base_url": root,
            },
        },
        "embedder": {
            "provider": "huggingface",
            "config": embedder_cfg,
        },
        "history_db_path": str(MEMORY_HISTORY_DB_PATH),
    }


def _is_missing_llm(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "not found" in text or "extraction failed" in text or "404" in text


class Mem0Memory:
    """mem0 loads on a background thread so it does not block the UI."""

    def __init__(self) -> None:
        self._memory = None
        self._ready = False
        self._failed = False
        self._error = ""
        self._llm_model = ""
        self._lock = threading.Lock()

    def start_background(self) -> None:
        thread = threading.Thread(target=self._warmup, name="catcrew-mem0", daemon=True)
        thread.start()

    def _warmup(self) -> None:
        try:
            import warnings

            warnings.filterwarnings(
                "ignore",
                message=r".*get_sentence_embedding_dimension.*",
            )
            self._ensure()
            self._ready = True
            print("[memory] mem0 is ready.", flush=True)
        except Exception as exc:  # noqa: BLE001
            self._failed = True
            self._error = str(exc)
            print(f"[memory] Failed to load: {exc}", flush=True)

    def _apply_llm(self, mem: object, want: str) -> None:
        llm = getattr(mem, "llm", None)
        cfg = getattr(llm, "config", None) if llm is not None else None
        if cfg is not None:
            cfg.model = want
        self._llm_model = want

    def _ensure(self):
        want = resolve_memory_llm()
        with self._lock:
            if not want:
                return None
            if self._memory is None:
                local_only = _use_local_embedder()
                from mem0 import Memory  # heavy import — only here

                MEMORY_HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                MEMORY_QDRANT_PATH.mkdir(parents=True, exist_ok=True)
                self._memory = Memory.from_config(_build_mem0_config(want))
                if local_only:
                    print("[memory] embedder from local cache (no HF Hub).", flush=True)
            self._apply_llm(self._memory, want)
            return self._memory

    def add_memory(self, user_input: str, ai_response: str) -> None:
        if self._failed:
            return
        mem = self._ensure()
        if mem is None:
            return
        try:
            text = f"User: {user_input}\nAssistant: {ai_response}"
            mem.add(text, user_id=MEMORY_USER_ID)
        except Exception as exc:  # noqa: BLE001
            if _is_missing_llm(exc):
                return
            print(f"[memory] add failed: {exc}", flush=True)

    def get_relevant_memories(self, user_input: str, limit: int = 5) -> list[str]:
        if self._failed:
            return []
        mem = self._ensure()
        if mem is None:
            return []
        try:
            results = mem.search(
                user_input, filters={"user_id": MEMORY_USER_ID}, top_k=limit
            )
        except Exception as exc:  # noqa: BLE001
            if _is_missing_llm(exc):
                return []
            print(f"[memory] search failed: {exc}", flush=True)
            return []
        if isinstance(results, dict):
            results = results.get("results", [])
        memories: list[str] = []
        for item in results:
            if isinstance(item, dict) and item.get("memory"):
                memories.append(item["memory"])
        return memories

    def search_memory(self, user_input: str, limit: int = 5) -> list[str]:
        return self.get_relevant_memories(user_input, limit)


def build_memory_context(memories: list[str]) -> str:
    lines = "\n".join(f"- {m}" for m in memories)
    return (
        "Facts you already know about the user from earlier conversations. "
        "Use them for more personal, consistent answers, "
        "but do not mention them unless it helps:\n" + lines
    )
