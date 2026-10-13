"""Layer L4: one multilingual NER model (GLiNER2), optional."""
from .types import Span

MODEL = "fastino/gliner2-multi-v1"
LABELS = {"person": "Person", "organization": "Org", "address": "Address"}
_model = None
_failed = False   # set once a load or inference fails; the model is then not retried this process


def _strings(out) -> list[tuple[str, str]]:
    ents = out.get("entities", out) if isinstance(out, dict) else {}
    found = []
    for label, items in ents.items():
        for it in items or []:
            s = it if isinstance(it, str) else (it.get("text") or "")
            if s and label in LABELS:
                found.append((s, LABELS[label]))
    return found


def _chunks(text: str, size: int = 1500) -> list[str]:
    parts, cur = [], ""
    for para in text.split("\n"):
        if cur and len(cur) + len(para) > size:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    return parts + ([cur] if cur else [])


def unavailable() -> bool:
    return _failed


def find(text: str, notices: set | None = None) -> list[Span]:
    """Names from the model, or [] with a "ner_unavailable" notice when the model cannot run.
    A missing package (ImportError) means NER is simply not installed: [] and no notice."""
    global _model, _failed
    if _failed:
        if notices is not None:
            notices.add("ner_unavailable")
        return []
    try:
        if _model is None:
            from gliner2 import GLiNER2
            _model = GLiNER2.from_pretrained(MODEL)
        items = []
        for chunk in _chunks(text):
            items += _strings(_model.extract_entities(chunk, list(LABELS)))
    except ImportError:
        return []
    except Exception:
        _failed = True
        if notices is not None:
            notices.add("ner_unavailable")
        return []
    from .detect import locate
    return locate(text, items)
