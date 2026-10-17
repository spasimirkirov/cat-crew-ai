"""App-wide configuration for jinx-ai."""

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

# --- Local llama-server (GGUF). Default folder is on the OS disk. ---
JINX_HOME = Path(os.environ.get("JINX_HOME", r"C:\Jinx-AI")).expanduser()
DEFAULT_MODELS_DIR = Path(os.environ.get("JINX_MODELS_DIR", str(JINX_HOME / "models")))
DEFAULT_BIN_DIR = Path(os.environ.get("JINX_BIN_DIR", str(JINX_HOME / "bin")))
LLAMA_SERVER_URL = os.environ.get("JINX_LLAMA_URL", "http://127.0.0.1:8787")
LLAMA_SERVER_PORT = int(os.environ.get("JINX_LLAMA_PORT", "8787"))
LLAMA_CONTEXT = int(os.environ.get("JINX_LLAMA_CTX", "32768"))

# --- DeepSeek API ---
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_DEEPSEEK_RAW = os.environ.get("JINX_DEEPSEEK_MODEL", "deepseek-v4-pro")
_DEEPSEEK_LEGACY = {
    "deepseek-chat": "deepseek-v4-pro",
    "deepseek-reasoner": "deepseek-v4-pro",
}
DEEPSEEK_MODEL = _DEEPSEEK_LEGACY.get(_DEEPSEEK_RAW, _DEEPSEEK_RAW)
DEEPSEEK_BASE_URL = os.environ.get("JINX_DEEPSEEK_URL", "https://api.deepseek.com")
DEEPSEEK_TIMEOUT = int(os.environ.get("JINX_DEEPSEEK_TIMEOUT", "180"))

OPENROUTER_API_KEY = os.environ.get("JINX_OPENROUTER_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("JINX_OPENROUTER_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("JINX_OPENROUTER_MODEL", "openrouter/auto")

OPENAI_API_KEY = os.environ.get("JINX_OPENAI_KEY", os.environ.get("OPENAI_API_KEY", ""))
OPENAI_BASE_URL = os.environ.get("JINX_OPENAI_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.environ.get("JINX_OPENAI_MODEL", "gpt-4o-mini")

# --- Window ---
FRAMELESS = False
TRANSLUCENT = False
WINDOW_TITLE = "Jinx AI - The Best AI Assistant"
CHAT_WINDOW_SIZE = (1580, 900)

# Workspace for agent tools (files, shell, VS Code).
# Live roots come from the session Files explorer (may be empty).
# Override with JINX_WORKSPACE only as a rare env fallback for scripts.
WORKSPACE_DIR = Path(
    os.environ.get("JINX_WORKSPACE", str(BASE_DIR))
).expanduser().resolve()

# Defaults only. Live values live in Settings → General (`data/settings.json`).
COMMAND_TIMEOUT = int(os.environ.get("JINX_COMMAND_TIMEOUT", "60"))
TOOL_RESULT_LIMIT = int(os.environ.get("JINX_TOOL_RESULT_LIMIT", "12000"))
# 0 = unlimited tool rounds (Stop is the only halt). Positive = a hard cap.
AGENT_MAX_ROUNDS = int(os.environ.get("JINX_AGENT_MAX_ROUNDS", "0"))
COMPACT_CHARS = int(os.environ.get("JINX_COMPACT_CHARS", "80000"))
COMPACT_KEEP = int(os.environ.get("JINX_COMPACT_KEEP", "10"))

# --- Long-term memory (mem0ai, fully local) ---
MEMORY_ENABLED = os.environ.get("JINX_MEMORY_ENABLED", "1") not in ("0", "false", "False")
MEMORY_USER_ID = "jinx_user"
# Empty = follow AI Models → General Memory role (local llama-server).
MEMORY_LLM_MODEL = os.environ.get("JINX_MEM0_LLM_MODEL", "").strip()
MEMORY_EMBEDDER_MODEL = os.environ.get("JINX_MEM0_EMBEDDER_MODEL", "all-MiniLM-L6-v2")
MEMORY_HISTORY_DB_PATH = BASE_DIR / "data" / "mem0" / "history.db"
MEMORY_QDRANT_PATH = BASE_DIR / "data" / "mem0" / "qdrant"
MEMORY_COLLECTION_NAME = "jinx_memory"
MEMORY_TOP_K = int(os.environ.get("JINX_MEMORY_TOP_K", "5"))

# --- Avatar / desktop orb ---
AVATAR_ENABLED = os.environ.get("JINX_AVATAR_ENABLED", "1") not in ("0", "false", "False")
AVATAR_AUTO_SHOW = os.environ.get("JINX_AVATAR_AUTO", "1") in ("1", "true", "True")
AVATAR_WINDOW_SIZE = (240, 360)
CHARACTER_SIZE_PRESETS = {
    "compact": (200, 300),
    "default": (240, 360),
    "large": (300, 460),
}

# Optional Brave Search API (https://brave.com/search/api/) — used after DuckDuckGo if set.
BRAVE_SEARCH_API_KEY = os.environ.get(
    "JINX_BRAVE_KEY", os.environ.get("BRAVE_SEARCH_API_KEY", "")
)

# Optional SearXNG JSON instance. Empty = skip (default search is DuckDuckGo via ddgs).
SEARXNG_URL = os.environ.get("JINX_SEARXNG_URL", "").strip()

# ComfyUI image generation — separate process from the chat LLM.
COMFYUI_URL = os.environ.get("JINX_COMFYUI_URL", "http://127.0.0.1:8188")
COMFYUI_ROOT = os.environ.get("JINX_COMFYUI_ROOT", "")

# --- Voice / listening ---
TTS_ENABLED = os.environ.get("JINX_TTS", "1") not in ("0", "false", "False")
STT_ENABLED = os.environ.get("JINX_STT", "1") not in ("0", "false", "False")
TTS_VOICE_EN = os.environ.get("JINX_TTS_VOICE_EN", "en-US-JennyNeural")
WHISPER_MODEL = os.environ.get("JINX_WHISPER_MODEL", "base")
WHISPER_DIR = BASE_DIR / "data" / "whisper"
TTS_CACHE_DIR = BASE_DIR / "data" / "tts"

# --- Self-Healing / Self-Restart ---
SELF_HEAL_MAX_ATTEMPTS = int(os.environ.get("JINX_SELF_HEAL_ATTEMPTS", "3"))
SELF_HEAL_TEST_TIMEOUT = int(os.environ.get("JINX_SELF_HEAL_TIMEOUT", "15"))
