"""Layer L5: the LLM switch. Any OpenAI-compatible endpoint: a private server or a local model (Ollama, vLLM)."""
import hashlib
import json
import re
import threading
import time
from pathlib import Path

import httpx

from .config import home
from .session import _write

PROMPT = (
    "Find personal identifiers in this text (Uzbek Latin or Cyrillic, Russian or English). "
    'Answer with JSON only: {"items": [{"text": "<exact substring>", "type": "Person|Org|Address"}]}. '
    "List names of private people, private companies and street addresses exactly as written. "
    "Skip anything in square brackets, public bodies, cities and regions.\nText:\n")
TYPES = {"Person", "Org", "Address"}
_lock = threading.Lock()
_mem: dict[Path, dict] = {}   # cache file -> {key: [time, items]}, loaded lazily
_clock = time.time


def _cache(path: Path) -> dict:
    if path not in _mem:
        try:
            _mem[path] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _mem[path] = {}
    return _mem[path]


def llm_find(text: str, cfg, transport: httpx.BaseTransport | None = None) -> list[tuple[str, str]] | None:
    """Exact strings the model marks, or None when the model is unavailable. Results are cached
    locally by (model, base URL, masked text) for retention_days; failures are never cached."""
    path = home() / "llm_cache.json"
    key = hashlib.sha256(json.dumps([cfg.llm_model, cfg.llm_base_url, text]).encode("utf-8")).hexdigest()
    cutoff = _clock() - cfg.retention_days * 86400
    with _lock:
        hit = _cache(path).get(key)
    if hit and hit[0] >= cutoff:
        return [tuple(x) for x in hit[1]]
    found = _ask(text, cfg, transport)
    if found is not None:
        with _lock:
            c = _cache(path)
            # ponytail: expired entries leave the file only on the next write; purge it at serve start if that matters
            for k in [k for k, v in c.items() if v[0] < cutoff]:
                del c[k]
            c[key] = [_clock(), found]
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _write(path, c)
            except OSError:
                pass   # the cache is an optimisation; detection must not fail over it
    return found


_sleep = time.sleep
_RETRY_IN = re.compile(r"retry in ([\d.]+)\s*(ms|s)", re.I)


def _retry_wait(r: httpx.Response) -> float | None:
    """Seconds to wait before retrying a rate-limited or overloaded call, or None to stop.
    A daily cap is never retried; a short per-minute limit or a 503 is."""
    if r.status_code not in (429, 503) or "PerDay" in r.text:
        return None
    m = _RETRY_IN.search(r.text)
    wait = (float(m.group(1)) / (1000 if m.group(2).lower() == "ms" else 1)) if m else 2.0
    return min(max(wait, 0.2), 20.0)


def _ask(text: str, cfg, transport) -> list[tuple[str, str]] | None:
    headers = {"Authorization": f"Bearer {cfg.llm_api_key}"} if cfg.llm_api_key else {}
    body = {"model": cfg.llm_model, "temperature": 0,
            "messages": [{"role": "system", "content": "Reasoning: low"},
                         {"role": "user", "content": PROMPT + text}]}
    deadline, waited = time.monotonic() + cfg.llm_timeout_s, 0.0
    try:
        with httpx.Client(transport=transport, timeout=cfg.llm_timeout_s) as c:
            while True:
                r = c.post(cfg.llm_base_url.rstrip("/") + "/chat/completions", json=body, headers=headers)
                wait = _retry_wait(r)
                if wait is None or time.monotonic() + wait >= deadline or waited + wait >= cfg.llm_timeout_s:
                    break
                _sleep(wait)
                waited += wait
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.S)
        items = json.loads(m.group()).get("items", []) if m else []
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None
    return [(i["text"], i["type"]) for i in items
            if isinstance(i, dict) and i.get("type") in TYPES and i.get("text")]
