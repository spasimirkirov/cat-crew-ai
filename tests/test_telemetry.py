"""Live telemetry: the usage meter, context events, balances, the Gemini meter, GPU stats and the endpoint."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.chat_service import ChatService
from backend.platform import bus
from backend.platform.runtime import reset_project_id, set_project_id
from backend.providers.base import AssistantMessage, BaseProvider
from backend.providers.openai_compat import OpenAICompatProvider
from backend.services import balances, hardware, telemetry, usage_meter
from backend.services.usage_meter import UsageMeter

BASE = "http://127.0.0.1:8765"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh_meter():
    usage_meter.reset_meter()
    balances.clear_cache()
    yield
    usage_meter.reset_meter()
    balances.clear_cache()


@pytest.fixture
def client(isolated):
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url=BASE) as c:
        yield c


# -- the meter ------------------------------------------------------------------------------------


def test_level_thresholds():
    assert usage_meter.level_for(0) == "ok"
    assert usage_meter.level_for(59) == "ok"
    assert usage_meter.level_for(60) == "warn"
    assert usage_meter.level_for(85) == "warn"
    assert usage_meter.level_for(86) == "danger"
    assert usage_meter.level_for(140) == "danger"


def test_windows_count_requests_and_tokens_per_host_and_model():
    clock = Clock()
    m = UsageMeter(clock)
    m.record_call("gemini", "gemini-2.0-flash", prompt=1_000, completion=200)
    clock.now += 30
    m.record_call("gemini", "gemini-2.0-flash", prompt=500, completion=100)
    m.record_call("gemini", "gemini-2.5-pro", prompt=10, completion=10)
    m.record_call("deepseek", "deepseek-chat", prompt=99, completion=1)
    assert m.window("gemini", 60) == (3, 1_820)
    assert m.window("gemini", 60, model="gemini-2.0-flash") == (2, 1_800)
    clock.now += 45  # the first call is 75 s old now: out of the minute, still in the day
    assert m.window("gemini", 60, model="gemini-2.0-flash") == (1, 600)
    assert m.window("gemini", 86_400, model="gemini-2.0-flash") == (2, 1_800)
    assert m.window("deepseek", 60) == (1, 100)
    assert m.window("nobody", 60) == (0, 0)
    assert m.last_model("gemini") == "gemini-2.5-pro"


def test_speed_needs_a_real_generation_and_ages_out():
    clock = Clock()
    m = UsageMeter(clock)
    m.record_call("lmstudio", "qwen", prompt=100, completion=3, seconds=0.01)  # too small to be a speed
    assert m.speed("lmstudio") is None
    m.record_call("lmstudio", "qwen", prompt=100, completion=144, seconds=2.0)
    tps, at = m.speed("lmstudio")
    assert tps == pytest.approx(72.0) and at == clock.now
    clock.now += 700
    assert m.speed("lmstudio") is None


def test_errors_count_as_requests_and_clear_on_success():
    clock = Clock()
    m = UsageMeter(clock)
    m.record_error("gemini", "gemini-2.0-flash", 429, "quota")
    assert m.window("gemini", 60) == (1, 0)
    assert m.last_error("gemini")["status"] == 429
    m.record_call("gemini", "gemini-2.0-flash", prompt=1, completion=1)
    assert m.last_error("gemini") is None


def test_context_falls_back_to_the_projectless_run_and_can_be_forgotten():
    m = UsageMeter()
    assert m.context("p1") is None
    m.set_context("", {"tokens": 5})
    assert m.context("p1")["tokens"] == 5  # a run that had no project id is still the best answer
    m.set_context("p1", {"tokens": 9})
    assert m.context("p1")["tokens"] == 9
    m.forget_project("p1")
    assert m.context("p1")["tokens"] == 5


class Echo(BaseProvider):
    display_name = "Echo"
    host_id = "lmstudio"
    model = "qwen-test"

    def chat(self, messages, tools=None):
        return AssistantMessage(content="done", tool_calls=[], raw={"role": "assistant", "content": "done"})

    def tool_message(self, tool_call, result):
        return {"role": "tool", "content": result}


def test_report_context_counts_the_whole_conversation_against_the_window_and_announces_it(recorder):
    messages = [
        {"role": "system", "content": "s" * 4_000},  # ~1000 tokens: they fill the window like any other
        {"role": "user", "content": "u" * 2_000},  # ~500
        {"role": "assistant", "content": "a" * 2_000},  # ~500
    ]
    row = usage_meter.report_context(Echo(), messages, 4_000, project_id="p1", threshold=3_400)
    assert row["tokens"] == 2_000 and row["system_tokens"] == 1_000
    assert row["limit"] == 4_000 and row["threshold"] == 3_400 and row["percent"] == 50 and row["level"] == "ok"
    assert row["host"] == "lmstudio" and row["model"] == "qwen-test"
    [event] = recorder.of(bus.CONTEXT_USAGE)
    assert event.project_id == "p1" and event.data["tokens"] == 2_000
    assert usage_meter.get_meter().context("p1")["percent"] == 50
    danger = usage_meter.report_context(Echo(), messages + [{"role": "user", "content": "x" * 6_000}], 4_000, project_id="p1")
    assert danger["level"] == "danger" and danger["percent"] > 85
    assert danger["threshold"] == 3_400  # not given: 85 % of the window


def test_run_agent_reports_the_context_of_the_current_project(recorder):
    token = set_project_id("proj-9")
    try:
        text = Echo().run_agent([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}], [], lambda n, a: "")
    finally:
        reset_project_id(token)
    assert text == "done"
    [event] = recorder.of(bus.CONTEXT_USAGE)
    assert event.project_id == "proj-9" and event.data["compressed"] is False
    assert event.data["limit"] > 0 and event.data["tokens"] >= 1


# -- providers feed the meter ------------------------------------------------------------------------


class FakeStream:
    status_code = 200
    content = b""

    def __init__(self, lines):
        self._lines = lines
        self.encoding = "utf-8"

    def iter_lines(self, decode_unicode=False):
        yield from (line.encode("utf-8") for line in self._lines)

    def close(self):
        pass


def _sse(*chunks):
    return [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]


def test_streaming_records_the_hosts_own_token_counts(monkeypatch):
    seen = {}

    def post(url, json=None, **kwargs):
        seen["payload"] = json
        return FakeStream(
            _sse(
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 1_200, "completion_tokens": 40}},
            )
        )

    monkeypatch.setattr("backend.providers.openai_compat.requests.post", post)
    p = OpenAICompatProvider(base_url="https://x.test/v1", api_key="k", model="gemini-2.0-flash", host_id="gemini", stream_usage=True)
    reply = p.stream_chat([{"role": "user", "content": "hi"}])
    assert reply.content == "Hello"
    assert seen["payload"]["stream_options"] == {"include_usage": True}
    assert p.last_usage == {"prompt_tokens": 1_200, "completion_tokens": 40}
    assert usage_meter.get_meter().window("gemini", 60, model="gemini-2.0-flash") == (1, 1_240)


def test_a_host_without_usage_gets_an_estimate_and_no_stream_option(monkeypatch):
    seen = {}

    def post(url, json=None, **kwargs):
        seen["payload"] = json
        return FakeStream(_sse({"choices": [{"delta": {"content": "x" * 400}}]}))

    monkeypatch.setattr("backend.providers.openai_compat.requests.post", post)
    p = OpenAICompatProvider(base_url="http://127.0.0.1:1234/v1", model="qwen", host_id="lmstudio")
    p.stream_chat([{"role": "user", "content": "y" * 800}])
    assert "stream_options" not in seen["payload"]
    assert p.last_usage is None
    assert usage_meter.get_meter().window("lmstudio", 60) == (1, 300)  # 800 chars + 400 chars, four to a token


def test_a_host_that_rejects_stream_options_is_retried_without_it(monkeypatch):
    payloads = []

    class Refusal:
        status_code = 400
        content = b'{"error": {"message": "Unknown parameter: stream_options"}}'
        encoding = "utf-8"

        def json(self):
            return {"error": {"message": "Unknown parameter: stream_options"}}

        def close(self):
            pass

    def post(url, json=None, **kwargs):
        payloads.append(json)
        return Refusal() if "stream_options" in json else FakeStream(_sse({"choices": [{"delta": {"content": "ok"}}]}))

    monkeypatch.setattr("backend.providers.openai_compat.requests.post", post)
    p = OpenAICompatProvider(base_url="https://x.test/v1", api_key="k", model="m", host_id="groq", stream_usage=True)
    assert p.stream_chat([{"role": "user", "content": "hi"}]).content == "ok"
    assert len(payloads) == 2 and "stream_options" not in payloads[1]
    assert p.stream_usage is False
    assert usage_meter.get_meter().last_error("groq") is None or usage_meter.get_meter().last_error("groq")["status"] != 400


def test_http_errors_are_metered_and_a_429_is_visible(monkeypatch):
    class TooMany:
        status_code = 429
        content = b'{"error": {"message": "quota"}}'
        encoding = "utf-8"

        def json(self):
            return {"error": {"message": "quota"}}

        def close(self):
            pass

    monkeypatch.setattr("backend.providers.openai_compat.requests.post", lambda *a, **k: TooMany())
    monkeypatch.setattr("backend.providers.openai_compat._sleep_unless", lambda abort, seconds: False)
    p = OpenAICompatProvider(base_url="https://x.test/v1", api_key="k", model="gemini-2.0-flash", host_id="gemini")
    with pytest.raises(RuntimeError):
        p.stream_chat([{"role": "user", "content": "hi"}])
    # A rate limit gets one short retry on a paid model (none on a :free one); both requests are metered.
    assert usage_meter.get_meter().window("gemini", 60) == (2, 0)
    assert usage_meter.get_meter().last_error("gemini")["status"] == 429


def test_a_passing_host_hiccup_is_retried_and_the_turn_goes_on(monkeypatch):
    class Reply:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.content = b'{"error": {"message": "overloaded"}}'
            self.encoding = "utf-8"
            self.headers = {"retry-after": "0"}

        def json(self):
            if self.status_code >= 400:
                return {"error": {"message": "overloaded"}}
            return {"choices": [{"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}]}

        def close(self):
            pass

    statuses = [503, 200]
    monkeypatch.setattr("backend.providers.openai_compat.requests.post", lambda *a, **k: Reply(statuses.pop(0)))
    monkeypatch.setattr("backend.providers.openai_compat._sleep_unless", lambda abort, seconds: False)
    p = OpenAICompatProvider(base_url="https://x.test/v1", api_key="k", model="m", host_id="gemini")
    assert p.chat([{"role": "user", "content": "hi"}]).content == "hello"
    assert not statuses


# -- balances ----------------------------------------------------------------------------------------


def test_openrouter_credit_is_total_minus_usage():
    out = balances.parse_openrouter({"data": {"total_credits": 10.0, "total_usage": 2.55}})
    assert out["balance"] == 7.45 and out["display"] == "$7.45 remaining" and out["status"] == "ok"
    assert balances.parse_openrouter({"data": {"total_credits": 1, "total_usage": 1}})["status"] == "empty"
    assert balances.parse_openrouter([])["status"] == "error"
    assert balances.parse_openrouter({})["status"] == "error"
    assert balances.parse_openrouter({"data": {"total_usage": 1}})["status"] == "error"  # no total_credits


@pytest.fixture
def openrouter_key(monkeypatch):
    balances.clear_cache()
    monkeypatch.setattr(balances, "_key", lambda host: "sk-secret-value" if host == "openrouter" else "")
    yield
    balances.clear_cache()


def test_balance_is_cached_and_refreshed_once_marked_stale(monkeypatch, openrouter_key):
    calls = []
    clock = {"t": 100.0}
    monkeypatch.setattr(balances.time, "monotonic", lambda: clock["t"])

    def get(url, key):
        calls.append(url)
        return {"data": {"total_credits": 10.0, "total_usage": float(len(calls))}}

    monkeypatch.setattr(balances, "_get", get)
    assert balances.get_balance("openrouter")["balance"] == 9.0
    clock["t"] += 5
    assert balances.get_balance("openrouter")["balance"] == 9.0  # cached
    balances.mark_stale("openrouter")
    assert balances.get_balance("openrouter")["balance"] == 9.0  # stale, but read only 5 s ago: too soon
    clock["t"] += 6
    assert balances.get_balance("openrouter")["balance"] == 8.0  # spent since, and >= 10 s: re-read
    clock["t"] += 61
    assert balances.get_balance("openrouter")["balance"] == 7.0  # plain expiry
    assert len(calls) == 3 and calls[0].endswith("/credits")


def test_balance_failures_never_leak_the_key(monkeypatch, openrouter_key):
    def rejected(url, key):
        raise balances._Http(401)

    monkeypatch.setattr(balances, "_get", rejected)
    out = balances.get_balance("openrouter", force=True)
    assert out["status"] == "error" and "rejected" in out["message"]

    def boom(url, key):
        raise RuntimeError(f"connection to {url} with Bearer {key} failed")

    monkeypatch.setattr(balances, "_get", boom)
    out = balances.get_balance("openrouter", force=True)
    assert out["status"] == "error" and "sk-secret-value" not in json.dumps(out)


def test_no_key_means_not_configured(monkeypatch):
    balances.clear_cache()
    monkeypatch.setattr(balances, "_key", lambda host: "")
    monkeypatch.setattr(balances, "_get", lambda *a: pytest.fail("no key: nothing may be requested"))
    assert balances.get_balance("openrouter") == {"host": "openrouter", "configured": False, "status": "no_key"}
    assert balances.get_balance("mystery")["status"] == "unsupported"


# -- cache savings, the Jev gatekeeper, gpu and the snapshot -------------------------------------------


def test_cache_savings_sums_only_that_hosts_ok_calls(monkeypatch):
    from backend.services import model_discovery

    monkeypatch.setattr(model_discovery, "peek_snapshot", lambda kind: None)  # no catalog cached: isolate from other tests' state
    meter = usage_meter.get_meter()
    meter.record_call("openrouter", "deepseek/deepseek-v4-pro", prompt=1_000, completion=100, cached_prompt=800)
    meter.record_call("openrouter", "deepseek/deepseek-v4-pro", prompt=500, completion=50, cached_prompt=0)
    meter.record_call("lmstudio", "qwen", prompt=1_000, completion=100, cached_prompt=1_000)  # a different host: ignored
    meter.record_error("openrouter", "deepseek/deepseek-v4-pro", 429, "quota")  # not ok: ignored
    out = meter.cache_savings("openrouter")
    assert out["calls"] == 2 and out["prompt_tokens"] == 1_500 and out["cached_tokens"] == 800
    assert out["cache_hit_pct"] == 53  # round(100 * 800 / 1500)
    assert out["estimated_pct_spend_saved"] == round(53 * usage_meter.CACHED_TOKEN_DISCOUNT)
    assert out["estimated_usd_saved"] is None  # no catalog snapshot cached: an honest unknown, not a guess


def test_cache_savings_prices_the_dollar_figure_from_the_catalog_snapshot(monkeypatch):
    from backend.services import model_discovery
    from backend.services.lm_runtime import HostSnapshot, LmModel

    monkeypatch.setattr(
        model_discovery,
        "peek_snapshot",
        lambda kind: HostSnapshot(kind="openrouter", online=True, models=[LmModel(name="deepseek/deepseek-v4-pro", price_prompt_per_1m=0.9)]),
    )
    meter = usage_meter.get_meter()
    meter.record_call("openrouter", "deepseek/deepseek-v4-pro", prompt=1_000, completion=100, cached_prompt=800)
    out = meter.cache_savings("openrouter")
    # 800 cached tokens * $0.9/1M * 0.9 discount
    assert out["estimated_usd_saved"] == round(800 / 1_000_000 * 0.9 * usage_meter.CACHED_TOKEN_DISCOUNT, 4)


def test_jev_reports_its_settings_and_metrics(isolated, monkeypatch):
    from backend.services import jev_metrics, settings_store
    from backend.services.jev_client import Decision

    jev_metrics.clear()
    settings_store.save_jev_settings({"enabled": True, "confidence_threshold": 0.8})
    decision = Decision(id="bypass", type="noul", value=True, confidence=0.97, latency_ms=12.0)
    jev_metrics.record_precheck(decision, bypassed=True, tokens=500)
    out = telemetry._jev()
    assert out["enabled"] is True and out["confidence_threshold"] == 0.8
    assert out["model"] == "typesafe/jev-latest"
    assert "api_key" not in out
    assert out["precheck_calls"] == 1 and out["bypasses"] == 1 and out["interceptions"] == 1
    assert out["tokens_saved_estimate"] == 500 and out["avg_decision_latency_ms"] == 12.0
    jev_metrics.clear()


def test_gpu_prefers_nvml_and_falls_back_to_nvidia_smi(monkeypatch):
    hardware.reset_gpu_cache()
    small = {"name": "GTX", "vram_used_gb": 1.0, "vram_total_gb": 4.0, "utilization": 3.0, "temperature_c": 40.0}
    big = {"name": "RTX 4080", "vram_used_gb": 8.9, "vram_total_gb": 16.0, "utilization": 71.0, "temperature_c": 63.0}
    monkeypatch.setattr(hardware, "_nvml_gpus", lambda: [small, big])
    monkeypatch.setattr(hardware, "_smi_gpus", lambda: pytest.fail("NVML answered: nvidia-smi must not be spawned"))
    stats = hardware.gpu_stats(ttl=0)
    assert stats["available"] and stats["source"] == "nvml" and stats["count"] == 2
    assert stats["name"] == "RTX 4080" and stats["vram_used_gb"] == 8.9 and stats["vram_total_gb"] == 16.0

    monkeypatch.setattr(hardware, "_nvml_gpus", lambda: [])
    monkeypatch.setattr(hardware, "_smi_gpus", lambda: [big])
    assert hardware.gpu_stats(ttl=0)["source"] == "nvidia-smi"

    monkeypatch.setattr(hardware, "_smi_gpus", lambda: [])
    none = hardware.gpu_stats(ttl=0)
    assert none["available"] is False and none["vram_total_gb"] == 0.0
    hardware.reset_gpu_cache()


def test_nvidia_smi_output_is_parsed(monkeypatch):
    monkeypatch.setattr(hardware, "_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(hardware, "_run", lambda cmd, timeout=4.0: "NVIDIA GeForce RTX 4080, 9113, 16376, 71, 63\nbroken line")
    [gpu] = hardware._smi_gpus()
    assert gpu["name"] == "NVIDIA GeForce RTX 4080" and gpu["vram_used_gb"] == 8.9 and gpu["vram_total_gb"] == 16.0
    assert gpu["utilization"] == 71.0 and gpu["temperature_c"] == 63.0


def test_the_gpu_section_has_no_local_generation_speed(monkeypatch):
    """No local model host any more: the GPU card shows the card (ComfyUI uses it), not a token rate."""
    hardware.reset_gpu_cache()
    monkeypatch.setattr(hardware, "_nvml_gpus", lambda: [{"name": "RTX", "vram_used_gb": 8.9, "vram_total_gb": 16.0, "utilization": 50.0, "temperature_c": 60.0}])
    out = telemetry._gpu()
    assert out["gen_speed_tps"] is None and out["vram_used_gb"] == 8.9
    hardware.reset_gpu_cache()


# -- the endpoint --------------------------------------------------------------------------------------


def _project(client, tmp_path, name="Alpha"):
    r = client.post("/api/projects", json={"name": name, "folders": [str(tmp_path / name)]})
    assert r.status_code == 201, r.text
    return r.json()


def test_endpoint_shape_and_unknown_project(client, tmp_path, monkeypatch):
    monkeypatch.setattr(balances, "_key", lambda host: "")
    hardware.reset_gpu_cache()
    monkeypatch.setattr(hardware, "_nvml_gpus", lambda: [])
    monkeypatch.setattr(hardware, "_smi_gpus", lambda: [])
    body = client.get("/api/telemetry/live").json()
    assert set(body) >= {"project_id", "updated_at", "context", "active", "context_limit", "context_threshold", "openrouter", "openrouter_cache", "jev", "gpu"}
    assert body["context_limit"] > body["context_threshold"] > 0
    assert body["context"] is None and body["gpu"]["available"] is False
    assert body["openrouter"]["configured"] is False
    assert body["jev"]["enabled"] is False and body["jev"]["model"] == "typesafe/jev-latest"
    assert "api_key" not in body["jev"]
    assert client.get("/api/telemetry/live", params={"project_id": "nope"}).status_code == 404

    project = _project(client, tmp_path)
    usage_meter.report_context(Echo(), [{"role": "user", "content": "u" * 4_000}], 2_000, project_id=project["id"])
    body = client.get("/api/telemetry/live", params={"project_id": project["id"]}).json()
    assert body["context"]["tokens"] == 1_000 and body["context"]["percent"] == 50 and body["context"]["level"] == "ok"
    assert body["context"]["updated_at"].endswith("+00:00")
    assert body["active"]["role"] == "coding" and body["project_id"] == project["id"]


def test_endpoint_never_returns_a_key(client, monkeypatch):
    monkeypatch.setattr(balances, "_key", lambda host: "sk-very-secret" if host == "openrouter" else "")
    monkeypatch.setattr(balances, "_get", lambda url, key: {"data": {"total_credits": 12.45, "total_usage": 0.0}})
    balances.clear_cache()
    body = client.get("/api/telemetry/live")
    assert "sk-very-secret" not in body.text
    assert body.json()["openrouter"]["display"] == "$12.45 remaining"
    balances.clear_cache()


def test_one_failing_section_leaves_the_rest(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("driver crashed")

    monkeypatch.setattr(telemetry, "_gpu", boom)
    body = client.get("/api/telemetry/live").json()
    assert body["gpu"] == {"configured": False, "status": "error"}
    assert "openrouter" in body and "active" in body


def test_deleting_a_project_forgets_its_gauge(client, tmp_path):
    project = _project(client, tmp_path)
    usage_meter.report_context(Echo(), [{"role": "user", "content": "u" * 400}], 2_000, project_id=project["id"])
    assert usage_meter.get_meter().context(project["id"]) is not None
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert usage_meter.get_meter().context(project["id"]) is None


# -- live over the project WebSocket --------------------------------------------------------------------


def _connect(client, path):
    return client.websocket_connect(path, headers={"host": "127.0.0.1:8765"})


def test_context_usage_is_pushed_to_the_projects_own_socket_only(client, tmp_path):
    a, b = _project(client, tmp_path, "A"), _project(client, tmp_path, "B")
    with _connect(client, f"/ws/events?project_id={a['id']}") as ws:
        assert ws.receive_json()["type"] == "hello"
        usage_meter.report_context(Echo(), [{"role": "user", "content": "u" * 400}], 2_000, project_id=b["id"])  # not ours
        usage_meter.report_context(Echo(), [{"role": "user", "content": "u" * 4_000}], 2_000, project_id=a["id"])
        frame = ws.receive_json()
    assert frame["type"] == "context.usage" and frame["project_id"] == a["id"]
    assert frame["data"]["tokens"] == 1_000 and frame["data"]["percent"] == 50 and frame["data"]["level"] == "ok"
    assert frame["data"]["limit"] == 2_000 and frame["data"]["compressed"] is False
