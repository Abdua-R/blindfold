"""Per-session state: depth, placeholder map, scrub memo, exact inversion records."""
import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from .config import home
from .normalize import entity_key

_SID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_cache: dict[tuple[str, str], "Session"] = {}
_cache_lock = threading.Lock()


def valid_sid(sid) -> bool:
    return isinstance(sid, str) and bool(_SID.fullmatch(sid))


def block_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _dir(root, sid) -> Path:
    return Path(root) / "sessions" / sid


def _write(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class Session:
    def __init__(self, sid: str, root: Path):
        self.sid, self.dir = sid, _dir(root, sid)
        self.lock = threading.RLock()
        st = self._read("state.json", {})
        self.depth = st.get("depth", "normal")
        self.start_index = st.get("start_index")
        self.notices = set(st.get("notices", []))          # delivered to the user
        self.pending_notices = set(st.get("pending_notices", []))  # raised, not yet shown
        self.entities = self._read("map.json", [])
        self.memo = self._read("memo.json", {})
        self.inv_text = self._read("inv_text.json", {})
        self.inv_tool = self._read("inv_tool.json", {})
        self._by_ph = {self._ph(e): e for e in self.entities}
        self._pending: list = []

    @staticmethod
    def _ph(e) -> str:
        return f"[{e['type']}_{e['n']}]"

    def _read(self, name, default):
        p = self.dir / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def placeholder(self, type_: str, value: str) -> str:
        key = list(entity_key(type_, value))
        with self.lock:
            # An exact key match wins; otherwise a shorter mention reuses a longer entry that contains it.
            # A longer name never merges into a shorter entry, or it would be restored in the short form.
            for e in self.entities:   # a surface form already seen maps straight to its entry
                if e["type"] == type_ and (value == e["value"] or value in e.get("forms", [])):
                    return self._ph(e)
            same = [e for e in self.entities if e["type"] == type_ and key and e["key"]]
            exact = [e for e in same if list(e["key"]) == key]   # word order kept, so restore keeps it
            for e in exact or [e for e in same if set(key) < set(e["key"])]:
                forms = e.setdefault("forms", [])
                if value not in forms:
                    forms.append(value)
                return self._ph(e)
            n = 1 + sum(1 for e in self.entities if e["type"] == type_)
            e = {"type": type_, "n": n, "key": key, "value": value, "forms": [value]}   # verbatim: restored as first written
            self.entities.append(e)
            self._by_ph[self._ph(e)] = e
            return self._ph(e)

    def value_of(self, ph: str) -> str | None:
        e = self._by_ph.get(ph)
        return e["value"] if e else None

    def known_values(self, depth: str) -> list[tuple[str, str]]:
        """(type, original surface form) of every entity, for re-masking a known value that comes
        back with no detector support. Files written before forms were kept fall back to value."""
        with self.lock:
            return [(e["type"], f) for e in self.entities
                    if depth == "strict" or e["type"] not in ("Num", "Date")
                    for f in {*e.get("forms", []), e["value"]}]

    def name_entities(self) -> list[dict]:
        return [e for e in self.entities if e["type"] in ("Person", "Org")]

    def record_text(self, restored: str, original: str) -> None:
        with self.lock:
            self._pending.append(("t", block_hash(restored), original))

    def record_tool(self, tool_id: str, original_input) -> None:
        with self.lock:
            self._pending.append(("u", tool_id, original_input))

    def merge_pending(self) -> None:
        items, self._pending = self._pending, []
        for kind, k, v in items:
            (self.inv_text if kind == "t" else self.inv_tool)[k] = v

    def save(self) -> None:
        self.merge_pending()
        self.dir.mkdir(parents=True, exist_ok=True)
        _write(self.dir / "state.json", {"depth": self.depth, "start_index": self.start_index,
                                         "notices": sorted(self.notices),
                                         "pending_notices": sorted(self.pending_notices)})
        _write(self.dir / "map.json", self.entities)
        _write(self.dir / "memo.json", self.memo)
        _write(self.dir / "inv_text.json", self.inv_text)
        _write(self.dir / "inv_tool.json", self.inv_tool)

    def save_locked(self) -> None:
        with self.lock:
            self.save()

    def audit(self, record: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.time(), **record}, ensure_ascii=False) + "\n")


def state_exists(sid, root=None) -> bool:
    return valid_sid(sid) and (_dir(root or home(), sid) / "state.json").exists()


def get(sid, root=None) -> Session | None:
    root = Path(root or home())
    if not state_exists(sid, root):
        return None
    with _cache_lock:
        key = (str(root), sid)
        if key not in _cache:
            _cache[key] = Session(sid, root)
        return _cache[key]


def activate(sid, depth: str, root=None) -> Session | None:
    if not valid_sid(sid):
        return None
    root = Path(root or home())
    p = _dir(root, sid)
    if not (p / "state.json").exists():
        p.mkdir(parents=True, exist_ok=True)
        _write(p / "state.json", {"depth": "strict" if depth == "strict" else "normal",
                                  "start_index": None, "notices": []})
    return get(sid, root)


def purge(root, days: int) -> int:
    base, cutoff, n = Path(root) / "sessions", time.time() - days * 86400, 0
    if not base.exists():
        return 0
    for d in base.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            with _cache_lock:
                _cache.pop((str(Path(root)), d.name), None)
            n += 1
    return n
