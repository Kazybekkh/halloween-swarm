"""Structured logs: one JSON object per line in logs/run-<ts>.jsonl, plus a short console line."""
import json
import sys
import threading
import time
from pathlib import Path

from swarm.env import ROOT

LOG_DIR = ROOT / "logs"


class Log:
    def __init__(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        self.path: Path = LOG_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self.lock = threading.Lock()

    def _write(self, level: str, event: str, fields: dict) -> None:
        record = {"ts": round(time.time(), 3), "level": level, "event": event, **fields}
        line = json.dumps(record, default=str)
        with self.lock:
            with self.path.open("a") as fh:
                fh.write(line + "\n")
            short = " ".join(f"{k}={v}" for k, v in fields.items() if not isinstance(v, (dict, list)))
            print(f"[{level}] {event} {short}"[:400], file=sys.stderr, flush=True)

    def info(self, event: str, **fields) -> None:
        self._write("info", event, fields)

    def warn(self, event: str, **fields) -> None:
        self._write("warn", event, fields)


log = Log()
