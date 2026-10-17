"""AI providers: one OpenAI-compatible client for every host (OpenRouter and the vendors' own APIs)."""

from .base import AgentAborted, AssistantMessage, BaseProvider, Message, ToolCall
from .openai_compat import OpenAICompatProvider, OpenRouterProvider

__all__ = [
    "AgentAborted",
    "AssistantMessage",
    "BaseProvider",
    "Message",
    "ToolCall",
    "OpenAICompatProvider",
    "OpenRouterProvider",
]
