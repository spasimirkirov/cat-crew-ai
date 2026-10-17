"""The "Cat Crew" orchestrator: identity constants and the intent router.

Cat Crew is the unconstrained Orchestrator / System Chat. It is **not** an
agent in the registry and has no row in ``agent_tools`` / ``agent_skills``:
skills and tools are assigned to specialist cats only, and the orchestrator
uses the engine's built-in routing (this module + ``platform/sop.py``).

Nothing here touches the database, the registry or the network, so the intent
router is trivially unit-testable (``tests/test_orchestrator.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Wire id of the Cat Crew thread / sidebar card. Older sessions and ledger rows
# used "chief" (or an empty bot id); those are read as the same thing.
ROOM_ID = "crew"
LEGACY_ROOM_IDS = frozenset({"", "chief"})
ROOM_NAME = "Cat Crew"
ROOM_TITLE = "Orchestrator"
ROOM_DESCRIPTION = (
    "Orchestrator & system chat. Talk to the primary model freely: steer progress, "
    "analyse requirements, and let it hand the work to the right specialist cats."
)
ROOM_AVATAR_FILE = "default.png"


def is_room_id(value: str | None) -> bool:
    """True for the Cat Crew id, current or legacy — never a real agent."""
    key = str(value or "").strip().lower()
    return key == ROOM_ID or key in LEGACY_ROOM_IDS


def canonical_room_id(value: str | None) -> str:
    """``value`` with the legacy room ids folded into :data:`ROOM_ID`."""
    return ROOM_ID if is_room_id(value) else str(value).strip()


def without_room(ids: Iterable[str]) -> list[str]:
    """``ids`` minus the orchestrator (it is never an assignable agent)."""
    return [i for i in ids if not is_room_id(i)]


# --------------------------------------------------------------------------- #
# Intent router
# --------------------------------------------------------------------------- #

CHAT = "chat"        # free conversation with the primary model
WORK = "work"        # delegate to specialist cats (whole pipeline or a subset)
MENTION = "mention"  # the user named a cat with @name
BUG = "bug"          # a problem report: the lead plans a fix, the cats fix it, QA verifies
BUG_REASON = "problem_report"
HANDOFF_REASON = "hand_off_to_crew"  # Intent.reason of work Cat Crew's own chat turn asked for


@dataclass(frozen=True)
class Intent:
    kind: str
    # For WORK: the roles to run, or None for the whole SOP pipeline.
    roles: tuple[str, ...] | None = None
    reason: str = ""
    # For BUG: the cat the user was talking to when they reported it ("" = reported in Cat Crew).
    reporter: str = ""


_WORD = re.compile(r"[\w#+.\-/]+", re.UNICODE)

# Request verbs (English + Bulgarian stems; a needle matches a word *prefix*).
_ACTION = (
    "build", "create", "make", "implement", "write", "add", "fix", "refactor", "deploy",
    "design", "develop", "generate", "set up", "setup", "install", "configure", "migrate",
    "test", "review", "audit", "draft", "ship", "launch", "scaffold", "integrate", "update",
    "optimi", "debug", "wire", "port", "convert", "prepare", "plan",
    "направ", "създа", "имплемент", "добав", "поправ", "напиш", "разработ", "изгра",
    "тествай", "деплой", "дизайн", "генерир", "настро", "инсталир", "мигрир", "прегле",
    "подготв", "оптимизир", "интегрир", "обнов", "рефакт", "пусни", "планир",
)
_WORK_NOUNS = (
    "app", "application", "website", "site", "page", "feature", "api", "endpoint", "bug",
    "component", "database", "schema", "ui", "ux", "screen", "landing", "script", "service",
    "module", "tests", "pipeline", "docker", "backend", "frontend", "login", "dashboard",
    "приложени", "сайт", "страниц", "функционал", "компонент", "база", "екран", "скрипт",
    "услуга", "модул", "тест", "бекенд", "фронтенд", "табло", "проект",
)
_QUESTION_START = (
    "what", "why", "how", "when", "where", "who", "which", "is", "are", "does", "did",
    "explain", "tell me", "describe", "summarize", "summarise", "show me", "status",
    "any update", "can you explain", "could you explain",
    "какво", "защо", "как", "кога", "къде", "кой", "коя", "кое", "кои", "обясни", "разкажи",
    "опиши", "покажи", "статус",
)
_GREETING = (
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "cool", "great", "good morning",
    "привет", "здравей", "здрасти", "благодаря", "ок", "добре", "супер", "чао",
)
_REQUEST_LEAD = (
    "please", "can you", "could you", "would you", "let's", "lets", "i want", "i need",
    "we need", "need to", "go ahead", "start",
    "моля", "можеш ли", "искам", "трябва", "нека", "започни", "хайде",
)
_POLITE_QUESTION = ("can you", "could you", "would you", "можеш ли")
_CREATE = ("build", "create", "make", "develop", "scaffold", "generate", "направ", "създа", "разработ", "изгра")
# Roles that advise rather than build: they win over "the whole app" wording.
_ADVISORY = frozenset({"legal", "accountant", "marketing", "prompt_engineer"})
# "The whole thing" — never narrowed to a single specialist.
_WHOLE = (
    "app", "application", "project", "product", "platform", "system", "from scratch",
    "end-to-end", "end to end", "whole", "entire", "full", "mvp", "website",
    "приложени", "проект", "продукт", "платформа", "система", "от нулата", "цял", "сайт",
)

# role -> keywords that call for that specialist on their own (word prefixes).
_ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prompt_engineer", ("system prompt", "prompt engineer", "rewrite the prompt", "improve the prompt", "промпт")),
    ("vanguard", ("architecture", "architect", "roadmap", "tech stack", "file tree", "архитектур")),
    ("devops", ("deploy", "docker", "ci/cd", "pipeline", "kubernetes", "release", "server setup",
                "infrastructure", "деплой", "докер", "инфраструктур")),
    ("developer", ("implement", "refactor", "function", "endpoint", "bug", "script", "code",
                   "fix", "имплемент", "рефакт", "функция", "бъг", "код", "поправ")),
    ("designer", ("ui", "ux", "css", "layout", "mockup", "landing", "styling", "style", "design",
                  "дизайн", "стил", "интерфейс", "макет")),
    ("qa", ("test", "qa", "regression", "verify", "smoke", "тест", "провери", "регресия")),
    ("legal", ("legal", "contract", "terms of service", "privacy policy", "gdpr", "compliance",
               "license", "trademark", "copyright", "договор", "поверителност", "лиценз")),
    ("accountant", ("budget", "pricing", "invoice", "cost", "revenue", "бюджет", "фактура", "цена")),
    ("marketing", ("marketing", "seo", "campaign", "social media", "launch copy", "newsletter",
                   "маркетинг", "кампания", "реклама")),
)


# A complaint about something the cats already made ("why is the nav still a huge capsule", "that is
# not a modern design", "the button is too big"): a problem report, not a question to chat about.
_COMPLAINT = re.compile(
    r"""
      \bnot\s+(?:a\s+|an\s+)?(?:good|great|modern|right|correct|working|nice|acceptable|professional)\b
    | \b(?:doesn'?t|does\s+not|isn'?t|is\s+not|aren'?t|are\s+not|won'?t)\s+(?:work|working|show|showing|load|loading|look|looking|fit|fitting|display|displaying)\b
    | \b(?:is|are|looks?|looking)\s+(?:still\s+)?(?:broken|ugly|bad|wrong|terrible|awful|horrible|weird|messy|clunky|cut\s+off)\b
    | \btoo\s+(?:big|small|long|wide|narrow|tall|short|large|dark|bright|slow|thick|thin|close|far)\b
    | \bwhy\b.{0,80}\bstill\b
    | \bstill\s+(?:too|not|broken|wrong|ugly|bad|the\s+same)\b
    | гадн|грозн|ужасн|счупен|не\s+работи|не\s+е\s+(?:добър|добра|добро|модерн|както)|прекалено|твърде|още\s+(?:е|са|не)\b
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)


def is_complaint(message: str) -> bool:
    """True for a remark that something already built is wrong. A how-to question ("how do I fix
    the broken build?") and an order that starts with a verb ("make the nav smaller, it is too
    big") are not: they keep their normal route."""
    text = (message or "").strip()
    if not text or not _COMPLAINT.search(text):
        return False
    norm = _norm(text)
    first = norm.split()[0] if norm.split() else ""
    if first == "how" or first.startswith("как"):
        return False
    if _has(" " + first + " ", _ACTION) or _starts(norm, _REQUEST_LEAD):
        return False
    return True


def _norm(text: str) -> str:
    """Lower-cased words joined by single spaces, padded so ``" word"`` finds prefixes."""
    return " " + " ".join(t.lower() for t in _WORD.findall(text or "")) + " "


def _has(norm: str, needles: tuple[str, ...]) -> bool:
    """Word-prefix match: ``fix`` hits ``fixing``; ``ui`` does not hit ``guide``."""
    return any((" " + n) in norm for n in needles)


def _starts(norm: str, needles: tuple[str, ...]) -> bool:
    body = norm.strip()
    return any(body == n or body.startswith(n + " ") for n in needles)


def roles_for(message: str) -> tuple[str, ...] | None:
    """The specialist roles a work request calls for, or ``None`` for the whole
    SOP pipeline (a whole product/app, or nothing specific enough to narrow)."""
    norm = _norm(message)
    hits = [role for role, words in _ROLE_KEYWORDS if _has(norm, words)]
    creating = _has(norm, _CREATE)
    if hits and set(hits) <= _ADVISORY and not creating:
        return tuple(hits)  # "write a privacy policy for the app" -> Lex only
    if _has(norm, _WHOLE):
        return None
    if creating and "designer" in hits and "developer" not in hits:
        hits.insert(hits.index("designer"), "developer")  # a page needs building, then styling
    return tuple(hits) or None


def classify_intent(message: str, *, has_mention: bool = False) -> Intent:
    """Decide what a Cat Crew message is: a chat turn, a delegation, or an @mention.

    Deterministic and cheap on purpose (no LLM call): a request is *work* when it
    carries a request verb plus a deliverable-ish noun (or is clearly long and
    imperative); questions, greetings and short remarks stay a normal
    conversation with the primary model, which can always escalate on request.
    """
    if has_mention:
        return Intent(MENTION, reason="@mention")
    norm = _norm(message)
    n_words = len(norm.split())
    if n_words == 0:
        return Intent(CHAT, reason="empty")
    if n_words <= 3 and (_starts(norm, _GREETING) or "?" in (message or "")):
        return Intent(CHAT, reason="greeting/short")

    if is_complaint(message):
        return Intent(BUG, reason=BUG_REASON)

    work = chat = 0
    if _has(norm, _ACTION):
        work += 2
    if _has(norm, _WORK_NOUNS):
        work += 1
    if _starts(norm, _REQUEST_LEAD):
        work += 1
    if n_words >= 14:
        work += 1
    if "?" in (message or ""):
        chat += 1
    if _starts(norm, _QUESTION_START):
        chat += 2
    if _starts(norm, _GREETING):
        chat += 3
    if n_words <= 4:
        chat += 1
    # "can you build X?" is a request, not a question.
    if _starts(norm, _POLITE_QUESTION) and _has(norm, _ACTION):
        chat = max(0, chat - 2)

    # "explain how to build an app" is a question about work, not a work order.
    informational = _starts(norm, _QUESTION_START) and not _starts(norm, _REQUEST_LEAD)
    if work >= 2 and work > chat and not informational:
        return Intent(WORK, roles=roles_for(message), reason=f"work={work} chat={chat}")
    return Intent(CHAT, reason=f"work={work} chat={chat}")
