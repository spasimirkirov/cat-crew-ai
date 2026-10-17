"""Context management in one place: the model's window, and the budget that keeps a conversation inside it.

* :func:`get_model_context_limit` (``services/provider_limits``): ``{max_tokens, compression_threshold}`` for a
  ``(host, model)`` pair: DeepSeek 64k, Gemini 1M, Claude 200k, OpenAI 128k, Mistral and xAI 128k.
* :class:`ContextBudget` (``services/compaction``): summarizes the older turns once the conversation reaches the
  threshold (about 85 % of the window), and never before.

``BaseProvider.run_agent`` uses the budget on every round with the provider that is about to be called, so a run
that swaps model mid-way is measured against the new model's window.
"""

from __future__ import annotations

from backend.services.compaction import ContextBudget, estimate_tokens
from backend.services.provider_limits import (
    THRESHOLD_RATIO,
    get_model_context_limit,
    limits_for_provider,
    threshold_for,
)

__all__ = [
    "ContextBudget",
    "THRESHOLD_RATIO",
    "estimate_tokens",
    "get_model_context_limit",
    "limits_for_provider",
    "threshold_for",
]
