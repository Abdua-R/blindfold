import json
import os
from dataclasses import dataclass, fields
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("BLINDFOLD_HOME") or Path.home() / ".blindfold")


@dataclass
class Config:
    port: int = 8787
    upstream: str = "https://api.anthropic.com"
    llm_base_url: str = ""   # the LLM switch: any private or local OpenAI-compatible server (vLLM, Ollama, ...); "" turns L5 off
    llm_model: str = "gpt-oss-120b"
    llm_api_key: str = ""
    llm_timeout_s: float = 15.0
    retention_days: int = 7
    use_ner: bool = True


def load() -> Config:
    p = home() / "config.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known})
