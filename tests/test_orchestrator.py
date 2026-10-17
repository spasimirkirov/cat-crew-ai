"""platform/orchestrator.py: Cat Crew identity + the intent router (pure, no LLM)."""

from __future__ import annotations

import pytest

from backend.platform import handoff
from backend.platform.orchestrator import (
    BUG,
    BUG_REASON,
    CHAT,
    MENTION,
    ROOM_ID,
    ROOM_NAME,
    WORK,
    canonical_room_id,
    classify_intent,
    is_room_id,
    without_room,
)


def test_room_ids_current_and_legacy():
    assert ROOM_ID == "crew" and ROOM_NAME == "Cat Crew"
    for legacy in ("crew", "chief", "CHIEF", "", None, " crew "):
        assert is_room_id(legacy)
        assert canonical_room_id(legacy) == "crew"
    assert not is_room_id("pulse")
    assert canonical_room_id("pulse") == "pulse"
    assert without_room(["chief", "pulse", "crew", "lex"]) == ["pulse", "lex"]


def test_domain_layer_literals_match():
    from backend.domain.chief import ChiefSpec as DomainSpec
    from backend.platform.models import ChiefSpec

    for spec in (DomainSpec(), ChiefSpec()):
        assert spec.id == ROOM_ID and spec.name == ROOM_NAME
        assert spec.as_bot().role == "orchestrator"
    assert DomainSpec().as_bot().description == ChiefSpec().as_bot().description
    legacy = ChiefSpec.from_dict({"id": "chief", "name": "Chief"})
    assert (legacy.id, legacy.name) == ("crew", "Cat Crew")
    assert ChiefSpec.from_dict({"name": "Maestro"}).name == "Maestro"  # a custom name survives
    assert ChiefSpec(name="Project Room").name == "Cat Crew"  # the pre-rename display name


@pytest.mark.parametrize(
    "text",
    [
        "hi",
        "thanks!",
        "Здравей",
        "what is the status of the login page?",
        "explain how to build an app",
        "how do I fix the login bug?",
        "Как върви проектът?",
        "I want to think about the architecture options",
        "",
    ],
)
def test_conversation_stays_a_chat(text):
    assert classify_intent(text).kind == CHAT


@pytest.mark.parametrize(
    "text, roles",
    [
        ("Build a todo app with React and a FastAPI backend", None),
        ("Please write a privacy policy for the app", ("legal",)),
        ("fix the bug in the login endpoint", ("developer",)),
        ("can you build a landing page?", ("developer", "designer")),
        ("set up docker for deploy", ("devops",)),
        ("write tests for the parser module", ("qa",)),
        ("Направи ми страница за вход с дизайн", ("developer", "designer")),
    ],
)
def test_work_is_routed_with_the_right_specialists(text, roles):
    intent = classify_intent(text)
    assert intent.kind == WORK, intent
    assert intent.roles == roles


@pytest.mark.parametrize(
    "text",
    [
        "Why is the navigation still a full-screen size capsule, that is not a modern website design",
        "the header looks wrong on mobile",
        "The button is too big",
        "this is not a good landing page",
        "the menu is still broken",
        "Навигацията е гадна и е дълга капсула",
        "защо менюто още е прекалено голямо",
    ],
)
def test_a_complaint_about_finished_work_is_a_problem_report(text):
    intent = classify_intent(text)
    assert intent.kind == BUG and intent.reason == BUG_REASON, intent


@pytest.mark.parametrize(
    "text",
    [
        "how do I fix the broken build?",
        "Make the navigation bar smaller, it is too big",
        "please fix the header, it looks wrong",
        "what is still running?",
        "why does the login endpoint return 401?",
        "is the deploy still going?",
    ],
)
def test_a_complaint_shaped_question_or_order_keeps_its_normal_route(text):
    assert classify_intent(text).kind != BUG


def test_mention_wins():
    assert classify_intent("@pulse fix it", has_mention=True).kind == MENTION
    assert classify_intent("hi", has_mention=True).kind == MENTION


def test_handoff_slot_is_per_project_and_consumed_once():
    handoff.request("p1", "Build X", ("developer",))
    handoff.request("p2", "Build Y")
    assert handoff.take("p1").roles == ("developer",)
    assert handoff.take("p1") is None
    assert handoff.take("p2").brief == "Build Y"
    handoff.request("p1", "   ")  # blank briefs are ignored
    assert handoff.take("p1") is None


def test_orchestrator_has_no_per_agent_db_rows(isolated):
    """Cat Crew (any legacy id) only ever sees global skills/tools; assigning is a no-op."""
    from backend.db import ledger

    ledger.init_db() if hasattr(ledger, "init_db") else ledger._connect().close()
    sid = ledger.upsert_skill(slug="s1", name="s1", description="d", playbook_path="", type="custom", version="1")
    ledger.assign_skill("chief", sid)
    ledger.assign_skill("crew", sid)
    assert ledger.agent_ids_for_skill(sid) == set()
    assert ledger.skills_for_agent("chief") == ledger.skills_for_agent("crew") == []
    ledger.set_skill_global(sid, True)
    assert [s["name"] for s in ledger.skills_for_agent("chief")] == ["s1"]
    assert ledger.tools_for_agent("chief") == ledger.tools_for_agent("crew")
