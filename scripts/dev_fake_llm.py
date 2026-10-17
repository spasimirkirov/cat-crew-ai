"""Offline stand-in for LM Studio: an OpenAI-compatible server with scripted replies.

Lets you exercise the whole stack (SOP pipeline, live sidebar pop-in, execution
cards, file diffs) with no model installed, and drives the end-to-end tests /
UI screenshots. Point CatCrewAI at it with ``CATCREW_LMSTUDIO_URL`` (default
``http://127.0.0.1:1234/v1``):

    python scripts/dev_fake_llm.py --port 1234 --delay 0.6

Behaviour: a request that offers the ``write_file`` tool and has no tool result
yet gets one ``write_file`` call (so builder phases are "verified"); anything
else gets a short streamed text reply.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_calls = 0
DELAY = 0.4


def _persona(messages: list[dict]) -> str:
    # LM Studio providers may fold the system prompt into the first user turn,
    # so look at the whole conversation for "You are <Name>, a <role> bot".
    text = " ".join(str(m.get("content") or "") for m in messages)
    m = re.search(r"You are (\w+), an? [\w ]+? bot", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(Cipher|Vanguard|Pixel|Pulse|Aegis|Lex|Blaze|Forge)\b", text)
    return m.group(1) if m else "CatCrewAI"


def _tool_names(tools: list[dict] | None) -> set[str]:
    return {str((t.get("function") or {}).get("name") or "") for t in (tools or [])}


def _has_tool_result(messages: list[dict]) -> bool:
    for m in reversed(messages):
        if m.get("role") == "tool":
            return True
        if m.get("role") == "user":
            return False
    return False


def script_for(body: dict) -> tuple[str, dict | None]:
    global _calls
    with _lock:
        _calls += 1
        n = _calls
    messages = body.get("messages") or []
    who = _persona(messages)
    if "write_file" in _tool_names(body.get("tools")) and not _has_tool_result(messages):
        path = f"notes/{who.lower()}-{n}.md"
        content = (
            f"# {who} — working notes #{n}\n\n"
            "This file was written by the offline demo model.\n\n"
            "- scope agreed with the crew\n- next step queued for the following specialist\n"
            "- nothing here touches your real projects\n"
        )
        return "", {"name": "write_file", "arguments": json.dumps({"path": path, "content": content})}
    return f"{who} here — done. Everything for this step is in place and handed to the next cat.", None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # close-delimited bodies: no chunked framing needed for SSE

    def log_message(self, *_args) -> None:  # quiet
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json({"data": [{"id": "demo-model", "object": "model"}]})
        else:
            self._json({"error": "not found"}, 404)

    def _sse(self, obj: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        time.sleep(DELAY)
        text, call = script_for(body)
        if not body.get("stream"):
            message: dict = {"role": "assistant", "content": text or None}
            if call:
                message["tool_calls"] = [{"id": "call_1", "type": "function", "function": call}]
            self._json({"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if call:
            self._sse({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "type": "function", "function": call}]}}]})
        for word in text.split(" "):
            self._sse({"choices": [{"index": 0, "delta": {"content": word + " "}}]})
            time.sleep(0.02)
        self._sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if call else "stop"}]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> None:
    global DELAY
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--delay", type=float, default=0.4, help="seconds of fake 'thinking' per request")
    args = ap.parse_args()
    DELAY = args.delay
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"FAKE_LLM_LISTENING http://127.0.0.1:{args.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
