"""App-wide configuration for cat-crew-ai."""

from __future__ import annotations

import os
from pathlib import Path

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

# --- CatCrewAI home (skills/prompts overlay, platform catalog). ---
# Default home: the historical ``C:\\CatCrewAI`` on Windows (existing installs keep
# their overlay), ``~/CatCrewAI`` elsewhere. ``CATCREW_HOME`` overrides both.
_DEFAULT_HOME = r"C:\CatCrewAI" if os.name == "nt" else str(Path.home() / "CatCrewAI")
CATCREW_HOME = Path(os.environ.get("CATCREW_HOME", _DEFAULT_HOME)).expanduser()

# --- OpenRouter: every vendor's models through one key, chosen by slug ("deepseek/deepseek-v4-pro",
# "anthropic/claude-sonnet-4"). The vendors' own APIs (OpenAI, Anthropic, Gemini, DeepSeek, Groq,
# Mistral, xAI) are hosts too, each with its own key: see services/api_access.CATALOG.
OPENROUTER_API_KEY = os.environ.get("CATCREW_OPENROUTER_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("CATCREW_OPENROUTER_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("CATCREW_OPENROUTER_MODEL", "openrouter/auto")
# OpenRouter prompt caching (Anthropic-style cache_control breakpoints on the system prompt and
# tool schemas): on by default, cuts repeated input tokens by a large margin on models that support
# it and is a harmless no-op (an ignored field) on ones that don't.
OPENROUTER_PROMPT_CACHING = os.environ.get("CATCREW_OPENROUTER_PROMPT_CACHING", "1") not in ("0", "false", "False")

# --- Jev AI ("System One"): TypeSafe AI's decision model -- it answers small typed yes/no, choice
# or score questions and never drafts text (services/jev_client.py, middleware/jev_gatekeeper.py).
# It runs on OpenRouter's Decisions API (the OpenRouter key) or on TypeSafe's official API (the
# TypeSafe key); both keys live in the key catalog (services/api_access.py). The two settings below
# only re-point the OpenRouter path. Off by default; a missing key or a failed call never blocks a turn.
JEV_BASE_URL = os.environ.get("CATCREW_JEV_URL", "https://openrouter.ai/api/alpha/decisions")
JEV_MODEL = os.environ.get("CATCREW_JEV_MODEL", "typesafe/jev-latest")
JEV_ENABLED = os.environ.get("CATCREW_JEV_ENABLED", "0") in ("1", "true", "True")
JEV_CONFIDENCE_THRESHOLD = float(os.environ.get("CATCREW_JEV_CONFIDENCE", "0.92") or 0.92)

# Workspace for agent tools (files, shell, VS Code).
# Live roots come from the session Files explorer (may be empty).
# Override with CATCREW_WORKSPACE only as a rare env fallback for scripts.
WORKSPACE_DIR = Path(
    os.environ.get("CATCREW_WORKSPACE", str(BASE_DIR))
).expanduser().resolve()

# Defaults only. Live values live in Settings → General (`data/settings.json`).
COMMAND_TIMEOUT = int(os.environ.get("CATCREW_COMMAND_TIMEOUT", "60"))
TOOL_RESULT_LIMIT = int(os.environ.get("CATCREW_TOOL_RESULT_LIMIT", "12000"))
# 0 = unlimited tool rounds (Stop is the only halt). Positive = a hard cap.
AGENT_MAX_ROUNDS = int(os.environ.get("CATCREW_AGENT_MAX_ROUNDS", "0"))
# Delegated work (a cat running a work order) gets a finite default even when the setting above is 0,
# so a drifting specialist cannot loop forever; the work order then ends as `blocked`. 0 = unlimited.
DELEGATE_MAX_ROUNDS = int(os.environ.get("CATCREW_DELEGATE_MAX_ROUNDS", "40") or 0)
# A chat context above this many tokens (estimated, ~4 characters each) is summarized before it is sent on.
# 0 = automatic: each model's own window (services/provider_limits.py). A number forces one summarize-at size for every model.
CONTEXT_TOKEN_LIMIT = int(os.environ.get("CATCREW_CONTEXT_TOKENS", "0") or 0)
COMPACT_KEEP = int(os.environ.get("CATCREW_COMPACT_KEEP", "10"))
# How much recent conversation a compaction keeps verbatim, in estimated tokens (as Pi does), capped
# at half the model's summarize-at threshold so a small local window still gets room. 0 = keep by
# message count (COMPACT_KEEP) instead.
COMPACT_KEEP_TOKENS = int(os.environ.get("CATCREW_COMPACT_KEEP_TOKENS", "20000") or 0)
# Opt-in: one shared message history per work run, the cat being a swapped mask (see platform/shared_context.py).
UNIFIED_CONTEXT = os.environ.get("CATCREW_UNIFIED_CONTEXT", "0") in ("1", "true", "True")

# --- Long-term memory (mem0ai, fully local) ---
MEMORY_ENABLED = os.environ.get("CATCREW_MEMORY_ENABLED", "1") not in ("0", "false", "False")
MEMORY_USER_ID = "catcrew_user"
# Empty = follow the Memory role on the Models page.
MEMORY_LLM_MODEL = os.environ.get("CATCREW_MEM0_LLM_MODEL", "").strip()
MEMORY_EMBEDDER_MODEL = os.environ.get("CATCREW_MEM0_EMBEDDER_MODEL", "all-MiniLM-L6-v2")
MEMORY_HISTORY_DB_PATH = BASE_DIR / "data" / "mem0" / "history.db"
MEMORY_QDRANT_PATH = BASE_DIR / "data" / "mem0" / "qdrant"
MEMORY_COLLECTION_NAME = "catcrew_memory"
MEMORY_TOP_K = int(os.environ.get("CATCREW_MEMORY_TOP_K", "5"))

# Defaults for the `character_window` section of data/settings.json. The overlay UI is gone;
# the section stays in the settings schema only so existing settings files keep loading
# unchanged (see the dead-code item in todo/TECHNICAL_DEBT_AND_OPTIMIZATIONS.md, N-09).
AVATAR_WINDOW_SIZE = (240, 360)
CHARACTER_SIZE_PRESETS = {
    "compact": (200, 300),
    "default": (240, 360),
    "large": (300, 460),
}

# Optional Brave Search API (https://brave.com/search/api/) — used after DuckDuckGo if set.
BRAVE_SEARCH_API_KEY = os.environ.get(
    "CATCREW_BRAVE_KEY", os.environ.get("BRAVE_SEARCH_API_KEY", "")
)

# Optional SearXNG JSON instance. Empty = skip (default search is DuckDuckGo via ddgs).
SEARXNG_URL = os.environ.get("CATCREW_SEARXNG_URL", "").strip()

# ComfyUI image generation — separate process from the chat LLM.
COMFYUI_URL = os.environ.get("CATCREW_COMFYUI_URL", "http://127.0.0.1:8188")
COMFYUI_ROOT = os.environ.get("CATCREW_COMFYUI_ROOT", "")

# --- Self-Healing / Self-Restart ---
SELF_HEAL_MAX_ATTEMPTS = int(os.environ.get("CATCREW_SELF_HEAL_ATTEMPTS", "3"))
SELF_HEAL_TEST_TIMEOUT = int(os.environ.get("CATCREW_SELF_HEAL_TIMEOUT", "15"))
