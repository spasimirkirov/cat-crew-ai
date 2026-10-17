"""Hugging Face GGUF catalog: Hub cards, quant + mmproj, gated downloads."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from .hardware import probe_hardware
from .local_models import (
    family_from_name,
    is_complete_gguf,
    is_mmproj_name,
    models_dir,
    parse_quant,
    write_sidecar,
)

HF_API = "https://huggingface.co/api/models"
HF_RESOLVE = "https://huggingface.co/{repo}/resolve/main/{filename}"
TRUSTED_AUTHORS = ("bartowski", "unsloth", "ggml-org", "Qwen", "google", "lmstudio-community")
PAGE_SIZE = 20
FETCH_LIMIT = 80
_UA = {"User-Agent": "JinxAI/1.0 (huggingface hub)"}
_QUANT_PREF = (
    "Q4_K_M",
    "Q5_K_M",
    "Q4_K_S",
    "Q5_K_S",
    "IQ4_XS",
    "Q6_K",
    "Q3_K_M",
    "Q8_0",
)
_VISION_TAGS = (
    "vision",
    "image-text-to-text",
    "image-to-text",
    "visual-question-answering",
    "multimodal",
    "llava",
)
_VISION_HINTS = (
    "mmproj",
    "llava",
    "vision",
    "-vl-",
    "vl-",
    "pixtral",
    "gemma-3",
    "gemma3",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen2.5vl",
    "internvl",
    "minicpm-v",
    "moondream",
)
_TOOL_TAGS = (
    "tools",
    "tool-use",
    "tool_use",
    "function-calling",
    "function_calling",
    "agent",
    "instruct",
)
_TOOL_HINTS = (
    "qwen",
    "llama",
    "gemma",
    "mistral",
    "ministral",
    "phi-",
    "phi3",
    "glm",
    "command",
    "deepseek",
    "internlm",
    "nemotron",
    "granite",
    "olmo",
    "yi-",
    "hermes",
)
_SKIP_HINTS = ("embed", "rerank", "colbert", "whisper", "tts", "clip", "wav2vec")
RECOMMENDED_HINTS = (
    "gemma-3",
    "gemma3",
    "qwen2.5",
    "qwen2-5",
    "qwen3",
    "llama-3.1",
    "llama-3.2",
    "llama-3.3",
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "ministral",
    "mistral-small",
    "phi-4",
    "phi4",
    "glm-4",
    "glm4",
    "minicpm-v",
    "minicpm_v",
)
RECOMMENDED_BLURB = (
    "Recommended filter is on: Jinx fetches a mix — Gemma 3, Llama 3, Ministral, "
    "Phi-4, GLM-4, MiniCPM-V, and only a couple of Qwen — from trusted GGUF authors, "
    "instruct / tool-calling, sized to ~70% of this GPU. Has Vision starts off so the "
    "list is not VL-only. Turn Recommended off to browse Hub downloads."
)
RECOMMENDED_QUERIES = (
    "gemma-3-it-GGUF bartowski",
    "Llama-3.1-8B-Instruct-GGUF bartowski",
    "Llama-3.3-70B-Instruct-GGUF bartowski",
    "Ministral-8B-Instruct-GGUF",
    "Phi-4-GGUF bartowski",
    "GLM-4-9B-Chat-GGUF",
    "MiniCPM-V-GGUF",
    "Qwen2.5-7B-Instruct-GGUF bartowski",
    "Qwen2.5-VL-7B-Instruct-GGUF",
)
_FAMILY_KEYS = (
    "gemma",
    "llama",
    "ministral",
    "phi",
    "glm",
    "minicpm",
    "qwen",
    "mistral",
)


@dataclass
class HfFamily:
    repo: str
    title: str
    downloads: int = 0
    author: str = ""
    blurb: str = ""
    trusted: bool = False
    likes: int = 0


@dataclass
class HfFile:
    repo: str
    filename: str
    size_bytes: int = 0
    quant: str = ""
    is_mmproj: bool = False
    recommended: bool = False


@dataclass
class HubCard:
    repo: str
    title: str
    author: str = ""
    downloads: int = 0
    likes: int = 0
    blurb: str = ""
    trusted: bool = False
    gated: bool = False
    pipeline: str = ""
    tags: list[str] = field(default_factory=list)
    files: list[HfFile] = field(default_factory=list)
    rec: HfFile | None = None
    vram_gb: float = 0.0
    fits_gpu: bool = False
    has_vision: bool = False
    has_tools: bool = False
    quants: list[str] = field(default_factory=list)


def is_gated(value: Any) -> bool:
    """True / 'auto' / 'manual' count as gated. False and empty do not."""
    return value not in (None, False, "", "false", "False", 0, "0")


def repo_page(repo: str) -> str:
    text = (repo or "").strip().lstrip("/")
    return f"https://huggingface.co/{text}" if text else "https://huggingface.co"


def hub_error_url(text: str) -> str:
    match = re.search(r"https://huggingface\.co/[^\s]+", text or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,);")


def gated_help(repo: str, *, has_token: bool = False, status: int | None = None) -> str:
    url = repo_page(repo)
    if not has_token:
        return (
            f"{repo} is gated. Add a Hugging Face token in Settings → API Access, "
            f"open {url} and accept the license, then pull again."
        )
    code = status or 403
    return (
        f"Hugging Face denied this download (HTTP {code}). The token is set, "
        f"but this account may not have accepted the license. "
        f"Open {url}, accept it, then pull again."
    )


def probe_gated(repo: str) -> bool | None:
    """Hub metadata: True gated, False public, None if the probe failed."""
    text = (repo or "").strip()
    if not text:
        return False
    try:
        from huggingface_hub import HfApi

        from .api_access import huggingface_token

        api = HfApi(token=huggingface_token() or None)
        info = api.model_info(text)
        return is_gated(getattr(info, "gated", False))
    except Exception:  # noqa: BLE001
        return None


def huggingface_headers(*, use_token: bool = True) -> dict[str, str]:
    """Hub API headers. Public GGUF downloads pass use_token=False."""
    from .api_access import huggingface_token

    headers = dict(_UA)
    if not use_token:
        return headers
    token = huggingface_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _blob(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    out: dict[str, Any] = {}
    for key in (
        "id",
        "modelId",
        "downloads",
        "likes",
        "tags",
        "pipeline_tag",
        "gated",
        "siblings",
        "cardData",
        "card_data",
        "description",
    ):
        if hasattr(item, key):
            out[key] = getattr(item, key)
    return out


def _files_from_siblings(repo: str, siblings: Any) -> list[HfFile]:
    files: list[HfFile] = []
    if not isinstance(siblings, list):
        return files
    for row in siblings:
        name = ""
        size = 0
        if isinstance(row, dict):
            name = str(row.get("rfilename") or row.get("filename") or "").strip()
            size = int(row.get("size") or 0)
        else:
            name = str(getattr(row, "rfilename", "") or getattr(row, "filename", "") or "").strip()
            size = int(getattr(row, "size", 0) or 0)
        if not name.lower().endswith(".gguf"):
            continue
        files.append(
            HfFile(
                repo=repo,
                filename=name,
                size_bytes=size,
                quant=parse_quant(name),
                is_mmproj=is_mmproj_name(name),
            )
        )
    return files


def infer_vision(repo: str, tags: list[str], files: list[HfFile]) -> bool:
    blob = f"{repo} {' '.join(tags)}".lower()
    if any(item.is_mmproj for item in files):
        return True
    if any(tag.lower() in _VISION_TAGS or "vision" in tag.lower() for tag in tags):
        return True
    return any(hint in blob for hint in _VISION_HINTS)


def infer_tools(repo: str, tags: list[str]) -> bool:
    blob = repo.lower()
    if any(hint in blob for hint in _SKIP_HINTS):
        return False
    if any(tag.lower() in _TOOL_TAGS or "tool" in tag.lower() for tag in tags):
        return True
    return any(hint in blob for hint in _TOOL_HINTS)


def is_recommended(card: HubCard) -> bool:
    """Jinx's Hub picks: trusted author, fits this GPU, tools, known instruct family."""
    if not card.trusted or not card.fits_gpu or not card.has_tools:
        return False
    blob = f"{card.repo} {card.title}".lower()
    if any(hint in blob for hint in _SKIP_HINTS):
        return False
    return any(hint in blob for hint in RECOMMENDED_HINTS)


def _as_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key in ("description", "pretty_name", "license"):
        if hasattr(value, key):
            out[key] = getattr(value, key)
    return out


def _clip_blurb(text: str, *, limit: int = 280) -> str:
    clean = re.sub(r"\s+", " ", re.sub(r"^#+\s*", "", (text or "").strip()))
    if not clean:
        return ""
    if len(clean) <= limit:
        return clean
    cut = clean[: limit - 1].rsplit(" ", 1)[0]
    return (cut or clean[: limit - 1]) + "…"


def card_blurb(data: dict[str, Any], *, pipeline: str = "") -> str:
    card_data = _as_map(data.get("cardData") or data.get("card_data") or {})
    skip = {"", "gguf", "text-generation", "image-text-to-text", "text-to-image"}
    for raw in (
        data.get("description"),
        card_data.get("description"),
        card_data.get("pretty_name"),
        pipeline,
    ):
        text = _clip_blurb(str(raw or ""))
        if text.lower() not in skip:
            return text
    return pipeline or "GGUF"


def card_from_payload(row: Any, *, vram_gb: float) -> HubCard | None:
    data = _blob(row)
    repo = str(data.get("id") or data.get("modelId") or "").strip()
    if not repo:
        return None
    author = repo.split("/")[0] if "/" in repo else ""
    tags = [str(item) for item in (data.get("tags") or []) if item]
    files = _files_from_siblings(repo, data.get("siblings"))
    rec = pick_quant(files, vram_gb=vram_gb) if files else None
    size_gb = (rec.size_bytes / 1_000_000_000) if rec and rec.size_bytes else 0.0
    budget = vram_budget_gb(vram_gb or 8.0)
    pipeline = str(data.get("pipeline_tag") or "")
    gated = is_gated(data.get("gated"))
    blurb = card_blurb(data, pipeline=pipeline)
    return HubCard(
        repo=repo,
        title=repo.split("/")[-1],
        author=author,
        downloads=int(data.get("downloads") or 0),
        likes=int(data.get("likes") or 0),
        blurb=blurb,
        trusted=author in TRUSTED_AUTHORS,
        gated=bool(gated),
        pipeline=pipeline,
        tags=tags,
        files=files,
        rec=rec,
        vram_gb=round(size_gb, 1) if size_gb else 0.0,
        fits_gpu=bool(rec) and (not rec.size_bytes or size_gb <= budget),
        has_vision=infer_vision(repo, tags, files),
        has_tools=infer_tools(repo, tags),
        quants=sorted({item.quant for item in files if item.quant and not item.is_mmproj}),
    )


def search_hub(
    query: str = "",
    *,
    limit: int = FETCH_LIMIT,
    vram_gb: float | None = None,
    recommended: bool = False,
) -> list[HubCard]:
    if vram_gb is None:
        try:
            vram_gb = float(probe_hardware().vram_gb or 0)
        except Exception:  # noqa: BLE001
            vram_gb = 0
    vram = float(vram_gb or 0)
    text = (query or "").strip()
    if recommended and not text:
        cards = search_recommended(vram_gb=vram)
        if cards:
            return cards
    cards = _search_hub_hfapi(text, limit=limit, vram_gb=vram)
    if not cards:
        cards = _search_hub_http(text, limit=limit, vram_gb=vram)
    cards.sort(key=lambda item: (not item.trusted, -item.downloads, item.repo.lower()))
    if recommended:
        return diversify_cards(cards, per_family=2)
    return cards


def search_recommended(*, vram_gb: float, per_query: int = 4) -> list[HubCard]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    seen: set[str] = set()
    out: list[HubCard] = []

    def one(query: str) -> list[HubCard]:
        rows = _search_hub_hfapi(query, limit=per_query, vram_gb=vram_gb)
        if not rows:
            rows = _search_hub_http(query, limit=per_query, vram_gb=vram_gb)
        return rows

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(one, query) for query in RECOMMENDED_QUERIES]
        for fut in as_completed(futs):
            try:
                rows = fut.result()
            except Exception:  # noqa: BLE001
                continue
            for card in rows:
                if card.repo in seen:
                    continue
                seen.add(card.repo)
                out.append(card)
    out.sort(key=lambda item: (_family_rank(item), not item.trusted, -item.downloads, item.repo.lower()))
    return diversify_cards(out, per_family=3)


def card_family(card: HubCard) -> str:
    blob = f"{card.repo} {card.title}".lower()
    for key in _FAMILY_KEYS:
        if key in blob:
            return key
    return (card.author or "other").lower()


def _family_rank(card: HubCard) -> int:
    key = card_family(card)
    try:
        return _FAMILY_KEYS.index(key)
    except ValueError:
        return len(_FAMILY_KEYS)


def diversify_cards(cards: list[HubCard], *, per_family: int = 2) -> list[HubCard]:
    buckets: dict[str, list[HubCard]] = {}
    order: list[str] = []
    for item in cards:
        key = card_family(item)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        if len(buckets[key]) < per_family:
            buckets[key].append(item)
    out: list[HubCard] = []
    index = 0
    while True:
        added = False
        for key in order:
            rows = buckets[key]
            if index < len(rows):
                out.append(rows[index])
                added = True
        if not added:
            break
        index += 1
    return out


def _search_hub_hfapi(query: str, *, limit: int, vram_gb: float) -> list[HubCard]:
    try:
        from huggingface_hub import HfApi

        from .api_access import huggingface_token

        api = HfApi(token=huggingface_token() or None)
        kwargs: dict[str, Any] = {
            "search": query or None,
            "filter": "gguf",
            "sort": "downloads",
            "direction": -1,
            "limit": limit,
            "cardData": True,
        }
        try:
            models = list(api.list_models(**kwargs, expand=["siblings", "cardData"]))
        except TypeError:
            models = list(api.list_models(**kwargs, full=True))
        out: list[HubCard] = []
        seen: set[str] = set()
        for info in models:
            card = card_from_payload(info, vram_gb=vram_gb)
            if card is None or card.repo in seen:
                continue
            seen.add(card.repo)
            if not card.files:
                continue
            out.append(card)
        return out
    except Exception:  # noqa: BLE001
        return []


def _search_hub_http(query: str, *, limit: int, vram_gb: float) -> list[HubCard]:
    params = {
        "filter": "gguf",
        "sort": "downloads",
        "direction": "-1",
        "limit": str(limit),
        "full": "true",
    }
    if query:
        params["search"] = query
    response = requests.get(HF_API, params=params, timeout=25, headers=huggingface_headers())
    response.raise_for_status()
    rows = response.json()
    rows = rows if isinstance(rows, list) else []
    out: list[HubCard] = []
    seen: set[str] = set()
    for row in rows:
        card = card_from_payload(row, vram_gb=vram_gb)
        if card is None or card.repo in seen:
            continue
        seen.add(card.repo)
        if not card.files:
            continue
        out.append(card)
    return out


def filter_cards(
    cards: list[HubCard],
    *,
    gpu: bool = True,
    vision: bool = True,
    tools: bool = True,
    recommended: bool = False,
) -> list[HubCard]:
    out: list[HubCard] = []
    for item in cards:
        if recommended and not is_recommended(item):
            continue
        if gpu and not item.fits_gpu:
            continue
        if vision and not item.has_vision:
            continue
        if tools and not item.has_tools:
            continue
        out.append(item)
    if recommended:
        return diversify_cards(out, per_family=3)
    return out


def paginate(cards: list[HubCard], page: int, *, size: int = PAGE_SIZE) -> tuple[list[HubCard], int, int]:
    total = max(1, (len(cards) + size - 1) // size) if cards else 1
    page = max(0, min(int(page), total - 1))
    start = page * size
    return cards[start : start + size], page, total


def search_families(query: str, *, limit: int = 24) -> list[HfFamily]:
    cards = search_hub(query, limit=limit)
    return [
        HfFamily(
            repo=item.repo,
            title=item.title,
            downloads=item.downloads,
            author=item.author,
            blurb=item.blurb,
            trusted=item.trusted,
            likes=item.likes,
        )
        for item in cards
    ]


def list_repo_files(repo: str) -> list[HfFile]:
    text = (repo or "").strip()
    if not text:
        return []
    from .api_access import huggingface_token

    gated = probe_gated(text)
    token = huggingface_token()
    if gated is True and not token:
        raise RuntimeError(gated_help(text, has_token=False))
    return _list_repo_files_http(text, use_token=gated is True)


def _list_repo_files_http(repo: str, *, use_token: bool) -> list[HfFile]:
    from .api_access import huggingface_token

    response = requests.get(
        f"{HF_API}/{repo}",
        params={"blobs": "true"},
        timeout=20,
        headers=huggingface_headers(use_token=use_token),
    )
    if response.status_code in {401, 403}:
        if not use_token and huggingface_token():
            return _list_repo_files_http(repo, use_token=True)
        raise RuntimeError(
            gated_help(repo, has_token=bool(huggingface_token()), status=response.status_code)
        )
    response.raise_for_status()
    payload = response.json()
    siblings = payload.get("siblings") if isinstance(payload, dict) else None
    return _files_from_siblings(repo, siblings)


def guess_hub_spec(filename: str) -> str:
    """Best-effort repo#file for a leftover GGUF name (retry without a stored repo)."""
    file_name = Path(filename or "").name
    if not file_name.lower().endswith(".gguf"):
        file_name = f"{Path(file_name).stem}.gguf"
    if is_mmproj_name(file_name):
        return ""
    family = family_from_name(file_name)
    if not family:
        return ""
    dashed = family.replace("_", "-")
    underscored = family.replace("-", "_")
    candidates: list[str] = []
    for fam in (family, dashed, underscored):
        if not fam:
            continue
        for author in TRUSTED_AUTHORS:
            candidates.append(f"{author}/{fam}-GGUF")
    seen: set[str] = set()
    want = file_name.lower()
    for repo in candidates:
        if repo in seen:
            continue
        seen.add(repo)
        try:
            files = _list_repo_files_http(repo, use_token=False)
        except Exception:  # noqa: BLE001
            continue
        if any(Path(item.filename).name.lower() == want for item in files):
            return f"{repo}#{file_name}"
    return ""


def vram_budget_gb(vram_gb: float, frac: float = 0.7) -> float:
    try:
        return max(1.0, float(vram_gb) * frac)
    except (TypeError, ValueError):
        return 8.0


def pick_quant(files: list[HfFile], *, vram_gb: float | None = None) -> HfFile | None:
    chat = [item for item in files if not item.is_mmproj]
    if not chat:
        return None
    if vram_gb is None:
        try:
            vram_gb = float(probe_hardware().vram_gb or 0)
        except Exception:  # noqa: BLE001
            vram_gb = 0
    budget = vram_budget_gb(float(vram_gb or 0) or 8.0)
    ranked: list[tuple[int, int, HfFile]] = []
    for item in chat:
        size_gb = item.size_bytes / 1_000_000_000 if item.size_bytes else 0
        fits = 1 if (not item.size_bytes or size_gb <= budget) else 0
        try:
            pref = _QUANT_PREF.index(item.quant)
        except ValueError:
            pref = len(_QUANT_PREF) + 1
        ranked.append((fits, -pref, item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    chosen = ranked[0][2]
    chosen.recommended = True
    return chosen


def matching_mmproj(files: list[HfFile], gguf: HfFile) -> HfFile | None:
    family = family_from_name(gguf.filename).lower()
    mates = [item for item in files if item.is_mmproj]
    if not mates:
        return None
    for item in mates:
        if family and family in item.filename.lower():
            return item
    return mates[0]


def download_gguf(
    repo: str,
    filename: str,
    *,
    dest_dir: Path | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    abort: Callable[[], bool] | None = None,
    mmproj: str = "",
    extra: dict[str, Any] | None = None,
) -> Path:
    from .api_access import huggingface_token

    folder = dest_dir or models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    abort = abort or (lambda: False)
    gated = probe_gated(repo)
    token = huggingface_token()
    if gated is True and not token:
        raise RuntimeError(gated_help(repo, has_token=False))
    use_token = gated is True
    target = folder / Path(filename).name
    sidecar = {
        "hf_repo": repo,
        "quant": parse_quant(filename),
        "family": family_from_name(filename),
        "mmproj": Path(mmproj).name if mmproj else "",
        **(extra or {}),
    }
    write_sidecar(target, sidecar)
    if target.is_file() and is_complete_gguf(target):
        if mmproj:
            mate_path = folder / Path(mmproj).name
            write_sidecar(mate_path, {"hf_repo": repo, "mmproj": Path(mmproj).name})
            if not mate_path.is_file() or not is_complete_gguf(mate_path):
                _http_download(repo, mmproj, mate_path, on_progress, abort, use_token=use_token)
        return target
    _http_download(repo, filename, target, on_progress, abort, use_token=use_token)
    write_sidecar(target, sidecar)
    if mmproj:
        mate_path = folder / Path(mmproj).name
        write_sidecar(mate_path, {"hf_repo": repo, "mmproj": Path(mmproj).name})
        _http_download(repo, mmproj, mate_path, on_progress, abort, use_token=use_token)
    return target


def _http_download(
    repo: str,
    filename: str,
    dest: Path,
    on_progress: Callable[[dict[str, Any]], None] | None,
    abort: Callable[[], bool],
    *,
    use_token: bool = False,
) -> None:
    from .api_access import huggingface_token

    url = HF_RESOLVE.format(repo=repo, filename=filename)
    part = dest.with_name(dest.name + ".part")
    existing = part.stat().st_size if part.is_file() else 0
    headers = huggingface_headers(use_token=use_token)
    if existing:
        headers["Range"] = f"bytes={existing}-"
    with requests.get(url, stream=True, timeout=(20, 900), headers=headers) as response:
        if response.status_code in {401, 403}:
            if not use_token and huggingface_token():
                _http_download(repo, filename, dest, on_progress, abort, use_token=True)
                return
            raise RuntimeError(
                gated_help(repo, has_token=bool(huggingface_token()), status=response.status_code)
            )
        if existing and response.status_code not in {200, 206}:
            try:
                part.unlink()
            except OSError:
                pass
            _http_download(repo, filename, dest, on_progress, abort, use_token=use_token)
            return
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0) + existing
        mode = "ab" if existing and response.status_code == 206 else "wb"
        if mode == "wb":
            existing = 0
        done = existing
        with part.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if abort():
                    raise RuntimeError(f"Cancelled download of {filename}")
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if on_progress:
                    percent = int(round(100 * done / total)) if total else 0
                    on_progress(
                        {
                            "status": filename,
                            "completed": done,
                            "total": total,
                            "percent": percent,
                            "indeterminate": total <= 0,
                            "text": f"{filename} · {percent}%" if total else filename,
                        }
                    )
    if abort():
        raise RuntimeError(f"Cancelled download of {filename}")
    if done <= 0:
        raise RuntimeError(f"Empty download of {filename}")
    if total and done < total:
        raise RuntimeError(f"Incomplete download of {filename} ({done} of {total} bytes)")
    if dest.exists():
        dest.unlink()
    part.replace(dest)
