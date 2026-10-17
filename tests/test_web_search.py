from backend.services.ddgs_search import parse_ddgs_images, parse_ddgs_text
from backend.services.here import locale_city, should_attach_forecast
from backend.tools import _clean_hit, _search_ddg_instant, looks_like_weather, weather_place


def test_clean_hit_skips_search_engine_hosts() -> None:
    assert _clean_hit("Spam", "https://duckduckgo.com/?q=hi", "hi", "hi") is None


def test_ddg_instant_parses_related_topics() -> None:
    class _Fake:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "Heading": "Example",
                "AbstractText": "An example page.",
                "AbstractURL": "https://example.com/page",
                "RelatedTopics": [
                    {"Text": "Example news", "FirstURL": "https://news.example.com/a"},
                    {"Topics": [{"Text": "Nested", "FirstURL": "https://example.com/n"}]},
                ],
                "Results": [],
            }

    import backend.tools as tools

    original = tools._http_get
    tools._http_get = lambda *a, **k: _Fake()  # type: ignore[assignment]
    try:
        hits = _search_ddg_instant("example")
    finally:
        tools._http_get = original
    blob = "\n".join(hits)
    assert "example.com/page" in blob
    assert "news.example.com" in blob


def test_weather_query_is_detected() -> None:
    assert looks_like_weather("Tell me the weather for tomorrow.")
    assert looks_like_weather("какво ще е времето утре")
    assert not looks_like_weather("git commit message")


def test_weather_place_keeps_city_and_drops_filler() -> None:
    assert weather_place("weather in Sofia tomorrow") == "Sofia"
    leftover = weather_place("Tell me the weather for tomorrow.")
    assert leftover == locale_city()


def test_yes_after_search_offer_counts_as_weather() -> None:
    history = [
        {
            "role": "assistant",
            "content": "I can help you find the latest weather forecast by searching the web.",
        }
    ]
    assert should_attach_forecast("Yes, I would like you to do that.", history)
    assert should_attach_forecast("Tell me the weather for tomorrow.", [])


def test_assistant_prompt_answers_facts_not_site_lists() -> None:
    from backend.tools import build_system_prompt

    from backend.platform.chief import turn_context

    text = build_system_prompt("assistant")
    assert "directory of websites" in text
    # The clock lives in the turn note, not the (cached) system prompt.
    assert "Now:" not in text
    note = turn_context()
    assert "Now:" in note and "Tomorrow is" in note
    assert "Never list weather websites" in note
    assert "image_search" in text
    assert "This is you (identity)" in text
    assert "source of truth" in text
    assert "read_skill" in text
    assert "comfy" in text
    assert "catcrew-self" in text


def test_developer_prompt_mentions_clarified_spec() -> None:
    from backend.tools import build_system_prompt

    text = build_system_prompt("developer")
    assert "clarified task spec" in text
    assert "This is you (identity)" in text
    assert "source of truth" in text


def test_ddgs_parses_text_and_images() -> None:
    hits = parse_ddgs_text(
        [
            {"title": "Python docs", "href": "https://docs.python.org/3/", "body": "Official docs"},
            {"title": "skip", "href": "/relative"},
            {"title": "Python docs", "href": "https://docs.python.org/3/", "body": "dup"},
        ]
    )
    assert hits == [("Python docs", "https://docs.python.org/3/", "Official docs")]
    photos = parse_ddgs_images(
        [
            {
                "title": "Fox",
                "url": "https://example.com/fox",
                "image": "https://img.example/fox.jpg",
            },
            {"title": "thumb-only", "thumbnail": "//cdn.example/t.png"},
        ]
    )
    assert photos[0] == ("Fox", "https://example.com/fox", "https://img.example/fox.jpg")
    assert photos[1][2] == "https://cdn.example/t.png"


def test_searxng_is_optional_and_has_no_docker_launch(monkeypatch) -> None:
    import backend.services.searxng as sx

    monkeypatch.setattr(sx, "SEARXNG_URL", "")
    monkeypatch.setattr("backend.services.settings_store.brains_settings", lambda: {"searxng_url": ""})
    monkeypatch.delenv("CATCREW_SEARXNG_URLS", raising=False)
    assert sx.searxng_urls() == []
    assert sx.search_searxng("python docs") == []
    assert not hasattr(sx, "launch_searxng")
