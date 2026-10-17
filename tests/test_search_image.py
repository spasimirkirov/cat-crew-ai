from pathlib import Path

from backend.activity import next_reveal, phase_for
from backend.services.searxng import parse_searxng, parse_searxng_images
from backend.services.comfyui import sampler_for_checkpoint, txt2img_workflow
from backend.services.image_search import parse_brave_images, parse_wikimedia_images
from backend.tools import ASSISTANT_TOOL_NAMES, tools_schema_for


def test_searxng_parses_results() -> None:
    hits = parse_searxng(
        {
            "results": [
                {
                    "title": "Sofia weather",
                    "url": "https://example.com/sofia",
                    "content": "18 C tomorrow",
                },
                {"title": "Skip me", "url": "/relative"},
            ]
        }
    )
    assert hits == [("Sofia weather", "https://example.com/sofia", "18 C tomorrow")]


def test_searxng_parses_image_hits() -> None:
    hits = parse_searxng_images(
        {
            "results": [
                {
                    "title": "Arcane CatCrewAI",
                    "url": "https://example.com/catcrew",
                    "img_src": "https://cdn.example.com/catcrew.jpg",
                },
                {
                    "title": "protocol-relative",
                    "url": "//example.com/page",
                    "thumbnail_src": "//cdn.example.com/thumb.png",
                },
                {"title": "skip", "url": "https://example.com/x"},
            ]
        }
    )
    assert hits[0] == ("Arcane CatCrewAI", "https://example.com/catcrew", "https://cdn.example.com/catcrew.jpg")
    assert hits[1][2] == "https://cdn.example.com/thumb.png"


def test_brave_and_wikimedia_image_parsers() -> None:
    brave = parse_brave_images(
        {
            "results": [
                {
                    "title": "Fox",
                    "url": "https://example.com/fox",
                    "thumbnail": {"src": "https://thumbs.example/fox.jpg"},
                    "properties": {"url": "https://img.example/fox.jpg"},
                }
            ]
        }
    )
    assert brave == [("Fox", "https://example.com/fox", "https://img.example/fox.jpg")]
    wiki = parse_wikimedia_images(
        {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:Fox.jpg",
                        "imageinfo": [
                            {
                                "thumburl": "https://upload.wikimedia.org/fox-thumb.jpg",
                                "url": "https://upload.wikimedia.org/fox.jpg",
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Fox.jpg",
                            }
                        ],
                    }
                }
            }
        }
    )
    assert wiki[0][0] == "Fox.jpg"
    assert "fox-thumb.jpg" in wiki[0][2]


def test_phase_for_search_and_image() -> None:
    title, detail = phase_for("web_search", {"query": "Sofia weather tomorrow"})
    assert title == "Searching the web"
    assert "Sofia" in detail
    title, detail = phase_for("generate_image", {"prompt": "a neon fox"})
    assert title == "Generating image"
    assert "fox" in detail
    title, detail = phase_for("image_search", {"query": "arcane catcrew"})
    assert title == "Searching images"
    assert "catcrew" in detail


def test_next_reveal_types_smoothly() -> None:
    shown = ""
    target = "Hello from CatCrewAI, streaming a longer reply."
    steps = 0
    while shown != target:
        nxt = next_reveal(shown, target)
        assert len(nxt) > len(shown) or nxt == target
        shown = nxt
        steps += 1
        assert steps < 80
    assert shown == target


def test_comfy_workflow_keeps_checkpoint_and_prompt() -> None:
    graph = txt2img_workflow("sd_xl_turbo_1.0_fp16.safetensors", "a cyan orb")
    assert graph["4"]["inputs"]["ckpt_name"].endswith(".safetensors")
    assert graph["6"]["inputs"]["text"] == "a cyan orb"
    assert sampler_for_checkpoint("sd_xl_turbo_1.0")["steps"] <= 8


def test_generate_image_is_an_assistant_tool() -> None:
    names = {item["function"]["name"] for item in tools_schema_for("assistant")}
    assert names == set(ASSISTANT_TOOL_NAMES)
    assert "generate_image" in names
    assert "image_search" in names
    assert "web_search" in names


def test_generate_image_routes_to_openrouter_or_comfyui_by_the_images_role(isolated, monkeypatch) -> None:
    """The Images role decides the host, same as any other role -- ComfyUI stays the default, but an
    OpenRouter pick is routed to the new module instead, never both."""
    from backend.services import model_resolver
    from backend import tools as tools_mod

    calls: list[str] = []

    def fake_or_generate(prompt, model, *, negative="", width=0, height=0, seed=-1):
        calls.append("openrouter")
        assert model == "google/gemini-2.5-flash-image"
        return Path("/tmp/or.png")

    def fake_comfy_generate(prompt, **kwargs):
        calls.append("comfyui")
        return Path("/tmp/comfy.png")

    monkeypatch.setattr(
        model_resolver,
        "get_effective_model",
        lambda project_id, role, data=None: model_resolver.EffectiveModel(host="openrouter", model="google/gemini-2.5-flash-image", source="global"),
    )
    monkeypatch.setattr("backend.services.openrouter_images.generate_image", fake_or_generate)
    monkeypatch.setattr("backend.services.openrouter_images.format_saved", lambda paths, caption="": "openrouter-saved")
    out = tools_mod.generate_image("a neon fox")
    assert calls == ["openrouter"] and out == "openrouter-saved"

    monkeypatch.setattr(
        model_resolver,
        "get_effective_model",
        lambda project_id, role, data=None: model_resolver.EffectiveModel(host="comfyui", model="sd_xl_turbo.safetensors", source="global"),
    )
    monkeypatch.setattr("backend.services.comfyui.generate_image", fake_comfy_generate)
    monkeypatch.setattr("backend.services.comfyui.format_saved", lambda paths, caption="": "comfy-saved")
    out = tools_mod.generate_image("a neon fox")
    assert calls == ["openrouter", "comfyui"] and out == "comfy-saved"
