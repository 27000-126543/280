import json
import queue
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Any, Dict, List, Optional


class MemoryStore:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._data: Dict[str, Any] = {}
        self._expiry: Dict[str, datetime] = {}
        self._lists: Dict[str, List[Any]] = defaultdict(list)
        self._sets: Dict[str, set] = defaultdict(set)
        self._hashes: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._events_queue: queue.Queue = queue.Queue(maxsize=100000)

    def get(self, key: str) -> Optional[bytes]:
        value = self._data.get(key)
        if value is None:
            return None
        expiry = self._expiry.get(key)
        if expiry and datetime.now() > expiry:
            del self._data[key]
            del self._expiry[key]
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str).encode("utf-8")
        return str(value).encode("utf-8")

    def set(self, key: str, value: Any):
        self._data[key] = value
        if key in self._expiry:
            del self._expiry[key]

    def setex(self, key: str, expiry: timedelta, value: Any):
        self._data[key] = value
        self._expiry[key] = datetime.now() + expiry

    def delete(self, key: str):
        if key in self._data:
            del self._data[key]
        if key in self._expiry:
            del self._expiry[key]
        if key in self._lists:
            del self._lists[key]
        if key in self._sets:
            del self._sets[key]
        if key in self._hashes:
            del self._hashes[key]

    def exists(self, key: str) -> bool:
        if key in self._data:
            expiry = self._expiry.get(key)
            if expiry and datetime.now() > expiry:
                del self._data[key]
                del self._expiry[key]
                return False
            return True
        return False

    def incr(self, key: str) -> int:
        current = self._data.get(key, 0)
        if isinstance(current, bytes):
            current = int(current.decode() if current else 0)
        current = int(current) if current else 0
        new_val = current + 1
        self._data[key] = new_val
        return new_val

    def incrbyfloat(self, key: str, amount: float) -> float:
        current = self._data.get(key, 0.0)
        if isinstance(current, bytes):
            current = float(current.decode() if current else 0.0)
        current = float(current) if current else 0.0
        new_val = current + amount
        self._data[key] = new_val
        return new_val

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        if key not in self._hashes:
            self._hashes[key] = {}
        current = self._hashes[key].get(field, 0)
        new_val = int(current) + amount
        self._hashes[key][field] = new_val
        return new_val

    def hincrbyfloat(self, key: str, field: str, amount: float) -> float:
        if key not in self._hashes:
            self._hashes[key] = {}
        current = self._hashes[key].get(field, 0.0)
        new_val = float(current) + amount
        self._hashes[key][field] = new_val
        return new_val

    def hgetall(self, key: str) -> Dict:
        return self._hashes.get(key, {}).copy()

    def lpush(self, key: str, *values):
        for value in reversed(values):
            self._lists[key].insert(0, value)

    def rpush(self, key: str, *values):
        for value in values:
            self._lists[key].append(value)

    def lrange(self, key: str, start: int, end: int) -> List:
        lst = self._lists.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start : end + 1]

    def ltrim(self, key: str, start: int, end: int):
        if key in self._lists:
            if end == -1:
                self._lists[key] = self._lists[key][start:]
            else:
                self._lists[key] = self._lists[key][start : end + 1]

    def sadd(self, key: str, *values):
        for value in values:
            self._sets[key].add(value)

    def srem(self, key: str, *values):
        for value in values:
            self._sets[key].discard(value)

    def smembers(self, key: str) -> set:
        return self._sets.get(key, set()).copy()

    def scard(self, key: str) -> int:
        return len(self._sets.get(key, set()))

    def scan_iter(self, match: str = None, count: int = None):
        for key in self._data.keys():
            if match is None or (match and self._key_matches(key, match)):
                yield key.encode("utf-8") if isinstance(key, str) else key

    def _key_matches(self, key: str, pattern: str) -> bool:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return key.startswith(prefix)
        return key == pattern

    def pipeline(self):
        return MemoryPipeline(self)

    def publish_event(self, topic: str, event: Dict):
        self._events_queue.put((topic, event, datetime.now()))

    def get_events(self, timeout: float = 0.1) -> List:
        events = []
        try:
            while not self._events_queue.empty():
                events.append(self._events_queue.get_nowait())
        except queue.Empty:
            pass
        return events


class MemoryPipeline:
    def __init__(self, store: MemoryStore):
        self.store = store
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.execute()

    def incr(self, key: str):
        self.commands.append(("incr", key))
        return self

    def incrbyfloat(self, key: str, amount: float):
        self.commands.append(("incrbyfloat", key, amount))
        return self

    def hincrby(self, key: str, field: str, amount: int = 1):
        self.commands.append(("hincrby", key, field, amount))
        return self

    def hincrbyfloat(self, key: str, field: str, amount: float):
        self.commands.append(("hincrbyfloat", key, field, amount))
        return self

    def execute(self):
        results = []
        for cmd in self.commands:
            if cmd[0] == "incr":
                results.append(self.store.incr(cmd[1]))
            elif cmd[0] == "incrbyfloat":
                results.append(self.store.incrbyfloat(cmd[1], cmd[2]))
            elif cmd[0] == "hincrby":
                results.append(self.store.hincrby(cmd[1], cmd[2], cmd[3]))
            elif cmd[0] == "hincrbyfloat":
                results.append(self.store.hincrbyfloat(cmd[1], cmd[2], cmd[3]))
        self.commands = []
        return results
