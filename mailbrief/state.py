"""État persistant : quels mails ont déjà été traités."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

MAX_KEPT_UIDS = 2000


@dataclass
class State:
    path: Path
    uidvalidity: int = 0
    last_uid: int = 0
    processed: List[int] = field(default_factory=list)
    last_run_ts: float = 0.0
    runs: int = 0

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # État corrompu : on repart proprement plutôt que de planter.
            return cls(path=path)
        return cls(
            path=path,
            uidvalidity=int(raw.get("uidvalidity", 0)),
            last_uid=int(raw.get("last_uid", 0)),
            processed=[int(u) for u in raw.get("processed", [])],
            last_run_ts=float(raw.get("last_run_ts", 0.0)),
            runs=int(raw.get("runs", 0)),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "uidvalidity": self.uidvalidity,
            "last_uid": self.last_uid,
            "processed": self.processed[-MAX_KEPT_UIDS:],
            "last_run_ts": self.last_run_ts,
            "last_run_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(self.last_run_ts)
            ),
            "runs": self.runs,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    def mark(self, uids: List[int]) -> None:
        known = set(self.processed)
        for uid in uids:
            if uid not in known:
                self.processed.append(uid)
                known.add(uid)
        if len(self.processed) > MAX_KEPT_UIDS:
            self.processed = self.processed[-MAX_KEPT_UIDS:]
        if uids:
            self.last_uid = max(self.last_uid, max(uids))

    def is_done(self, uid: int) -> bool:
        return uid in set(self.processed)
