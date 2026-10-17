from pathlib import Path

from jinx_ai.activity import next_reveal, phase_for
from jinx_ai.searxng import parse_searxng, parse_searxng_images
from jinx_ai.comfyui import sampler_for_checkpoint, txt2img_workflow
from jinx_ai.chat_transcript import extract_images
from jinx_ai.image_search import parse_brave_images, parse_wikimedia_images
from jinx_ai.tools import ASSISTANT_TOOL_NAMES, tools_schema_for


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
                    "title": "Arcane Jinx",
                    "url": "https://example.com/jinx",
                    "img_src": "https://cdn.example.com/jinx.jpg",
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
    assert hits[0] == ("Arcane Jinx", "https://example.com/jinx", "https://cdn.example.com/jinx.jpg")
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
    title, detail = phase_for("image_search", {"query": "arcane jinx"})
    assert title == "Searching images"
    assert "jinx" in detail


def test_next_reveal_types_smoothly() -> None:
    shown = ""
    target = "Hello from Jinx, streaming a longer reply."
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


def test_extract_images_strips_markdown() -> None:
    text, paths = extract_images("Hi\n\n![cap](C:/nope/missing.png)\n")
    assert "Hi" in text
    assert paths == []


def test_extract_images_keeps_existing_files(tmp_path: Path) -> None:
    one = tmp_path / "a.png"
    two = tmp_path / "b.png"
    one.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    two.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    text, paths = extract_images(f"Look\n\n![a]({one.as_posix()})\n![b]({two.as_posix()})\n")
    assert text == "Look"
    assert paths == [one, two]
