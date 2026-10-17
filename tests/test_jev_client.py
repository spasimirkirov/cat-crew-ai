"""backend/services/jev_client.py: the adapter to TypeSafe AI's System One model, reached through
OpenRouter's Decisions endpoint by default."""

from __future__ import annotations

import requests

from backend.services import jev_client as jc
from backend.services.jev_client import Decision, JevClient, Question


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def test_no_questions_is_a_no_op():
    client = JevClient(api_key="key")
    assert client.decide({"a": 1}, []) == []


def test_no_key_returns_a_fallback_decision_for_every_question():
    client = JevClient(api_key="")
    q = Question(id="q1", type="noul", instructions="?")
    [decision] = client.decide({}, [q])
    assert decision.ok is False and decision.value is False and decision.confidence == 0.0
    assert "key" in decision.reason


def test_request_shape_matches_the_documented_decisions_contract(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp({"model": "typesafe/jev-1.13", "answers": {"q1": {"type": "noul", "noul": 0.97}}, "usage": {"input_tokens": 10, "output_tokens": 2, "cost": 0.0001}})

    monkeypatch.setattr(jc.requests, "post", fake_post)
    client = JevClient(api_key="or-secret")  # default model/base_url: OpenRouter's Decisions endpoint
    q = Question(id="q1", type="noul", instructions="Is it safe?", criteria={"true": "yes", "false": "no"})
    [decision] = client.decide({"goal": "ship it"}, [q])

    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["headers"]["Authorization"] == "Bearer or-secret"
    assert captured["json"]["model"] == "typesafe/jev-latest"
    assert captured["json"]["state"] == {"goal": "ship it"}
    assert captured["json"]["questions"] == {"q1": {"type": "noul", "instructions": "Is it safe?", "criteria": {"true": "yes", "false": "no"}}}
    assert decision.ok is True and decision.type == "noul"
    # noul's returned probability doubles as the confidence-in-this-polarity.
    assert decision.value is True and decision.confidence == 0.97


def test_base_url_can_be_pointed_at_typesafes_own_direct_api(monkeypatch):
    """The escape hatch (CATCREW_JEV_URL / a custom base_url): a full endpoint URL, not a root --
    TypeSafe's own API and OpenRouter's Decisions endpoint use different path suffixes."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        return _Resp({"answers": {"q1": {"type": "noul", "noul": 0.9}}})

    monkeypatch.setattr(jc.requests, "post", fake_post)
    client = JevClient(api_key="ts-secret", model="jev-latest", base_url="https://api.typesafe.ai/v1/systemone")
    client.decide({}, [Question(id="q1", type="noul", instructions="?")])
    assert captured["url"] == "https://api.typesafe.ai/v1/systemone"


def test_choice_and_score_answers_are_coerced(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _Resp(
            {
                "answers": {
                    "route": {"type": "choice", "choice": "billing", "confidence": 0.81, "probabilities": {"billing": 0.81, "technical": 0.19}},
                    "anger": {"type": "score", "score": 1.4, "confidence": 0.6, "legend": {"0": "calm", "1": "annoyed", "2": "furious"}},
                }
            }
        )

    monkeypatch.setattr(jc.requests, "post", fake_post)
    client = JevClient(api_key="k")
    questions = [
        Question(id="route", type="choice", instructions="which team?", criteria={"billing": "...", "technical": "..."}),
        Question(id="anger", type="score", instructions="how upset?", criteria=["calm", "annoyed", "furious"]),
    ]
    route, anger = client.decide({}, questions)
    assert route.value == "billing" and route.confidence == 0.81 and route.probabilities["technical"] == 0.19
    assert anger.value == 1.4 and anger.confidence == 0.6


def test_network_failure_never_raises(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(jc.requests, "post", fake_post)
    client = JevClient(api_key="k")
    [decision] = client.decide({}, [Question(id="q", type="noul", instructions="?")])
    assert decision.ok is False and decision.confidence == 0.0
    assert "ConnectionError" in decision.reason


def test_a_missing_answer_falls_back_for_just_that_question(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _Resp({"answers": {}})

    monkeypatch.setattr(jc.requests, "post", fake_post)
    client = JevClient(api_key="k")
    [decision] = client.decide({}, [Question(id="q", type="score", instructions="?", default=0.5)])
    assert decision.ok is False and decision.value == 0.5


def test_error_status_becomes_a_fallback_not_an_exception(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _Resp({"error": "invalid key"}, status=401)

    monkeypatch.setattr(jc.requests, "post", fake_post)
    client = JevClient(api_key="bad-key")
    [decision] = client.decide({}, [Question(id="q", type="noul", instructions="?")])
    assert decision.ok is False
